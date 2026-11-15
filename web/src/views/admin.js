/* The Admin page: who has signed up, from where (by email domain), and what
 * each account is -- internal (sees the class datasets, no quota), external,
 * or blocked. Shown only to the addresses in MPACT_ADMIN_EMAILS. */

import { api } from '../api.js';
import { bytes, clear, el, timeAgo, toast } from '../util.js';

const ROLE_LABEL = { internal: 'Internal', external: 'External', blocked: 'Blocked' };

export function buildAdminView() {
  const node = el('div', { class: 'admin-view' });
  let filter = '';
  let data = null;

  async function load() {
    try {
      data = await api.adminUsers();
    } catch (error) {
      clear(node).append(el('div', { class: 'notice error', text: error.message }));
      return;
    }
    render();
  }

  async function change(email, patch) {
    try {
      await api.adminUpdate({ email, ...patch });
      await load();
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  function summary() {
    const users = data.users;
    const now = Date.now() / 1000;
    const count = (test) => users.filter(test).length;
    const stat = (label, value) => el('div', { class: 'stat' }, [
      el('div', { class: 'value', text: String(value) }), el('div', { class: 'label', text: label })]);
    return el('div', { class: 'panel' }, [
      el('h3', { text: 'Overview' }),
      el('div', { class: 'stat-row' }, [
        stat('Accounts', users.length),
        stat('Internal', count((u) => u.role === 'internal')),
        stat('External', count((u) => u.role === 'external')),
        stat('Waiting for approval', count((u) => u.status === 'pending')),
        stat('Signed up, last 7 days', count((u) => now - u.created < 7 * 86400)),
        stat('Active, last 7 days', count((u) => u.last_seen && now - u.last_seen < 7 * 86400)),
      ]),
      el('p', { class: 'muted' }, [
        `Sign-up is ${data.signup === 'approval' ? 'by approval for outside addresses' : 'open'}. `,
        `Internal domains: ${data.internal_domains.join(', ') || 'none'}. `,
        'External quota: ',
        data.quota.external_runs ? `${data.quota.external_runs} analyses` : 'no run limit',
        ', ',
        data.quota.external_storage_mb ? `${data.quota.external_storage_mb.toLocaleString()} MB` : 'no storage limit',
        '. ',
        data.mail ? '' : el('strong', { text: 'Email is not set up: nobody can sign in.' }),
      ]),
    ]);
  }

  function domains() {
    const body = el('tbody', {}, data.domains.map((row) => el('tr', {}, [
      el('td', { text: row.domain }),
      el('td', { class: 'num', text: String(row.count) }),
      el('td', { text: ROLE_LABEL[row.role] || row.role }),
    ])));
    return el('div', { class: 'panel' }, [
      el('h3', {}, [el('span', { text: 'Sign-ups by domain' }),
                    el('span', { class: 'hint', text: 'all that is recorded about where people are from' })]),
      data.domains.length
        ? el('table', { class: 'data' }, [
          el('thead', {}, [el('tr', {}, [el('th', { text: 'Domain' }), el('th', { class: 'num', text: 'Accounts' }),
                                         el('th', { text: 'By default' })])]),
          body])
        : el('p', { class: 'muted', text: 'Nobody has signed up yet.' }),
    ]);
  }

  function users() {
    const shown = data.users.filter((u) => !filter || u.email.includes(filter));
    const roleSelect = (user) => {
      const select = el('select', {
        title: 'Automatic follows the address: internal domains and the allowlist are internal',
        on: { change: (event) => {
          const value = event.target.value;
          if (value === 'auto') change(user.email, { reset_role: true });
          else change(user.email, { role: value });
        } },
      }, [
        el('option', { value: 'auto', text: `Automatic (${ROLE_LABEL[user.automatic_role]})`,
                       selected: !user.role_override }),
        ...['internal', 'external', 'blocked'].map((role) => el('option', {
          value: role, text: ROLE_LABEL[role], selected: user.role_override === role })),
      ]);
      if (user.admin) select.disabled = true;
      return select;
    };
    const rows = shown.map((user) => el('tr', { class: user.role === 'blocked' ? 'muted' : '' }, [
      el('td', {}, [el('span', { text: user.email }),
                    user.admin ? el('span', { class: 'pill', text: 'admin' }) : null]),
      el('td', {}, [roleSelect(user)]),
      el('td', {}, [user.status === 'pending'
        ? el('button', { class: 'btn small primary', type: 'button', text: 'Approve',
                         on: { click: () => change(user.email, { approve: true }) } })
        : el('span', { class: 'muted', text: 'active' })]),
      el('td', { text: new Date(user.created * 1000).toLocaleDateString() }),
      el('td', { text: user.last_seen ? timeAgo(user.last_seen) : '' }),
      el('td', { class: 'num', text: String(user.runs) }),
      el('td', { class: 'num', text: bytes(user.storage_bytes) }),
    ]));
    const search = el('input', {
      type: 'search', placeholder: 'find an address', value: filter,
      on: { input: (event) => { filter = event.target.value.trim().toLowerCase(); render(); } },
    });
    return el('div', { class: 'panel' }, [
      el('h3', {}, [el('span', { text: 'Accounts' }), search]),
      el('div', { class: 'table-wrap' }, [el('table', { class: 'data admin-users' }, [
        el('thead', {}, [el('tr', {}, ['Address', 'Role', 'Status', 'Signed up', 'Last active',
                                       'Analyses', 'Storage']
          .map((label, i) => el('th', { class: i >= 5 ? 'num' : '', text: label })))]),
        el('tbody', {}, rows.length ? rows
          : [el('tr', {}, [el('td', { colSpan: 7, class: 'muted', text: 'No accounts match.' })])]),
      ])]),
    ]);
  }

  function allowlist() {
    const input = el('input', { type: 'text', placeholder: 'colleague@umass.edu  or  @umass.edu' });
    const add = async (event) => {
      event.preventDefault();
      if (!input.value.trim()) return;
      try {
        await api.adminAllow(input.value.trim());
        await load();
      } catch (error) { toast(error.message, 'error'); }
    };
    return el('div', { class: 'panel' }, [
      el('h3', {}, [el('span', { text: 'Collaborators' }),
                    el('span', { class: 'hint', text: 'addresses or whole domains treated as internal, before or after they sign up' })]),
      el('div', { class: 'allowlist' }, data.allowlist.length
        ? data.allowlist.map((entry) => el('span', { class: 'pill allow-entry' }, [
          el('span', { text: entry }),
          el('button', { type: 'button', text: '×', title: 'Remove',
                         on: { click: async () => {
                           try { await api.adminDisallow(entry); await load(); } catch (error) { toast(error.message, 'error'); }
                         } } }),
        ]))
        : [el('span', { class: 'muted', text: 'None yet.' })]),
      el('form', { class: 'btn-row', style: { marginTop: '10px' }, on: { submit: add } }, [
        input, el('button', { class: 'btn small', type: 'submit', text: 'Add' }),
      ]),
    ]);
  }

  function render() {
    const focused = document.activeElement && node.contains(document.activeElement)
      && document.activeElement.type === 'search';
    clear(node).append(el('h2', { text: 'Admin' }), summary(), users(), domains(), allowlist());
    if (focused) {
      const search = node.querySelector('input[type=search]');
      search.focus();
      search.setSelectionRange(search.value.length, search.value.length);
    }
  }

  return { id: 'admin', label: 'Accounts', group: 'Admin', node, onShow: load };
}
