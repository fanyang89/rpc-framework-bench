#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
OUT="$ROOT/bin/certs"
mkdir -p "$OUT"
openssl req -x509 -newkey rsa:2048 -nodes -days 7 \
  -subj /CN=rpc-benchmark-ca \
  -addext basicConstraints=critical,CA:TRUE \
  -keyout "$OUT/ca.key" -out "$OUT/ca.pem"
openssl req -newkey rsa:2048 -nodes -subj /CN=localhost \
  -addext subjectAltName=DNS:localhost,IP:127.0.0.1 \
  -addext basicConstraints=critical,CA:FALSE \
  -addext extendedKeyUsage=serverAuth \
  -keyout "$OUT/server.key" -out "$OUT/server.csr"
openssl x509 -req -in "$OUT/server.csr" -CA "$OUT/ca.pem" \
  -CAkey "$OUT/ca.key" -CAcreateserial -days 7 -copy_extensions copy \
  -out "$OUT/server.pem"
openssl verify -CAfile "$OUT/ca.pem" -verify_hostname localhost "$OUT/server.pem"
