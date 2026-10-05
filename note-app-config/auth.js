const fs = require('fs');
const crypto = require('crypto');
const jwt = require('jsonwebtoken');

// This file reads its own credentials file instead of trusting process.env, and
// that is deliberate. The compose file used to carry `AUTH_PASS=...` as a
// literal in its `environment:` list, and a literal always wins over any
// `${VAR}` substitution -- so a .env sitting next to it had no effect at all,
// and the operator saw a login rejection that looked exactly like a wrong
// password. Reading the file here makes the source of truth independent of how
// the compose file happens to be written.
//
// The old code fell back to a hardcoded constant when JWT_SECRET was unset.
// That is worse than having no default: a box that forgot to configure it did
// not fail, it signed 30-day `admin` sessions with a string printed in the
// source, so anyone who could read the code could mint a valid token. There is
// no constant now. A secret that is missing from every source is generated once
// and persisted, never silently shared.
//
// What this must NOT do is crash. An earlier revision of this file threw when a
// variable was missing; on a box whose compose file supplied no JWT_SECRET that
// turned a misconfiguration into a restart loop and took the app down. Refusing
// to start is the right call for a service that only runs on a build machine --
// it is the wrong call for one holding the only copy of someone's notes.
const ENV_FILE = process.env.ENV_FILE || '/app/credentials.env';

function fromFile(name) {
  let text;
  try {
    text = fs.readFileSync(ENV_FILE, 'utf8');
  } catch (err) {
    // A missing file is a normal first run, not something to shout about.
    // Anything else (permissions, a directory in the way) is worth naming,
    // because the symptom would otherwise be "the password changed by itself".
    if (err.code !== 'ENOENT') {
      console.error(`[auth] could not read ${ENV_FILE}: ${err.message}`);
    }
    return undefined;
  }
  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) continue;
    if (trimmed.slice(0, eq).trim() === name) return trimmed.slice(eq + 1).trim();
  }
  return undefined;
}

function persist(name, value) {
  try {
    fs.appendFileSync(ENV_FILE, `${name}=${value}\n`, { mode: 0o600 });
    return true;
  } catch (err) {
    console.error(`[auth] could not persist ${name} to ${ENV_FILE}: ${err.message}`);
    return false;
  }
}

function resolve(name, { generate, onMissing }) {
  const value = process.env[name] || fromFile(name);
  if (value) return value;
  const fresh = generate();
  onMissing(fresh);
  return fresh;
}

const JWT_SECRET = resolve('JWT_SECRET', {
  generate: () => crypto.randomBytes(32).toString('hex'),
  onMissing: (fresh) => {
    if (persist('JWT_SECRET', fresh)) {
      console.warn(
        `[auth] JWT_SECRET was unset; generated one and saved it to ${ENV_FILE}. ` +
          'Sessions survive restarts only while that file does.'
      );
    } else {
      console.error(
        '[auth] JWT_SECRET is unset and could not be saved -- every restart ' +
          'will invalidate all sessions.'
      );
    }
  },
});

const AUTH_USER = resolve('AUTH_USER', {
  generate: () => 'admin',
  onMissing: () => console.warn('[auth] AUTH_USER was unset; using "admin".'),
});

const AUTH_PASS = resolve('AUTH_PASS', {
  generate: () => crypto.randomBytes(12).toString('base64url'),
  onMissing: (fresh) => {
    // Printed on purpose: a password nobody can read is a lockout, not security.
    console.warn(`[auth] AUTH_PASS was unset; generated one for this run: ${fresh}`);
    persist('AUTH_PASS', fresh);
  },
});

function generateToken() {
  return jwt.sign({ user: AUTH_USER }, JWT_SECRET, { expiresIn: '30d' });
}

function authMiddleware(req, res, next) {
  const authHeader = req.headers.authorization;

  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'Unauthorized' });
  }

  const token = authHeader.split(' ')[1];
  try {
    const decoded = jwt.verify(token, JWT_SECRET);
    req.user = decoded.user;
    next();
  } catch (err) {
    return res.status(401).json({ error: 'Invalid token' });
  }
}

module.exports = { generateToken, authMiddleware, JWT_SECRET, AUTH_USER, AUTH_PASS };
