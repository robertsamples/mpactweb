"""Analysis job queue.

Each run executes in its own spawned process (see ``runner`` for why). This
module owns the queue, the concurrency cap, and reaping: it never touches the
pipeline itself.

Status lives in the run's ``status.json``, written by the child. The queue is
only the *live* view on top of that, so a status poll still works after a
server restart -- a completed run stays readable, and a run whose process
died with the server is reported as failed rather than hanging on "running"
forever.
"""

import asyncio
import multiprocessing
import os
import time
import traceback
from pathlib import Path

from . import config, storage


def _child_entry(session_id, run_id, log_path, env):
    """Trampoline executed inside the spawned process.

    ``env`` carries the parent's configuration so the child's fresh import of
    ``app.config`` resolves the same directories, regardless of how the server
    was launched.
    """
    os.environ.update(env)
    # app.config was imported (with the inherited environment) as a side
    # effect of resolving this function, so re-resolve it now that the
    # parent's settings are in place -- otherwise the child would look for
    # the run under a different data root than the one that created it.
    from . import config as child_config
    child_config.reload()
    from .runner import child_main
    raise SystemExit(child_main(session_id, run_id, log_path))


def _stop(process, grace=5.0):
    """Terminate an analysis process and wait for it; kill it if it stays.

    Blocking, so call it from a worker thread. The escalation matters on
    POSIX, where ``terminate`` is a SIGTERM a process deep in native code can
    sit on: a cancelled run that kept computing held its concurrency slot's
    worth of CPU while the queue showed it gone.
    """
    try:
        if process.is_alive():
            process.terminate()
            process.join(timeout=grace)
        if process.is_alive():
            process.kill()
            process.join(timeout=grace)
    except Exception:  # noqa: BLE001 - already gone, or never started
        traceback.print_exc()


class AlreadyActive(Exception):
    """The session already has a run queued or running."""

    def __init__(self, run_id):
        super().__init__(run_id)
        self.run_id = run_id


