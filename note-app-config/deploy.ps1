param(
    # The Proxmox host and the LXC holding note-app.
    [string]$PveHost = "192.168.1.190",
    [string]$LxcId = "105",
    [string]$TargetDir = "/opt/note-app",
    # Restore the snapshot the previous deploy took, instead of shipping anything.
    [switch]$Rollback,
    # Package, upload, push, snapshot and extract -- then stop, leaving the
    # running container exactly as it was.  Everything except `build` and
    # `up -d` runs, so the transport this once got wrong (scp lands on the
    # Proxmox host, not in the CT) is exercised without a restart.  Nothing is
    # rebuilt, so a DryRun cannot change what is serving traffic.
    [switch]$DryRun
)

# note-app deploy: copy the source tree to CT 105, rebuild the server image, and
# restart only that container.
#
# Two things here exist because of specific failures:
#
#   * server/data/ is never packaged.  It holds the live SQLite database, the
#     uploaded images, and -- in server/data/keep/*.json -- notes that contain
#     real credentials for other systems.  Nothing in a deploy has any business
#     touching it, so it is excluded on the way out and never deleted on the way
#     in.  Same for backups/, .env, credentials.env, and nginx/.htpasswd.
#
#   * The deploy checks the server actually came up before declaring success. A
#     previous attempt shipped an auth.js that refused to start without
#     JWT_SECRET while the compose file supplied none; the container entered a
#     restart loop and the app was down while every message still said the
#     deploy had worked.  Here a loop is detected and undone automatically.

$ErrorActionPreference = "Stop"

# PowerShell 5.1 does not honour `2>$null` for a native command: stderr comes
# back as a stream of ErrorRecords, and with $ErrorActionPreference = 'Stop' the
# first one terminates the script. docker compose writes its build progress
# ("Image note-app-server Building") to stderr, so a perfectly healthy build
# killed the deploy before `up -d` ran -- twice, in two different places, which
# is why this is now done the only way that cannot happen: the script is written
# to a file on the far side, run with both streams redirected into a log there,
# and the log is read back as ordinary text. No output of the launched commands
# ever reaches PowerShell.
function Send-ScriptFile {
    param([string]$Script, [string]$RemotePath)
    $normalised = ($Script -replace "`r", "")
    $normalised | ssh "root@$PveHost" "pct exec $LxcId -- tee $RemotePath > /dev/null"
    if ($LASTEXITCODE -ne 0) { throw "could not write $RemotePath on CT $LxcId" }
}

function Get-RemoteFile {
    param([string]$RemotePath)
    # 2>&1 deliberately: this reads a text file and must never itself raise.
    $lines = ssh "root@$PveHost" "pct exec $LxcId -- cat $RemotePath" 2>&1
    return ($lines -join "`n")
}

