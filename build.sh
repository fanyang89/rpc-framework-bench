#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

cd "$ROOT"
mkdir -p "$ROOT/bin" "$ROOT/cpp-server/generated"

go mod download
go build -mod=readonly -trimpath -ldflags='-s -w' -o "$ROOT/bin/bench-client" ./cmd/client
go build -mod=readonly -trimpath -ldflags='-s -w' -o "$ROOT/bin/go-server" ./cmd/go-server

cargo build --release --locked --manifest-path "$ROOT/tonic-server/Cargo.toml"
cp "$ROOT/tonic-server/target/release/tonic-client" "$ROOT/bin/tonic-client"
cp "$ROOT/tonic-server/target/release/tonic-server" "$ROOT/bin/tonic-server"

protoc \
  -I "$ROOT/proto" \
  --cpp_out="$ROOT/cpp-server/generated" \
  --grpc_out="$ROOT/cpp-server/generated" \
  --plugin=protoc-gen-grpc="$(command -v grpc_cpp_plugin)" \
  "$ROOT/proto/benchmark.proto"

read -r -a grpc_cflags <<<"$(pkg-config --cflags grpc++ protobuf)"
read -r -a grpc_libs <<<"$(pkg-config --libs grpc++ protobuf)"
cpp_sources=(
  "$ROOT/cpp-server/server.cc"
  "$ROOT/cpp-server/generated/benchmark.pb.cc"
  "$ROOT/cpp-server/generated/benchmark.grpc.pb.cc"
)

c++ -std=c++17 -O3 -DNDEBUG -pthread \
  "${grpc_cflags[@]}" \
  -I "$ROOT/cpp-server/generated" \
  "${cpp_sources[@]}" \
  -o "$ROOT/bin/cpp-server" \
  "${grpc_libs[@]}"
