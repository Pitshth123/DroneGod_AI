@echo off
title SwarmGod - SysID 250
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start_sysid250.ps1"
