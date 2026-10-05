# deploy.ps1 — install the Ops Dashboard onto CT 109 (or back onto CT 106).
#
# One ssh invocation for the whole deploy.  Two reasons that matters here:
#
#   * There is no SSH key on this machine, so every `ssh` call would stop for a
#     password.  A dozen prompts is a dozen chances to mistype one and leave a
#     half-applied release behind.
#   * One `bash -s` on stdin is atomic in the way that matters: the script runs
#     with `set -e`, so a failure stops before the next step rather than after.
#
# Everything crosses the wire as base64 in a text script.  PowerShell 5.1 pipes
# corrupt binary, and a truncated tarball unpacks into a *partially* updated
# release, which is worse than no update at all.
#
# Rollback: each deploy lands in /opt/dashboard/releases/<stamp>/ and `current`
# is a symlink, so reverting is `ln -sfn` at the previous release plus a restart.
# The exact command is printed at the end.
#
[CmdletBinding()]
param(
    [string]$TargetHost = '192.168.1.200',
    [string]$RemoteUser = 'root',
    [string]$RemoteRoot = '/opt/dashboard',
    # The name of the site *on the host*, without a .conf suffix: Debian's
    # sites-available/ convention is bare names (see `default`), and CT 109
    # already has a site called `dashboard` serving 109:9080.  Using
    # "dashboard.conf" would not replace it -- it would sit beside it, and two
    # server blocks both claiming default_server on the same port is a config
    # nginx refuses to load at all.
    [string]$NginxSiteName = 'dashboard',
    [string]$NginxConf = (Join-Path $PSScriptRoot 'nginx/dashboard'),
    [string]$AppName = 'ops-dashboard',
    [int]$Port = 8001,
    [switch]$SkipNginx,
    [switch]$SkipService,
    [switch]$Preview,
    # apt-get install the python3-venv package if the host lacks ensurepip.
    # Off by default: installing system packages is the owner's call.
    [switch]$InstallDeps
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Encode-Base64([string]$Path) {
    return [Convert]::ToBase64String([IO.File]::ReadAllBytes($Path))
}

# ------------------------------------------------------------------ inputs ----
$srcParent = Join-Path $PSScriptRoot 'src'
$src = Join-Path $srcParent 'dashboard'
$requirements = Join-Path $PSScriptRoot 'requirements.txt'

if (-not (Test-Path $src)) { throw "missing $src" }
if (-not (Test-Path $requirements)) { throw "missing $requirements" }
if (-not $SkipNginx -and -not (Test-Path $NginxConf)) { throw "missing $NginxConf" }

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$release = "$RemoteRoot/releases/$stamp"
$venv = "$RemoteRoot/venv"

Write-Step "target        $RemoteUser@$TargetHost"
Write-Step "release       $release"

# ------------------------------------------------------------------- build ----
$archive = Join-Path ([IO.Path]::GetTempPath()) "${AppName}-${stamp}.tar.gz"
# -C with an absolute path so the archive contents do not depend on the caller's
# working directory.  __pycache__ is excluded: shipping another machine's
# bytecode is noise, and a stale .pyc next to a new .py is a debugging trap.
& tar.exe -czf $archive -C $srcParent --exclude='__pycache__' --exclude='*.pyc' 'dashboard'
if ($LASTEXITCODE -ne 0) { throw 'tar failed' }

$unit = @"
[Unit]
Description=Ops Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
WorkingDirectory=$RemoteRoot/current
Environment=PYTHONPATH=$RemoteRoot/current
Environment=DASHBOARD_BIND=127.0.0.1
Environment=DASHBOARD_PORT=$Port
Environment=SERVICES_CONFIG=/etc/dashboard/services.yaml
Environment=HEADSCALE_URL=http://192.168.1.197:8080
Environment=HEADSCALE_KEY_PATH=/etc/dashboard/secrets/headscale.key
Environment=PVE_URL=https://192.168.1.190:8006
Environment=PVE_TOKEN_PATH=/etc/dashboard/secrets/pve.token
Environment=PVE_TOKEN_ID=dashboard@pve!dashboard
# PVE serves a self-signed certificate, so verification is off for this one
# host; pinning the certificate is round 2.  The token still crosses the wire
# inside TLS, and the role behind it cannot start or stop anything.
Environment=PVE_VERIFY_TLS=false
ExecStart=$venv/bin/python -m dashboard.web.app
Restart=on-failure
RestartSec=3
# Read-only: the app reads the headscale key at request time and writes nothing.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
"@

# ------------------------------------------------------------ remote script ----
# Built as text so it can be inspected before it runs, and so -Preview can print
# it instead of shipping it.
#
# Two quoting rules are load-bearing here and worth stating once:
#
#   * The shell function comes from a *single*-quoted here-string.  In a
#     double-quoted one, `$1` is a PowerShell variable reference that expands to
#     nothing, and the function silently becomes `printf ... "\"` -- which prints
#     a backslash and eats the argument.
#   * PowerShell treats a backtick as an escape inside double-quoted here-strings.
#     A backtick-n inside a bash comment becomes a real newline and breaks the
#     comment.  Keep backticks out of the embedded script.

$script = @'
set -euo pipefail

say() { printf '\n--- %s\n' "$1"; }
'@ + @"

say 'unpack release'
mkdir -p '$RemoteRoot' '$release'
base64 -d > '$release/.payload.tar.gz' <<'PAYLOAD_EOF'
$(Encode-Base64 $archive)
PAYLOAD_EOF
tar -xzf '$release/.payload.tar.gz' -C '$release'
rm -f '$release/.payload.tar.gz'

say 'point current at the new release'
ln -sfn '$release' '$RemoteRoot/current'

say 'requirements'
base64 -d > '$RemoteRoot/requirements.txt' <<'PAYLOAD_EOF'
$(Encode-Base64 $requirements)
PAYLOAD_EOF
"@

if (-not $SkipService) {
    $script += @"

say 'virtualenv'
# Test for ensurepip, not for the venv module.  On Debian "import venv" succeeds
# even when a venv cannot be created -- venv is stdlib, ensurepip is a separate
# apt package (python3.X-venv).  Testing the module that always exists is how the
# first run of this script got as far as a half-built virtualenv before failing.
if [ ! -x '$venv/bin/pip' ]; then
  if ! python3 -c 'import ensurepip' 2>/dev/null; then
    PYVER=`$(python3 -c 'import sys; print("%s.%s" % sys.version_info[:2])')
$(if ($InstallDeps) { @'
    echo 'ensurepip missing -- installing the package (-InstallDeps)'
    apt-get update -qq
    # PYVER is "3.13", so the package is python3.13-venv -- no dash after
    # "python3".  Debian's other names follow that shape: python3.12-venv.
    apt-get install -y "python3${PYVER}-venv" || apt-get install -y python3-venv
    python3 -c 'import ensurepip' 2>/dev/null || {
      echo "ERROR: install did not provide ensurepip." >&2
      echo "       tried: python3${PYVER}-venv, python3-venv" >&2
      exit 1
    }
'@ } else { @'
    echo "ERROR: ensurepip is missing, so no virtualenv can be created." >&2
    echo "       on Debian this is the package python3${PYVER}-venv" >&2
    echo "       fix with one of:" >&2
    echo "         apt-get install -y python3${PYVER}-venv   # exact" >&2
    echo "         apt-get install -y python3-venv            # metapackage" >&2
    echo "       or re-run this deploy with -InstallDeps to do it for you." >&2
    exit 1
'@ })
  fi
  # A previous failed attempt leaves a directory with bin/python but no bin/pip,
  # which the -x test above would keep skipping.  Clear it so the retry is clean.
  rm -rf '$venv'
  python3 -m venv '$venv'
fi
'$venv/bin/pip' install --quiet --disable-pip-version-check --upgrade pip
'$venv/bin/pip' install --quiet --disable-pip-version-check -r '$RemoteRoot/requirements.txt'

say 'systemd unit'
base64 -d > '/etc/systemd/system/$AppName.service' <<'PAYLOAD_EOF'
$([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($unit)))
PAYLOAD_EOF
systemctl daemon-reload
systemctl enable '$AppName'
systemctl restart '$AppName'

say 'service status'
sleep 2
systemctl is-active --quiet '$AppName' || { systemctl --no-pager --full status '$AppName' || true; exit 1; }
echo "$AppName is active"
"@
}

if (-not $SkipNginx) {
    $script += @"

say 'nginx site'
site='/etc/nginx/sites-available/$NginxSiteName'
enabled='/etc/nginx/sites-enabled/$NginxSiteName'

# Land beside the target, compare, then replace.  Overwriting the site that
# currently answers on 109:9080 without keeping a copy would make "what was
# there before" the only route back.
base64 -d > "`$site.incoming" <<'PAYLOAD_EOF'
$(Encode-Base64 $NginxConf)
PAYLOAD_EOF
if [ -e "`$site" ] && cmp -s "`$site" "`$site.incoming"; then
  echo 'site unchanged'
  rm -f "`$site.incoming"
else
  if [ -e "`$site" ]; then
    cp -a "`$site" "`$site.bak-$stamp"
    echo "previous site saved as `$site.bak-$stamp"
  fi
  mv -f "`$site.incoming" "`$site"
fi

# A second server block on 9080 is a hard error once either one claims
# default_server, and nginx resolves it by refusing to load anything.  Ours is
# skipped: it is the one meant to own the port, and on a re-run it is already
# enabled.
conflict=''
for f in /etc/nginx/sites-enabled/*; do
  [ -e "`$f" ] || continue
  [ "`$(basename "`$f")" = '$NginxSiteName' ] && continue
  if grep -qE '^[[:space:]]*listen[[:space:]]+9080([[:space:]]|;)' "`$f" 2>/dev/null; then
    conflict="`$conflict `$f"
  fi
done
if [ -n "`$conflict" ]; then
  echo "ERROR: these sites also listen on 9080:`$conflict" >&2
  echo "       two server blocks cannot both own the port as default_server." >&2
  echo "       nothing was enabled; decide which site owns 9080, then re-run." >&2
  exit 1
fi

ln -sfn "`$site" "`$enabled"

# nginx -t before the reload, and undo the symlink if it fails: a bad file left
# in sites-enabled is invisible until the next restart, when it becomes an
# outage.
if ! nginx -t; then
  rm -f "`$enabled"
  echo 'ERROR: nginx rejected the config; our site is disabled again.' >&2
  echo "       previous version, if any: `$site.bak-$stamp" >&2
  exit 1
fi
systemctl reload nginx
echo 'nginx reloaded'
"@
}

$script += @"

say 'probe from inside the host'
# 127.0.0.1 rather than the LAN IP: this proves the app is up behind nginx without
# depending on anything outside the container.
curl -fsS -m 10 -o /dev/null -w 'GET /api/health -> %{http_code}\n' 'http://127.0.0.1:$Port/api/health'
"@

Remove-Item $archive -Force -ErrorAction SilentlyContinue

if ($Preview) {
    Write-Host ''
    Write-Host '=== remote script (-Preview: nothing was sent) ===' -ForegroundColor Yellow
    # The base64 payloads are omitted; they are just the archive and two files.
    Write-Host (($script -split "`n" | Where-Object { $_ -notmatch '^[\w+/=]{60,}$' }) -join "`n")
    Write-Host '=== end ===' -ForegroundColor Yellow
    return
}

$scriptFile = Join-Path ([IO.Path]::GetTempPath()) "${AppName}-${stamp}.sh"
# UTF-8 *without* a BOM: bash treats a leading BOM as part of the first command.
[IO.File]::WriteAllText($scriptFile, $script, (New-Object Text.UTF8Encoding $false))

try {
    Write-Step 'run remote script (expect one password prompt)'
    # `cmd /c` because PowerShell 5.1 has no `<` input redirect.  ssh reads the
    # password from the console, not from this redirected stdin.
    & cmd.exe /c "ssh.exe -o StrictHostKeyChecking=accept-new ${RemoteUser}@${TargetHost} bash -s < `"$scriptFile`""
    if ($LASTEXITCODE -ne 0) { throw "remote script failed (exit $LASTEXITCODE)" }
} finally {
    Remove-Item $scriptFile -Force -ErrorAction SilentlyContinue
}

Write-Step 'verify from here'
try {
    $health = & curl.exe -s -m 10 "http://${TargetHost}:9080/api/health"
    Write-Host "  http://${TargetHost}:9080/api/health -> $health" -ForegroundColor DarkGray
} catch {
    Write-Warning "could not reach http://${TargetHost}:9080/api/health -- check the service"
}

Write-Host ''
Write-Host 'rollback:' -ForegroundColor Yellow
Write-Host "  list releases:     ssh ${RemoteUser}@${TargetHost} 'ls -1t $RemoteRoot/releases'"
Write-Host "  app:               ssh ${RemoteUser}@${TargetHost} `"ln -sfn $RemoteRoot/releases/<previous> $RemoteRoot/current && systemctl restart $AppName`""
Write-Host "  backups:           ssh ${RemoteUser}@${TargetHost} 'ls -1t /etc/nginx/sites-available/$NginxSiteName.bak-*'"
Write-Host "  nginx:             ssh ${RemoteUser}@${TargetHost} 'cp -a /etc/nginx/sites-available/$NginxSiteName.bak-<stamp> /etc/nginx/sites-available/$NginxSiteName && nginx -t && systemctl reload nginx'"
Write-Host "  stop everything:   ssh ${RemoteUser}@${TargetHost} 'rm -f /etc/nginx/sites-enabled/$NginxSiteName && systemctl disable --now $AppName'"