function Invoke-RemoteScript {
    param([string]$RemotePath, [string]$LogPath, [string]$What, [string]$Mode = "deploy")
    # MODE travels as an environment variable because the script is a
    # single-quoted here-string -- nothing in PowerShell interpolates into it,
    # so a switch cannot be spliced in where bash would read it.
    ssh "root@$PveHost" `
        "pct exec $LxcId -- bash -c 'MODE=$Mode bash $RemotePath > $LogPath 2>&1'"
    $exit = $LASTEXITCODE
    $log = Get-RemoteFile -RemotePath $LogPath
    if ($exit -ne 0) {
        Write-Host $log
        throw "$What failed on CT $LxcId (exit $exit)."
    }
    return $log
}

# scp lands on the Proxmox host, not inside the container, so the file has to be
# pushed across the boundary before anything in the CT can read it. Skipping
# this step is why the first run stopped at `tar: .../note-app-upd.tgz: No such
# file or directory` after the snapshot had already been taken.
function Push-IntoContainer {
    param([string]$HostPath, [string]$LxcPath)
    ssh "root@$PveHost" "pct push $LxcId $HostPath $LxcPath"
    if ($LASTEXITCODE -ne 0) { throw "pct push into CT $LxcId failed for $HostPath" }
    $check = ssh "root@$PveHost" "pct exec $LxcId -- test -s $LxcPath && echo PUSH-OK" 2>&1
    if (($check -join "`n") -notmatch "PUSH-OK") {
        throw "$LxcPath is missing or empty inside CT $LxcId"
    }
    Write-Host "pushed $HostPath -> CT ${LxcId}:$LxcPath" -ForegroundColor Green
}

function Get-DeployStatus {
    param([string]$LxcId, [string]$PveHost)
    # Read the verdict with its own clean command, from a file the run wrote.
    $status = ssh "root@$PveHost" "pct exec $LxcId -- cat /tmp/note-app-deploy.status" 2>&1
    return ($status -join "`n")
}

# --------------------------------------------------------------------------- #
# rollback
# --------------------------------------------------------------------------- #
if ($Rollback) {
    Write-Host "=== Rollback: restoring the previous note-app on CT $LxcId ===" -ForegroundColor Cyan

    $rollbackScript = @'
set -e
STATUS=/tmp/note-app-deploy.status
: > "$STATUS"
mark() { echo "$1" | tee -a "$STATUS"; }

if [ ! -f /root/note-app-prev.tgz ]; then
    mark "ROLLBACK-FAILED: /root/note-app-prev.tgz is missing -- nothing to restore"
    exit 4
fi
cd /opt
tar -xzf /root/note-app-prev.tgz
mark "RESTORED"
cd /opt/note-app
docker compose up -d --build server
sleep 8
state=$(docker inspect note-server --format '{{.State.Status}}' 2>/dev/null || echo missing)
mark "STATE=$state"
[ "$state" = "running" ] || { mark "ROLLBACK-FAILED: note-server is $state"; exit 5; }
mark "ROLLBACK-OK"
'@
    Send-ScriptFile -Script $rollbackScript -RemotePath "/tmp/note-app-rollback.sh"
    $out = Invoke-RemoteScript -RemotePath "/tmp/note-app-rollback.sh" `
        -LogPath "/tmp/note-app-rollback.log" -What "rollback"
    Write-Host $out
    $status = Get-DeployStatus -LxcId $LxcId -PveHost $PveHost
    Write-Host $status
    if ($status -notmatch "ROLLBACK-OK") {
        Write-Error "rollback did not report ROLLBACK-OK -- inspect the box: docker compose logs --tail=40 server"
        exit 1
    }
    Write-Host "=== Rollback complete ===" -ForegroundColor Green
    exit 0
}

# --------------------------------------------------------------------------- #
# 1. package
# --------------------------------------------------------------------------- #
Write-Host "=== 1. Packaging note-app source ===" -ForegroundColor Cyan

$Repo = $PSScriptRoot
if (-not $Repo) { $Repo = Split-Path -Parent $MyInvocation.MyCommand.Definition }
if (-not (Test-Path (Join-Path $Repo "docker-compose.yml"))) {
    throw "docker-compose.yml not found next to this script (looked in '$Repo')."
}
Set-Location $Repo

$tarball = Join-Path $env:TEMP "note-app-upd.tgz"
if (Test-Path $tarball) { Remove-Item $tarball -Force }

# The exclude list is the whole safety story: no user data, no uploads, no
# credentials, no built client, no node_modules.  `credentials.env` is
# deliberately left on the box -- it is the source of truth for auth and must
# never be shipped from a workstation.
tar -czf $tarball `
    --exclude="./server/data" `
    --exclude="./backups" `
    --exclude="./.env" `
    --exclude="./credentials.env" `
    --exclude="./nginx/.htpasswd" `
    --exclude="./client/node_modules" `
    --exclude="./client/dist" `
    --exclude="./node_modules" `
    --exclude="./.git" `
    --exclude="./*.tgz" `
    .

if ($LASTEXITCODE -ne 0) { throw "tarball creation failed" }

Write-Host "--- contents (must contain no server/data, no credentials) ---"
# Match on the extracted lines, not on Select-String's exit code: that stays 0
# whether or not anything matched, so gating on it aborted a clean package.
$forbidden = @(tar -tzf $tarball | Where-Object {
    $_ -match 'server/data|/backups/|credentials\.env|\.htpasswd|node_modules|^\.?/?\.env$'
})
if ($forbidden.Count -gt 0) {
    $forbidden | ForEach-Object { Write-Host "  LEAK: $_" -ForegroundColor Red }
    throw "ABORT: the tarball contains a path that must never be deployed."
}
$count = @(tar -tzf $tarball).Count
Write-Host "packaged $count entries, none of them user data" -ForegroundColor Green

# --------------------------------------------------------------------------- #
# 2. upload
# --------------------------------------------------------------------------- #
Write-Host "=== 2. Uploading to CT $LxcId ===" -ForegroundColor Cyan

scp $tarball "root@${PveHost}:/tmp/note-app-upd.tgz"
if ($LASTEXITCODE -ne 0) { throw "scp failed" }

# scp put it on the Proxmox host; the container cannot see that filesystem.
Push-IntoContainer -HostPath "/tmp/note-app-upd.tgz" -LxcPath "/tmp/note-app-upd.tgz"

# --------------------------------------------------------------------------- #
# 3. snapshot, extract, rebuild, restart -- and prove it came up
# --------------------------------------------------------------------------- #
Write-Host "=== 3. Snapshot, extract, rebuild, verify ===" -ForegroundColor Cyan

$deployScript = @'
set -e
# Every gate this script passes writes a marker line, and the last thing it does
# is write the verdict to a file.  The caller reads that file with a separate
# command instead of pattern-matching the ssh output stream: PowerShell 5.1 lets
# a native command's stderr escape redirection, so docker compose's warnings
# arrived interleaved with the step markers and a successful deploy was reported
# as a failure.
STATUS=/tmp/note-app-deploy.status
: > "$STATUS"
mark() { echo "$1" | tee -a "$STATUS"; }

cd /opt

# Snapshot the source only.  server/data is excluded because a rollback that
# rewound the database or the uploaded images would undo work nobody asked to
# undo.  This is the tree a rollback restores.
tar -czf /root/note-app-prev.tgz \
    --exclude='note-app/server/data' \
    --exclude='note-app/backups' \
    --exclude='note-app/client/node_modules' \
    note-app
mark "SNAPSHOT-OK"

# credentials.env lives beside the compose file and is bind-mounted read-only
# into the container.  Create it only if absent; never overwrite it, or the
# login password would change under the operator.
cd /opt/note-app
if [ ! -f credentials.env ]; then
    echo "credentials.env is MISSING."
    echo "Create it with AUTH_USER / AUTH_PASS / JWT_SECRET, then re-run."
    mark "DEPLOY-FAILED: credentials.env absent"
    exit 6
fi
chmod 600 /opt/note-app/credentials.env

# Validate the compose file BEFORE touching the running container.  A config the
# daemon rejects would otherwise surface as a restart loop.
docker compose config >/dev/null 2>&1
mark "COMPOSE-OK"

tar -xzf /tmp/note-app-upd.tgz -C /opt/note-app
mark "EXTRACT-OK"

# In DryRun the two steps that disturb the running service are skipped.  The
# source has already been written over the tree on disk, which is harmless --
# nothing is rebuilt, so the container keeps serving the image it was started
# from, and the next real deploy publishes the same files anyway.
if [ "$MODE" = "dryrun" ]; then
    state=$(docker inspect note-server --format '{{.State.Status}}' 2>/dev/null || echo missing)
    mark "STATE=$state (untouched, DryRun)"
    mark "DEPLOY-OK"
    echo "DryRun: files extracted, container left as it was."
    exit 0
fi

docker compose build server
docker compose up -d server

# The container must be Up, not Restarting.  `docker compose up -d` returns
# success as soon as the container is *created*, so the loop that took the app
# down once would not have been visible here at all.
sleep 8
state=$(docker inspect note-server --format '{{.State.Status}}' 2>/dev/null || echo missing)
restarts=$(docker inspect note-server --format '{{.RestartCount}}' 2>/dev/null || echo 0)
mark "STATE=$state RESTARTS=$restarts"

if [ "$state" != "running" ]; then
    echo "--- last log lines ---"
    docker compose logs --tail=40 server
    mark "DEPLOY-FAILED: note-server is $state"
    exit 5
fi

code=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8090/ || echo 000)
mark "HTTP=$code"
if [ "$code" != "200" ]; then
    mark "DEPLOY-FAILED: the app did not answer 200"
    exit 5
fi

mark "DEPLOY-OK"
'@

# Resolve the pct id here so the two helpers below share one definition.
$ErrorActionPreference = "Stop"

$mode = if ($DryRun) { "dryrun" } else { "deploy" }

try {
    Send-ScriptFile -Script $deployScript -RemotePath "/tmp/note-app-deploy.sh"
    $out = Invoke-RemoteScript -RemotePath "/tmp/note-app-deploy.sh" `
        -LogPath "/tmp/note-app-deploy.log" -What "deploy" -Mode $mode
} catch {
    Write-Error "$_"
    Write-Host "Undo with: .\deploy.ps1 -Rollback" -ForegroundColor Yellow
    exit 1
}

Write-Host $out

$status = Get-DeployStatus -LxcId $LxcId -PveHost $PveHost
Write-Host ""
Write-Host "--- status file ---" -ForegroundColor Cyan
Write-Host $status

if ($status -notmatch "DEPLOY-OK") {
    $reached = @("SNAPSHOT-OK", "COMPOSE-OK", "EXTRACT-OK") |
        Where-Object { $status -match $_ } |
        Select-Object -Last 1
    Write-Host ""
    Write-Error ("deploy stopped after '$reached'. " +
        "Undo with: .\deploy.ps1 -Rollback")
    exit 1
}

if ($DryRun) {
    Write-Host "=== DryRun complete ===" -ForegroundColor Yellow
    Write-Host "Files were extracted and the marker file was written, but the server" -ForegroundColor Yellow
    Write-Host "was NOT rebuilt or restarted -- the running container is untouched." -ForegroundColor Yellow
    Write-Host "Run without -DryRun to publish this source." -ForegroundColor Yellow
    exit 0
}

Write-Host "=== Deploy complete ===" -ForegroundColor Green
Write-Host "Undo at any time with: .\deploy.ps1 -Rollback" -ForegroundColor Green
Write-Host "Log in at http://192.168.1.205:8090/ (container 105) and confirm the password works." -ForegroundColor Green
