# Invoke-FrontendChecks.ps1 — typecheck + build the Vue frontend reliably.
#
# Why the drive mapping: the repo lives on a UNC share (\\bob-fnos\...). Node and
# npm are launched through .cmd shims that run under cmd.exe, and **cmd.exe cannot
# use a UNC path as its working directory** — it silently falls back to
# C:\Windows, so local binaries such as node_modules\vue-tsc\bin\vue-tsc.js are
# not found and every run fails with MODULE_NOT_FOUND. Addressing the repo
# through a mapped drive letter makes cmd.exe (and therefore npm) work.
#
# Usage:
#   pwsh -NoProfile -File scripts\Invoke-FrontendChecks.ps1
#   pwsh -NoProfile -File scripts\Invoke-FrontendChecks.ps1 -SkipInstall
#   pwsh -NoProfile -File scripts\Invoke-FrontendChecks.ps1 -Drive Z
param(
    [string]$Drive = 'Z',
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Continue'

# scripts\ lives in the repo root.
$uncRoot = Split-Path -Parent $PSScriptRoot

# Resolve (or create) a drive mapping that points at the UNC share containing the
# repo, then address the repo through it.
function Resolve-RepoDrive {
    param([string]$UncPath, [string]$Preferred)

    $existing = Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayRoot -and $UncPath.StartsWith($_.DisplayRoot, 'OrdinalIgnoreCase') }
    if ($existing) { return $existing[0].Name }

    $share = ([uri]$UncPath).Host
    $shareRoot = "\\$share\" + ($UncPath.TrimStart('\').Split('\')[1])
    foreach ($letter in @($Preferred, 'Y', 'X', 'W', 'V')) {
        if (Test-Path -LiteralPath "${letter}:\") { continue }
        cmd /c "net use ${letter}: `"$shareRoot`" /persistent:no" > $null 2>&1
        if ($LASTEXITCODE -eq 0) { return $letter }
    }
    throw "no free drive letter could be mapped to $shareRoot; map the share manually and re-run with -Drive <letter>"
}

if ($uncRoot -notmatch '^\\\\') {
    # Already a local path: no mapping needed.
    $frontend = Join-Path $uncRoot 'frontend'
}
else {
    $letter = Resolve-RepoDrive -UncPath $uncRoot -Preferred $Drive
    $relative = $uncRoot.Substring($uncRoot.IndexOf('\', 2) + 1)
    $frontend = "${letter}:\$relative\frontend"
    Write-Output "== mapped $uncRoot -> ${letter}: =="
}

if (-not (Test-Path -LiteralPath $frontend)) { throw "frontend not found at $frontend" }

Set-Location $frontend
Write-Output "== frontend: $frontend =="

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

Write-Output ""
if ($failures.Count -eq 0) {
    Write-Output '== FRONTEND OK: typecheck + build passed =='
    exit 0
}
Write-Output "== FRONTEND FAILED: $($failures -join ', ') =="
exit 1
