# note-app-config

The four files that carry the note-app credential fix, kept here because the
application itself is deliberately **not** in version control.

## Why note-app is not a repository

Its runtime data is not source. `server/data/` holds the live SQLite database,
the uploaded images, and -- in `server/data/keep/*.json` -- notes that quote real
credentials for other systems (a Windows Server login, a SQL password, a WiFi
PSK, a VPN account). None of that can be pushed anywhere, and the application
tree is not worth a repository on its own. So the project stays local, and the
part that was actually designed -- the credential handling and the deploy -- is
copied here.

## What is in here

| file | goes to | what it is |
|---|---|---|
| `auth.js` | `/opt/note-app/server/src/middleware/auth.js` | reads credentials from a file, no hardcoded fallback |
| `docker-compose.yml` | `/opt/note-app/docker-compose.yml` | binds `credentials.env` in; no secrets in `environment:` |
| `.env.example` | template for `credentials.env` on the box | the three variables, with no real values |
| `deploy.ps1` | run from the workstation | packages, uploads, rebuilds, verifies, can roll back |

## The two faults these fix

**1. A hardcoded fallback secret.** `auth.js` used to be:

```js
const JWT_SECRET = process.env.JWT_SECRET || '<a constant committed to the repo>';
const AUTH_PASS  = process.env.AUTH_PASS  || '<the real password, in plaintext>';
```

A missing variable did not fail, it signed 30-day `admin` sessions with a string
printed in the source, so anyone who had read the code could mint a token. And
the compose file carried `AUTH_PASS` as a **literal** in its `environment:`
block -- a literal always beats `${VAR}`, so a `credentials.env` sitting beside
it had no effect at all and a login rejection looked exactly like a wrong
password.

Now `auth.js` reads `/app/credentials.env` itself, the compose file mounts it
read-only, and nothing credential-shaped appears in `environment:`.

**2. A restart loop.** An intermediate revision of `auth.js` *threw* when a
variable was missing. On a box whose compose file supplied no `JWT_SECRET`, that
turned a misconfiguration into a restart loop and took the app down -- the wrong
failure mode for a service holding the only copy of someone's notes. The
current version generates a secret and persists it instead, so it cannot fail
closed.

## Deploying

```powershell
cd F:\OpenCode\CT105\note-app
.\deploy.ps1 -DryRun     # package, upload, push, snapshot, extract; no rebuild
.\deploy.ps1             # the same, then build + restart + verify
.\deploy.ps1 -Rollback   # restore the previous source tree and rebuild
```

The script never packages or deletes `server/data/`, `backups/`, `credentials.env`
or `nginx/.htpasswd`, and it aborts if any of them ends up in the tarball.

Two transport details are load-bearing, both learned the hard way:

* `scp` lands on the Proxmox **host**, so `pct push` moves the tarball into the
  container before anything there can read it.
* The remote script is written to a file and run with both streams redirected
  into a log on the far side. PowerShell 5.1 does not honour `2>$null` for a
  native command, and docker writes its build progress to stderr -- so reading
  the ssh stream directly let a healthy build terminate the deploy.

## Before the first deploy on a fresh box

```bash
cd /opt/note-app
umask 077
{
  echo "AUTH_USER=admin"
  echo "AUTH_PASS=<the password you want>"
  echo "JWT_SECRET=$(openssl rand -hex 32)"
} > credentials.env
chmod 600 credentials.env
```

The deploy refuses to run without it (exit 6) rather than guessing a password.
