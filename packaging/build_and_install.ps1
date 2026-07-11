#Requires -Version 5.1
<#
    build_and_install.ps1 - one-click "build the current source and install it" for OpenMBB.

    Does exactly what the release chore does by hand, in order:
      1. Build the single-file exe   (PyInstaller via packaging/build.py)  -> dist/openmbb.exe
      2. Build the Windows installer (Inno Setup / ISCC; version read from __version__)
      3. Install it silently         (/VERYSILENT, per-user, no admin)
      4. Verify the installed build  (frozen --selftest [+ --smoketest] must exit 0)

    It builds whatever is in your working tree RIGHT NOW - that is "the latest".
    No git, no network, no auto-update. Re-run any time after you change the code.

    Run it by double-clicking "build-and-install.bat" in the repo root, or:
      powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_and_install.ps1
    Options:
      -SkipSmoketest   verify with --selftest only (skip the GUI smoketest)
      -Force           if OpenMBB is already running, close it automatically
                       (default: refuse and ask you to close it first, since the
                       app may be mid-write to the bike)
#>

[CmdletBinding()]
param(
    [switch]$SkipSmoketest,
    [switch]$Force
)

function Info($m) { Write-Host $m -ForegroundColor Cyan }
function Ok($m)   { Write-Host $m -ForegroundColor Green }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Fail($m) { Write-Host $m -ForegroundColor Red }
function Die($m) {
    Fail ""
    Fail "==================== BUILD / INSTALL FAILED ===================="
    Fail "  $m"
    Fail "==============================================================="
    Pop-Location -ErrorAction SilentlyContinue
    exit 1
}

# A running OpenMBB holds a lock on openmbb.exe, so the installer cannot replace
# it (Inno's RestartManager can't close a Tk app and aborts). Default: refuse and
# tell the user to close it - it may be mid-write to the bike. -Force closes it.
function Ensure-NotRunning($when) {
    $p = @(Get-Process -Name openmbb -ErrorAction SilentlyContinue)
    if ($p.Count -eq 0) { return }
    $ids = ($p | ForEach-Object { $_.Id }) -join ', '
    if (-not $Force) {
        Die "OpenMBB is running (PID $ids). It talks to your motorcycle - close it (make sure it isn't mid-write to the bike), then re-run. Or pass -Force to close it automatically."
    }
    Warn "  OpenMBB is running (PID $ids) - closing it ($when, -Force)..."
    $p | Stop-Process -Force
    $deadline = (Get-Date).AddSeconds(5)
    while (@(Get-Process -Name openmbb -ErrorAction SilentlyContinue).Count -gt 0 -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 200
    }
    if (@(Get-Process -Name openmbb -ErrorAction SilentlyContinue).Count -gt 0) {
        Die "Could not close running OpenMBB (PID $ids). Close it manually and re-run."
    }
    Ok "  closed the running instance."
}

# --- resolve locations -------------------------------------------------------
$Packaging = $PSScriptRoot
$RepoRoot  = Split-Path -Parent $Packaging          # packaging/ -> repo root
$Iss       = Join-Path $Packaging 'openmbb.iss'
$Builder   = Join-Path $Packaging 'build.py'
$DistExe   = Join-Path $RepoRoot  'dist\openmbb.exe'
$SetupOut  = Join-Path $Packaging 'Output\openmbb-setup-windows-x64.exe'
$InitPy    = Join-Path $RepoRoot  'src\openmbb\__init__.py'

Info "OpenMBB - build the current source and install it"
Info "  repo: $RepoRoot"

# --- find python (prefer the project venv) ----------------------------------
$Py = Join-Path $RepoRoot '..\.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $Py = $cmd.Source } else { Die "No Python found (looked for ..\.venv and 'python' on PATH)." }
}
$Py = (Resolve-Path $Py).Path
Info "  python: $Py"

# --- read the authoritative version -----------------------------------------
if (-not (Test-Path $InitPy)) { Die "Cannot find $InitPy" }
$m = Select-String -Path $InitPy -Pattern '__version__\s*=\s*"([\d.]+)"' | Select-Object -First 1
if (-not $m) { Die "Could not parse __version__ from $InitPy" }
$Version = $m.Matches[0].Groups[1].Value
Info "  version: $Version"

