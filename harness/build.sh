#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

cd "$ROOT"
mkdir -p "$ROOT/bin"

go mod download
go build -mod=readonly -trimpath -ldflags='-s -w' -o "$ROOT/bin/bench-client" ./workloads/go/client
go build -mod=readonly -trimpath -ldflags='-s -w' -o "$ROOT/bin/go-server" ./workloads/go/server

cargo build --release --locked --manifest-path "$ROOT/workloads/rust-tonic/Cargo.toml"
cp "$ROOT/workloads/rust-tonic/target/release/tonic-client" "$ROOT/bin/tonic-client"
cp "$ROOT/workloads/rust-tonic/target/release/tonic-server" "$ROOT/bin/tonic-server"

cmake -S "$ROOT/workloads/cpp-grpc" -B "$ROOT/bin/cpp-build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release
cmake --build "$ROOT/bin/cpp-build" --parallel "${BUILD_JOBS:-4}"
cp "$ROOT/bin/cpp-build/cpp-server" "$ROOT/bin/cpp-server"
