@echo off
title SwarmGod - RC Debug
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\diag_rc.ps1"
