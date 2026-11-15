# Sign-in and accounts

Sign-in is off by default. That is fine on your own computer and wrong on
anything with a public address, since uploading files and running analyses
use real CPU and disk. Set `MPACT_AUTH` to one of four modes.

## `none`

No sign-in. Each browser gets its own workspace, identified by a cookie. Use
this on a personal computer, or behind a proxy that does its own
authentication (see [Single sign-on](#single-sign-on)).

## `code`

One shared access code. Everyone who enters it gets their own workspace. This
suits a teaching lab: write the code on the board.

```bash
MPACT_AUTH=code
MPACT_ACCESS_CODE=pick-something-long
```

Several codes can be given, separated by commas (one per class, for example).
Codes are not case-sensitive.

## `users`

Named accounts with passwords. Each account's workspace follows the person to
any browser or computer, and no account can see another's data.

```bash
MPACT_AUTH=users
python scripts/mpact_users.py add alice      # prompts for a password
python scripts/mpact_users.py list
```

Accounts are stored in `<data root>/users.txt` (or `MPACT_USERS_FILE`) as
scrypt hashes.

## `email`

Anyone can sign in with an email address, with no password. Signing in sends a
six-digit code to the address, valid for 15 minutes, with five attempts and a
limit of a few codes per hour. The first sign-in creates the account.

```bash
MPACT_AUTH=email
MPACT_ADMIN_EMAILS=you@example.edu
MPACT_SITE_URL=https://mpact.example.edu
MPACT_SMTP_HOST=smtp.gmail.com          # smtp.office365.com for Microsoft 365
MPACT_SMTP_PORT=587
MPACT_SMTP_USER=you@example.edu
MPACT_SMTP_PASSWORD='app password'
MPACT_MAIL_FROM='MPACT <you@example.edu>'
```

For a Google account the password is an *app password* (Google Account >
Security > 2-Step Verification > App passwords). Microsoft 365 needs SMTP AUTH
enabled for the mailbox. Port 587 is used because most cloud providers block
ports 25 and 465.

### Roles

Every account has a role:

| Role | Who | Access |
|---|---|---|
| internal | Addresses at `MPACT_INTERNAL_DOMAINS` (subdomains included), or on the collaborator list | All demo datasets, including class datasets; no quota |
| external | Everyone else | The public demo and their own work, within `MPACT_EXTERNAL_MAX_RUNS` runs and `MPACT_EXTERNAL_MAX_STORAGE_MB` |
| blocked | Set by an administrator | Cannot sign in |

### Administration

Addresses listed in `MPACT_ADMIN_EMAILS` see an **Accounts** page. It shows
sign-ups by email domain, each account's role and storage, and the
collaborator list (individual addresses or whole `@domain`s treated as
internal). An administrator can override any account's role.

With `MPACT_SIGNUP=approval`, outside addresses wait for an administrator to
approve them, and administrators are emailed when one signs up.

| Variable | Default | Purpose |
|---|---|---|
| `MPACT_INTERNAL_DOMAINS` | `smith.edu` | Domains whose addresses are internal |
| `MPACT_ADMIN_EMAILS` | | Who sees the Accounts page |
| `MPACT_SIGNUP` | `open` | `open`, or `approval` to hold outside sign-ups |
| `MPACT_EXTERNAL_MAX_RUNS` | `10` | Run limit for external accounts |
| `MPACT_EXTERNAL_MAX_STORAGE_MB` | `1024` | Storage limit for external accounts |
| `MPACT_SITE_URL` | | The app's public address, used in emails |
| `MPACT_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD` | port `587` | Outgoing mail server |
| `MPACT_SMTP_SECURITY` | `starttls` | Connection security for SMTP |
| `MPACT_MAIL_FROM`, `MPACT_MAIL_REPLY_TO` | | Sender and reply-to addresses |

## Single sign-on

MPACT is not an identity provider. For institutional single sign-on, put an
authenticating reverse proxy in front of it (Caddy `forward_auth`, oauth2-proxy,
Shibboleth), run MPACT with `MPACT_AUTH=none`, and make sure the proxy is the
only way to reach it.

## The signing key

Login cookies are signed with `MPACT_SECRET_KEY`. If you do not set it, a key
is generated into the data root on first start. Set it explicitly if you run
more than one instance or move to a new host. With `users` and `email`
accounts, each workspace's location is derived from the key, so a different key
means everyone signs in to an empty workspace.
