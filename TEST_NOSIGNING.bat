@echo off
title SwarmGod - Test No Signing
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\test_nosigning.ps1"
