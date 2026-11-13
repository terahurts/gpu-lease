@echo off
rem ---------------------------------------------------------------------------
rem  Start gpu-lease from a Windows terminal pane.
rem  Put the config next to this file (gpu-lease init -o gpu-lease.toml) and
rem  edit the two paths below, or add a line like this to your launcher batch:
rem
rem    sp -H cmd /k C:\path\to\gpu-lease.cmd
rem ---------------------------------------------------------------------------
setlocal
set "REPO=%~dp0..\.."
set "PYTHON=py -3.13"
set "CONFIG=%~dp0gpu-lease.toml"

title gpu-lease
cd /d "%REPO%"
if not exist "%CONFIG%" (
  echo no config at %CONFIG%
  echo run:  %PYTHON% -m gpu_lease init -o "%CONFIG%"
  pause
  exit /b 1
)
%PYTHON% -m gpu_lease run -c "%CONFIG%"
echo.
echo gpu-lease stopped - exit %errorlevel%
pause