class JobManager:
    """FIFO queue with a cap on concurrently running analysis processes."""

    def __init__(self):
        self._queue = []                 # [(session_id, run_id)] awaiting a slot
        self._running = {}               # run_id -> (process, session_id, started)
        self._stopping = []              # timed-out processes not yet confirmed dead
        self._lock = asyncio.Lock()
        self._pump_task = None
        self._context = multiprocessing.get_context('spawn')

    # --- lifecycle --------------------------------------------------------

    def start(self):
        if self._pump_task is None or self._pump_task.done():
            self._pump_task = asyncio.create_task(self._pump_forever())

    async def shutdown(self):
        if self._pump_task is not None:
            self._pump_task.cancel()
            try:
                await self._pump_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._pump_task = None
        async with self._lock:
            for run_id, (process, _, _) in list(self._running.items()):
                if process.is_alive():
                    process.terminate()
                self._running.pop(run_id, None)

    # --- submission -------------------------------------------------------

    def active_for(self, session_id):
        """The run this session has queued or running, if any.

        One at a time per workspace: on a shared server a second Run click
        would otherwise queue a duplicate behind everyone else's analyses.
        """
        for run_id, (_, owner, _) in self._running.items():
            if owner != session_id:
                continue
            # The child writes its final status just before it exits, and the
            # pump reaps it up to a second later; a finished run must not
            # block the next one in between.
            status = storage.Run(storage.Session(owner), run_id).read_status() or {}
            if status.get('state') in ('complete', 'failed', 'cancelled'):
                continue
            return run_id
        for owner, run_id in self._queue:
            if owner == session_id:
                return run_id
        return None

    async def submit(self, session, run):
        # Checked under the lock as well as by the caller: two clicks can both
        # pass the caller's check before either reaches the queue.
        async with self._lock:
            active = self.active_for(session.id)
            if active is not None:
                raise AlreadyActive(active)
            # Before the job is visible to the scheduler, not after: the
            # background pass can start it the moment it is queued, and a
            # "queued" written after the analysis process's own "running"
            # left the run reading as queued for its whole life.
            run.write_status({
                'run_id': run.id,
                'state': 'queued',
                'stage': 'queued',
                'started_at': None,
                'finished_at': None,
                'error': None,
                'warnings': [],
            })
            self._queue.append((session.id, run.id))
        self.start()
        await self._pump()
        return run.id

    async def cancel(self, session, run):
        """Stop a queued or running job. A completed run is left alone."""
        async with self._lock:
            self._queue = [item for item in self._queue if item[1] != run.id]
            entry = self._running.pop(run.id, None)
        if entry is not None:
            # Off the event loop: waiting for the process to go can take
            # seconds, and every other request would wait with it.
            await asyncio.to_thread(_stop, entry[0])
        status = run.read_status() or {'run_id': run.id}
        if status.get('state') in ('queued', 'running'):
            status.update(state='cancelled', stage='cancelled', finished_at=time.time())
            run.write_status(status)
        await self._pump()
        return status

    # --- scheduling -------------------------------------------------------

    async def _pump_forever(self):
        while True:
            await asyncio.sleep(1.0)
            try:
                await self._pump()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad pass must not end scheduling
                # Without this a single failed status write (a full disk, a
                # session deleted under a running job) ended the loop, and
                # nothing was reaped or started again until the next submit.
                traceback.print_exc()

    async def _pump(self):
        """Reap finished processes, then start as many queued jobs as fit."""
        async with self._lock:
            for run_id, (process, session_id, _) in list(self._running.items()):
                if process.is_alive():
                    continue
                self._running.pop(run_id, None)
                try:
                    self._reconcile_exit(session_id, run_id, process.exitcode)
                except Exception:  # noqa: BLE001 - the run directory may be gone
                    traceback.print_exc()

            while self._queue and len(self._running) < config.MAX_CONCURRENT_JOBS:
                session_id, run_id = self._queue.pop(0)
                try:
                    self._spawn(session_id, run_id)
                except Exception as exc:  # noqa: BLE001
                    # The job has left the queue, so nothing would ever report
                    # on it again: say why it never started.
                    traceback.print_exc()
                    self._fail(session_id, run_id, 'failed',
                               f'the analysis process could not be started: {exc}')

            # Time out a job that has overrun its budget.
            now = time.time()
            for run_id, (process, session_id, started) in list(self._running.items()):
                if now - started > config.JOB_TIMEOUT_SECONDS and process.is_alive():
                    process.terminate()
                    self._running.pop(run_id, None)
                    self._stopping.append(process)
                    self._fail(session_id, run_id, 'timed out',
                               f'exceeded {config.JOB_TIMEOUT_SECONDS}s time limit')

            # A terminated process that is still there a pass later did not
            # take the hint.
            for process in list(self._stopping):
                if process.is_alive():
                    process.kill()
                else:
                    self._stopping.remove(process)

    def _fail(self, session_id, run_id, stage, error):
        """Record a terminal failure the analysis process could not write."""
        try:
            run = storage.Run(storage.Session(session_id), run_id)
            status = run.read_status() or {'run_id': run_id}
            status.update(state='failed', stage=stage, error=error, finished_at=time.time())
            run.write_status(status)
        except Exception:  # noqa: BLE001 - e.g. the run was deleted meanwhile
            traceback.print_exc()

    def _spawn(self, session_id, run_id):
        session = storage.Session(session_id)
        run = storage.Run(session, run_id)
        env = {
            'MPACT_DATA_ROOT': str(config.DATA_ROOT),
            'MPACT_REFERENCE_DIR': str(config.REFERENCE_DIR),
            'MPACT_NPATLAS_PATH': str(config.NPATLAS_PATH),
            'MPACT_PVCLUST_NBOOT': str(config.PVCLUST_NBOOT),
            'MPLBACKEND': 'Agg',
        }
        process = self._context.Process(
            target=_child_entry,
            args=(session_id, run_id, str(run.log_path), env),
            name=f'mpact-run-{run_id}',
            daemon=False,   # pvclust starts its own pool; a daemon can't have children
        )
        process.start()
        self._running[run_id] = (process, session_id, time.time())

    def _reconcile_exit(self, session_id, run_id, exitcode):
        """Make sure a dead process's run isn't left claiming to be running.

        The child writes its own terminal status; this only covers the cases
        where it *couldn't* (segfault, OOM kill, terminate).
        """
        run = storage.Run(storage.Session(session_id), run_id)
        status = run.read_status() or {'run_id': run_id}
        if status.get('state') in ('queued', 'running'):
            status.update(
                state='failed',
                stage='failed',
                error=(f'analysis process exited with code {exitcode} '
                       '(see the run log for details)'),
                finished_at=time.time())
            run.write_status(status)

    # --- inspection -------------------------------------------------------

    def live_state(self, run_id):
        """'running', 'queued', or None if this process isn't tracking it."""
        if run_id in self._running:
            return 'running'
        if any(item[1] == run_id for item in self._queue):
            return 'queued'
        return None

    def status_for(self, run):
        """The run's status, reconciled against what this process knows.

        A ``running`` status with no live process means the job died with a
        previous server; report that rather than leaving the browser polling
        an unreachable job forever.
        """
        status = run.read_status()
        if status is None:
            return None
        live = self.live_state(run.id)
        if status.get('state') in ('queued', 'running') and live is None:
            status = dict(status)
            status.update(state='failed', stage='interrupted',
                          error='analysis was interrupted (server restarted)')
        elif live == 'queued':
            position = [item[1] for item in self._queue].index(run.id) + 1
            status = dict(status)
            status['stage'] = f'queued (position {position})'
        return status


manager = JobManager()
