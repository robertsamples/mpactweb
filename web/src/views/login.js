/* The sign-in screen.
 *
 * Shown instead of the application when the server says a login is required
 * and this browser does not have one. Two shapes, decided by the server: one
 * box for a shared access code, two for a named account.
 *
 * It replaces the whole app rather than overlaying it, because there is
 * nothing behind it to look at: every API call would be refused.
 */

import { api } from '../api.js';
import { clear, el } from '../util.js';

/** Render the sign-in screen into `host`. Resolves once signed in. */
export function showLogin(host, status) {
  return new Promise((resolve) => {
    const named = Boolean(status.needs_username);
    const error = el('div', { class: 'notice error hidden' });
    const username = el('input', {
      type: 'text', name: 'username', autocomplete: 'username',
      placeholder: 'username', autofocus: named,
    });
    const password = el('input', {
      type: 'password', name: 'password',
      autocomplete: named ? 'current-password' : 'one-time-code',
      placeholder: named ? 'password' : 'access code', autofocus: !named,
    });
    const submit = el('button', { class: 'btn primary', type: 'submit',
                                  text: 'Sign in' });

    const form = el('form', {
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
      named ? el('label', { class: 'login-field' }, [
        el('span', { text: 'Username' }), username,
      ]) : null,
      el('label', { class: 'login-field' }, [
        el('span', { text: named ? 'Password' : 'Access code' }), password,
      ]),
      error,
      submit,
    ]);

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
        form,
      ]),
    ]));

    (named ? username : password).focus();
  });
}
