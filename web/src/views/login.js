/* The sign-in screen.
 *
 * Shown instead of the application when the server says a login is required
 * and this browser does not have one. Three shapes, decided by the server:
 * one box for a shared access code, two for a named account, and, with email
 * accounts, an address and then the code emailed to it.
 *
 * It replaces the whole app rather than overlaying it, because there is
 * nothing behind it to look at: every API call would be refused.
 */

import { api } from '../api.js';
import { clear, el } from '../util.js';

const LAST_EMAIL = 'mpact.login.email';

/** Render the sign-in screen into `host`. Resolves once signed in. */
export function showLogin(host, status) {
  return new Promise((resolve) => {
    const form = status.mode === 'email' ? emailForm(resolve) : passwordForm(status, resolve);
    clear(host).append(el('div', { class: 'login-screen' }, [
      el('div', { class: 'login-card' }, [
        el('div', { class: 'login-brand' }, [
          el('img', { class: 'login-icon', src: '/assets/mpact-icon.png', alt: '' }),
          el('div', {}, [
            el('div', { class: 'login-title', text: 'MPACT' }),
            el('div', { class: 'login-subtitle',
                        text: 'Metabolomics Peak Analysis Computational Tool' }),
          ]),
        ]),
        form.node,
      ]),
    ]));
    form.focus();
  });
}

/** Access code, or username and password. */
function passwordForm(status, resolve) {
  const named = Boolean(status.needs_username);
  const error = el('div', { class: 'notice error hidden' });
  const username = el('input', {
    type: 'text', name: 'username', autocomplete: 'username', placeholder: 'username',
  });
  const password = el('input', {
    type: 'password', name: 'password',
    autocomplete: named ? 'current-password' : 'one-time-code',
    placeholder: named ? 'password' : 'access code',
  });
  const submit = el('button', { class: 'btn primary', type: 'submit', text: 'Sign in' });

  const node = el('form', {
    class: 'login-form',
    on: {
      submit: async (event) => {
        event.preventDefault();
        submit.disabled = true;
        submit.textContent = 'Signing in...';
        error.classList.add('hidden');
        try {
          await api.login(named ? username.value.trim() : '', password.value);
          resolve();
        } catch (problem) {
          error.textContent = problem.message;
          error.classList.remove('hidden');
          password.value = '';
          password.focus();
        } finally {
          submit.disabled = false;
          submit.textContent = 'Sign in';
        }
      },
    },
  }, [
    named ? el('label', { class: 'login-field' }, [el('span', { text: 'Username' }), username]) : null,
    el('label', { class: 'login-field' }, [
      el('span', { text: named ? 'Password' : 'Access code' }), password,
    ]),
    error,
    submit,
  ]);
  return { node, focus: () => (named ? username : password).focus() };
}

/** Email accounts: an address, then the six-digit code sent to it. There is
 *  no password, so there is nothing to forget: a new code is one click. */
function emailForm(resolve) {
  let remembered = '';
  try { remembered = localStorage.getItem(LAST_EMAIL) || ''; } catch { /* storage blocked */ }

  const node = el('div', { class: 'login-form' });
  const error = el('div', { class: 'notice error hidden' });
  const fail = (message) => { error.textContent = message; error.classList.remove('hidden'); };
  const ok = () => error.classList.add('hidden');
  let address = remembered;

  function askForAddress() {
    const email = el('input', {
      type: 'email', name: 'email', autocomplete: 'email', inputMode: 'email',
      placeholder: 'you@institution.edu', value: address, required: true,
    });
    const submit = el('button', { class: 'btn primary', type: 'submit', text: 'Email me a code' });
    const form = el('form', {
      class: 'login-step',
      on: { submit: async (event) => {
        event.preventDefault();
        submit.disabled = true;
        submit.textContent = 'Sending...';
        ok();
        try {
          const body = await api.emailStart(email.value);
          address = body.email;
          try { localStorage.setItem(LAST_EMAIL, address); } catch { /* ignore */ }
          askForCode(body.expires_in);
        } catch (problem) {
          fail(problem.message);
          submit.disabled = false;
          submit.textContent = 'Email me a code';
        }
      } },
    }, [
      el('label', { class: 'login-field' }, [el('span', { text: 'Email address' }), email]),
      error,
      submit,
      el('p', { class: 'login-note',
                text: 'No password: we email you a code each time you sign in. '
                      + 'Signing in for the first time creates your account.' }),
    ]);
    clear(node).append(form);
    email.focus();
  }

  function askForCode(expiresIn = 900) {
    const code = el('input', {
      type: 'text', name: 'code', autocomplete: 'one-time-code', inputMode: 'numeric',
      placeholder: '123 456', maxLength: 9, class: 'login-code', required: true,
    });
    const submit = el('button', { class: 'btn primary', type: 'submit', text: 'Sign in' });
    const resend = el('button', {
      class: 'link-button', type: 'button', text: 'Send a new code',
      on: { click: async () => {
        resend.disabled = true;
        ok();
        try {
          await api.emailStart(address);
          note.textContent = `We sent a new code to ${address}.`;
        } catch (problem) {
          fail(problem.message);
        } finally {
          setTimeout(() => { resend.disabled = false; }, 15000);
        }
      } },
    });
    const note = el('p', { class: 'login-note',
                           text: `We sent a 6-digit code to ${address}. It works for `
                                 + `${Math.round(expiresIn / 60)} minutes. `
                                 + 'Check your spam folder if it does not arrive.' });

    const form = el('form', {
      class: 'login-step',
      on: { submit: async (event) => {
        event.preventDefault();
        submit.disabled = true;
        submit.textContent = 'Signing in...';
        ok();
        try {
          const body = await api.emailVerify(address, code.value);
          if (body.pending) {
            clear(node).append(el('div', { class: 'notice info', text: body.detail }));
            return;
          }
          resolve();
        } catch (problem) {
          fail(problem.message);
          code.value = '';
          code.focus();
          submit.disabled = false;
          submit.textContent = 'Sign in';
        }
      } },
    }, [
      note,
      el('label', { class: 'login-field' }, [el('span', { text: 'Code' }), code]),
      error,
      submit,
      el('div', { class: 'login-links' }, [
        resend,
        el('button', { class: 'link-button', type: 'button', text: 'Use a different address',
                       on: { click: () => { ok(); askForAddress(); } } }),
      ]),
    ]);
    clear(node).append(form);
    code.focus();
  }

  askForAddress();
  return { node, focus: () => node.querySelector('input')?.focus() };
}
