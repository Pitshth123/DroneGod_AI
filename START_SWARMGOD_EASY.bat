@echo off
chcp 65001 >nul 2>&1
title SwarmGod - Easy Start
set "PSModulePath=%SystemRoot%\System32\WindowsPowerShell\v1.0\Modules;%ProgramFiles%\WindowsPowerShell\Modules;%USERPROFILE%\Documents\WindowsPowerShell\Modules"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-SwarmGodEasy.ps1"
if errorlevel 1 (
  echo.
  echo SwarmGod could not start. Review the message above.
  pause
)
