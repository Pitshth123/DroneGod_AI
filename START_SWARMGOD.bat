@echo off
title SwarmGod
cd /d "%~dp0frontend"
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw -m swarmgod_gui.launcher
  exit /b
)
where python >nul 2>nul
if %errorlevel%==0 (
  start "" python -m swarmgod_gui.launcher
  exit /b
)
where py >nul 2>nul
if %errorlevel%==0 (
  start "" py -m swarmgod_gui.launcher
  exit /b
)
echo Python not found. Install Python 3 first.
pause
