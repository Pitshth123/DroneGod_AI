@echo off
set SWARMGOD_PROFILE=sitl
set SWARMGOD_GRPC_ADDR=127.0.0.1:50054
set SWARMGOD_MISSION_AUTHORITY=
set SWARMGOD_DB=logs/f9a-bench.db
set SWARMGOD_MAVLINK_SIGNING=off
cd /d "%~dp0..\backend"
go run ./cmd/swarmgod-core
