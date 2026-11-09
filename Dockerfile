# MPACT Web
#
# One image serving both the API and the static front end. The scientific
# stack is installed from server/requirements.txt, which pins the same
# versions upstream MPACT pins -- server/mpact_core is that code verbatim, so
# it must run against the libraries it was verified against.

FROM python:3.12-slim AS base

# libgomp: scipy/scikit-learn wheels link against OpenMP at runtime.
# libfreetype6, libfontconfig1: epam.indigo's structure renderer dlopens them.
# libxrender1, libxext6: rdkit's drawing module links against them.
# curl: the container healthcheck below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libfreetype6 libfontconfig1 \
       libxrender1 libxext6 curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg \
    MPACT_DATA_ROOT=/data \
    MPACT_REFERENCE_DIR=/reference \
    MPACT_NPATLAS_PATH=/reference/npatlas.tsv

WORKDIR /app

# Dependencies first, so a source edit doesn't re-resolve the whole stack.
COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

COPY server/ server/
COPY web/ web/
COPY scripts/ scripts/

# Fail the build rather than the deployment if a vendored core file was
# edited in place: the whole design depends on it matching upstream.
RUN python scripts/vendor_core.py verify

# Runtime data (uploads, run outputs) and reference data are volumes, so a
# container restart or image rebuild doesn't discard a user's analyses.
RUN mkdir -p /data /reference
VOLUME ["/data", "/reference"]

# Run as an unprivileged user; it needs write access to the data volumes.
RUN useradd --create-home --uid 10001 mpact \
    && chown -R mpact:mpact /data /reference /app
USER mpact

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

WORKDIR /app/server
# One worker by default: analysis runs already fork their own processes
# (app/jobs.py) and MPACT_MAX_CONCURRENT_JOBS caps how many run at once, so
# extra web workers add memory pressure without adding analysis throughput.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
