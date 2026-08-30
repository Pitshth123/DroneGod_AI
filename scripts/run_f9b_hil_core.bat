@echo off
setlocal

rem F9B actual-FC bench launcher. This script deliberately refuses to create
rem the physical-safety confirmation or invent a HOME location for the operator.
if /I not "%SWARMGOD_BENCH_CONFIRM%"=="PROPS-REMOVED-BENCH" (
  echo ERROR: set SWARMGOD_BENCH_CONFIRM=PROPS-REMOVED-BENCH only after the bench is physically safe.
  exit /b 2
)
if "%SWARMGOD_HOME_LOC%"=="" (
  echo ERROR: set an explicit SWARMGOD_HOME_LOC=lat,lon,alt,heading for HIL bench safety.
  exit /b 2
)

set SWARMGOD_PROFILE=hil
set SWARMGOD_GRPC_ADDR=127.0.0.1:50055
set SWARMGOD_MISSION_AUTHORITY=core-single-wait
set SWARMGOD_DB=logs/f9b-hil.db

cd /d "%~dp0..\backend"
go run ./cmd/swarmgod-core
