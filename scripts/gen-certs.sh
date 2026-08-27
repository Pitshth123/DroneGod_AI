#!/usr/bin/env bash
# สร้าง dev certificates สำหรับ mTLS (core ↔ cockpit) + MAVLink signing key
# ⚠️ dev เท่านั้น — production ต้องออก cert จาก CA จริง
set -euo pipefail
cd "$(dirname "$0")/../certs"

echo "==> CA"
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
  -subj "/CN=SwarmGod-Dev-CA" -out ca.crt

gen_cert () {  # $1 = name, $2 = CN
  openssl genrsa -out "$1.key" 4096
  openssl req -new -key "$1.key" -subj "/CN=$2" -out "$1.csr"
  openssl x509 -req -in "$1.csr" -CA ca.crt -CAkey ca.key -CAcreateserial \
    -days 825 -sha256 \
    -extfile <(printf "subjectAltName=IP:127.0.0.1,DNS:localhost") \
    -out "$1.crt"
  rm -f "$1.csr"
}

echo "==> server cert (core)"
gen_cert server localhost
echo "==> client cert (cockpit)"
gen_cert client swarmgod-cockpit

echo "==> MAVLink signing key (32 bytes)"
openssl rand -out mavlink_key 32

chmod 600 *.key mavlink_key 2>/dev/null || true
echo "✓ certs ready in ./certs (git-ignored)"
