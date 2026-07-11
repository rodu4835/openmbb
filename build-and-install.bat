@echo off
REM ===========================================================================
REM  Double-click to build the CURRENT OpenMBB source and install it.
REM  Runs packaging\build_and_install.ps1 (build exe -> installer -> install ->
REM  verify). No admin, no network, no auto-update - just the latest local code.
REM  Close OpenMBB before running (it locks its own exe). Options you can add
REM  after the filename: -Force (close a running OpenMBB automatically),
REM  -SkipSmoketest (verify with --selftest only).
REM ===========================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\build_and_install.ps1" %*
echo.
pause