# --- find ISCC (Inno Setup compiler) ----------------------------------------
$Iscc = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) {
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { $Iscc = $cmd.Source }
}
if (-not $Iscc) { Die "Inno Setup compiler (ISCC.exe) not found. Install Inno Setup 6.3+ from jrsoftware.org." }
Info "  iscc: $Iscc"

# Fail fast if the app is open, BEFORE the multi-minute build (the installer
# can't replace a running openmbb.exe). -Force closes it instead.
Ensure-NotRunning 'before building'

# CWD must be the repo root: build.py / PyInstaller emit dist/ and build/ relative to it.
Push-Location $RepoRoot

# --- 1/4  build the exe ------------------------------------------------------
Info ""
Info "[1/4] Building the exe (PyInstaller) - this takes a couple of minutes..."
& $Py $Builder
if ($LASTEXITCODE -ne 0) { Die "PyInstaller build failed (exit $LASTEXITCODE)." }
if (-not (Test-Path $DistExe)) { Die "Build reported success but $DistExe is missing." }
Ok  ("      built dist\openmbb.exe ({0} MB)" -f [math]::Round((Get-Item $DistExe).Length / 1MB, 1))

# --- 2/4  build the installer ------------------------------------------------
Info ""
Info "[2/4] Building the installer (Inno Setup, v$Version)..."
$isccOut = & $Iscc "/DAppVersion=$Version" "/DExeSrc=$DistExe" $Iss
if ($LASTEXITCODE -ne 0) { $isccOut | ForEach-Object { Fail "      $_" }; Die "ISCC failed (exit $LASTEXITCODE)." }
if (-not (Test-Path $SetupOut)) { Die "ISCC reported success but $SetupOut is missing." }
Ok  ("      built {0} ({1} MB)" -f [System.IO.Path]::GetFileName($SetupOut), [math]::Round((Get-Item $SetupOut).Length / 1MB, 1))

# --- 3/4  install silently ---------------------------------------------------
Info ""
Info "[3/4] Installing silently (per-user, no admin)..."
Ensure-NotRunning 'before installing'   # re-check: app may have been opened during the build
$Log  = Join-Path $env:TEMP 'openmbb-install.log'
$proc = Start-Process -FilePath $SetupOut `
    -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=$Log" -Wait -PassThru
if ($proc.ExitCode -ne 0) { Die "Installer exited $($proc.ExitCode). See $Log" }
Ok  "      installed (exit 0)"

$InstallExe = Join-Path $env:LOCALAPPDATA 'Programs\OpenMBB\openmbb.exe'
$UninstKey  = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{7E3C9D41-52A6-4F0B-B01D-3E8A2C64F9B7}_is1'
if (Test-Path $UninstKey) {
    $disp = (Get-ItemProperty $UninstKey).DisplayVersion
    if ($disp -eq $Version) { Ok "      registry: v$disp" } else { Warn "      registry shows v$disp (expected v$Version)" }
}
if (-not (Test-Path $InstallExe)) { Die "Installed exe not found at $InstallExe" }

# --- 4/4  verify the installed build ----------------------------------------
Info ""
Info "[4/4] Verifying the installed build..."
$out = & $InstallExe --selftest
if ($LASTEXITCODE -ne 0) { $out | ForEach-Object { Fail "      $_" }; Die "Installed --selftest FAILED (exit $LASTEXITCODE)." }
Ok  "      --selftest PASSED"
if (-not $SkipSmoketest) {
    $out = & $InstallExe --smoketest
    if ($LASTEXITCODE -ne 0) { $out | ForEach-Object { Fail "      $_" }; Die "Installed --smoketest FAILED (exit $LASTEXITCODE)." }
    Ok  "      --smoketest PASSED"
}

Pop-Location
Ok  ""
Ok  "==================== OpenMBB v$Version INSTALLED ===================="
Ok  "  App:    $InstallExe"
Ok  "  Launch: Desktop 'OpenMBB' icon or Start menu"
Ok  "===================================================================="
exit 0
