#!/usr/bin/env bash
# สร้าง gRPC stubs จาก proto/ ทั้งฝั่ง Go และ Python
# ต้องมี: protoc, protoc-gen-go, protoc-gen-go-grpc, python grpcio-tools
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Go stubs -> backend/gen/"
mkdir -p backend/gen
protoc -I proto \
  --go_out=backend/gen --go_opt=paths=source_relative \
  --go-grpc_out=backend/gen --go-grpc_opt=paths=source_relative \
  proto/swarmgod/v1/*.proto

echo "==> Python stubs -> frontend/swarmgod_gui/gen/"
mkdir -p frontend/swarmgod_gui/gen
python -m grpc_tools.protoc -I proto \
  --python_out=frontend/swarmgod_gui/gen \
  --grpc_python_out=frontend/swarmgod_gui/gen \
  proto/swarmgod/v1/*.proto

# แก้ import ให้เป็น relative (ปัญหาคลาสสิกของ grpc_tools ใน Python)
touch frontend/swarmgod_gui/gen/__init__.py

echo "✓ proto stubs generated (Go + Python)"
