# Invoke-FrontendChecks.ps1 — typecheck + production build the Vue frontend.
#
# Reproduces CI's `frontend` job (`npm run typecheck`, `npm run build`) on Windows.
# Two environment traps on this repo make a naive `npm run build` impossible, and
# both are worked around here rather than papered over:
#
#   1. UNC + cmd.exe: npm/node are launched through .cmd shims that run under
#      cmd.exe, and cmd.exe cannot use a UNC path as its working directory — it
#      silently falls back to C:\Windows, so node_modules\.bin\vue-tsc is not found
#      (MODULE_NOT_FOUND). Addressing the repo through a mapped drive letter fixes
#      typecheck.
#   2. Mapped drive + non-ASCII path + Vite: `vite build` resolves its root through
#      POSIX-style paths and mangles Z:\ with the Chinese path segments into
#      Z:\bob-fnos\..., so it cannot find index.html. `vue-tsc` is happy, Vite is
#      not. The build therefore runs from a local ASCII mirror instead.
#
# Under a UNC workspace the script mirrors the frontend into a persistent local
# directory (node_modules is re-mirrored only when it is missing, which keeps
# repeat runs fast) and runs everything there. `npm ci` in the mirror regenerates a
# consistent node_modules from the committed lock file.
#
# Usage:
#   pwsh -NoProfile -File scripts\Invoke-FrontendChecks.ps1
#   pwsh -NoProfile -File scripts\Invoke-FrontendChecks.ps1 -SkipInstall
#   pwsh -NoProfile -File scripts\Invoke-FrontendChecks.ps1 -KeepMirror
param(
    [switch]$SkipInstall,
    [switch]$KeepMirror,
    [string]$MirrorRoot = ''
)

$ErrorActionPreference = 'Continue'
$uncRoot = Split-Path -Parent $PSScriptRoot
$sourceFrontend = Join-Path $uncRoot 'frontend'

if (-not (Test-Path -LiteralPath (Join-Path $sourceFrontend 'package.json'))) {
    throw "frontend not found at $sourceFrontend"
}

function Invoke-Robocopy {
    param([string]$From, [string]$To, [string[]]$ExcludeDirs = @())
    # NB: do not name this $args — that is a reserved automatic variable and
    # assigning to it silently breaks the call.
    $roboArgs = @($From, $To, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/R:1', '/W:1')
    if ($ExcludeDirs.Count -gt 0) { $roboArgs += '/XD'; $roboArgs += $ExcludeDirs }
    robocopy @roboArgs | Out-Null
    # robocopy exit codes 0-7 are success (bit flags); >=8 is a real failure.
    if ($LASTEXITCODE -ge 8) { throw "robocopy $From -> $To failed (exit $LASTEXITCODE)" }
}

# Decide where to run. A genuinely local path can be used as-is. A UNC path *or* a
# mapped network drive must be mirrored: cmd.exe breaks on the former and Vite
# mangles the latter into `Z:\<server>\<share>\...` (see the header).
$drive = $null
$rooted = $null
if ($uncRoot -match '^([A-Za-z]):\\(.*)$') {
    $drive = $Matches[1]
    $rooted = $Matches[2]
}
$mapped = $false
if ($drive) {
    $info = Get-PSDrive -Name $drive -ErrorAction SilentlyContinue
    $mapped = [bool]($info -and $info.DisplayRoot)
}
$needsMirror = ($uncRoot -match '^\\\\') -or $mapped

if (-not $needsMirror) {
    $work = $sourceFrontend
    Write-Output "== local workspace: running in place =="
}
else {
    if (-not $MirrorRoot) {
        $MirrorRoot = Join-Path $env:LOCALAPPDATA 'mql-fe-build'
    }
    $work = $MirrorRoot
    $reason = if ($mapped) { "mapped network drive ${drive}: (Vite cannot build there)" } else { 'UNC path (cmd.exe cannot build there)' }
    Write-Output "== $reason =="
    Write-Output "== mirroring frontend to $work =="
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    Invoke-Robocopy -From $sourceFrontend -To $work -ExcludeDirs @('node_modules', 'dist')
    Write-Output "   sources mirrored"
}

Set-Location $work
$failures = @()

if (-not $SkipInstall) {
    Write-Output "`n== npm ci =="
    npm ci --no-audit --no-fund 2>&1 | Select-Object -Last 6
    if ($LASTEXITCODE -ne 0) { $failures += 'npm ci' }
}

Write-Output "`n== npm run typecheck =="
npm run typecheck 2>&1 | Select-Object -Last 40
if ($LASTEXITCODE -ne 0) { $failures += 'typecheck' }

Write-Output "`n== npm run build =="
npm run build 2>&1 | Select-Object -Last 40
if ($LASTEXITCODE -ne 0) { $failures += 'build' }

if ($isUnc -and -not $KeepMirror) {
    Remove-Item -LiteralPath (Join-Path $work 'dist') -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output ""
if ($failures.Count -eq 0) {
    Write-Output '== FRONTEND OK: typecheck + build passed =='
    exit 0
}
Write-Output "== FRONTEND FAILED: $($failures -join ', ') =="
exit 1
