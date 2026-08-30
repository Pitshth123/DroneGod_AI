@echo off
set SWARMGOD_PROFILE=sitl
set SWARMGOD_GRPC_ADDR=127.0.0.1:50053
set SWARMGOD_MISSION_AUTHORITY=core-single-wait
set SWARMGOD_DB=logs/missionverify.db
set SWARMGOD_MAVLINK_SIGNING=off
cd /d "%~dp0..\backend"
go run ./cmd/swarmgod-core
