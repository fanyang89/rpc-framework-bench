# Cross-framework unary gRPC benchmark

This repository compares four plaintext unary gRPC servers with a primary Tonic
load generator and a wire-compatible `grpc.testing.TestService/UnaryCall`
schema. The retained Go client is a secondary cross-check for client bias. This
is a focused server comparison, not a general framework ranking.

The harness does not enable TLS, compression, retries, reflection, or
application logging.

## Requirements

- Linux with `taskset`
- Go 1.26 or later
- Rust and Cargo with the toolchain accepted by `tonic-server/Cargo.lock`
- `protoc`, `grpc_cpp_plugin`, and `pkg-config`
- gRPC C++, protobuf development libraries, and a C++17 compiler
- A separately built grpc-lite interop server

Set `GRPC_LITE_SERVER` to the grpc-lite executable when running benchmarks. The
grpc-lite source tree is not required to be adjacent to this repository.

## Build

```sh
./build.sh
```

The script creates `bin/` and `cpp-server/generated/`, downloads the locked Go
modules, builds stripped Go binaries in read-only module mode, builds
`bin/tonic-client` and `bin/tonic-server` with the checked-in Cargo lock file,
generates C++ protobuf/gRPC sources, and compiles the synchronous C++ server.
Generated C++ files and all binaries are intentionally ignored by Git.

## Servers

Run each server manually with:

```sh
GRPC_LITE_SERVER=/path/to/grpc-lite-interop-server
"$GRPC_LITE_SERVER" --port=51051 --use_tls=false

GOMAXPROCS=1 ./bin/go-server -addr=127.0.0.1:51052

SERVER_THREADS=1 SERVER_ADDR=127.0.0.1:51053 ./bin/tonic-server

./bin/cpp-server 127.0.0.1:51054
```

`GOMAXPROCS=1` limits the Go scheduler. `SERVER_THREADS=1` sets the Tonic Tokio
worker count. The C++ implementation uses the synchronous gRPC server API.

## Single Client Run

Start one server, then run the primary client:

```sh
CLIENT_THREADS=4 ./bin/tonic-client \
  -addr=127.0.0.1:51051 \
  -warmup=2s \
  -duration=10s \
  -concurrency=4 \
  -connections=1 \
  -req_size=1024 \
  -resp_size=1024 \
  -round=1 \
  -label=grpc-lite \
  -server_version=grpc-lite/VERSION \
  -compact
```

All latency fields are microseconds. Any RPC error, missing payload, or wrong
response body length makes the client exit nonzero. The Tonic client gives every
worker its own generated client and Prost request template. It clones that
template for each call because generated unary methods take request ownership;
that allocation/copy cost is part of the measured client path.

Use `./bin/bench-client` with the same flags for the secondary gRPC-Go client.

## Controlled Run

Build all local servers first, then provide the external grpc-lite binary:

```sh
GRPC_LITE_SERVER=/path/to/grpc-lite-interop-server \
GRPC_LITE_VERSION=grpc-lite/VERSION \
CLIENT_IMPL=tonic \
./run.sh
```

`run.sh` uses strict Bash error handling and always cleans up its current server.
For every case it starts one server, waits for its port, runs the client, records
one JSON object, and stops the server. Framework order rotates between rounds to
reduce time-order bias.

Defaults:

| Setting | Default |
| --- | --- |
| grpc-lite port | `51051` |
| grpc-go port | `51052` |
| Tonic port | `51053` |
| gRPC C++ port | `51054` |
| `SERVER_CPU` | `15` |
| `CLIENT_CPUS` | `8-14` |
| `ROUNDS` | `5` |
| `WARMUP` | `2s` |
| `DURATION` | `10s` |
| `CONCURRENCY` | `4` |
| `CONNECTIONS` | `1` |
| `REQ_SIZE` | `1024` |
| `RESP_SIZE` | `1024` |
| `CLIENT_IMPL` | `tonic` |
| `CLIENT_THREADS` | `4` |
| `RESULT_FILE` | `results/raw-${CLIENT_IMPL}-client.jsonl` |

The four port defaults can be overridden with `GRPC_LITE_PORT`, `GRPC_GO_PORT`,
`TONIC_PORT`, and `GRPC_CPP_PORT`.

## Method

The proto retains the official gRPC interop field numbers and service path for
`grpc.testing.TestService/UnaryCall`. A request carries a zero-filled payload
and asks the server to return a zero-filled payload of the configured size.

The same selected client binary drives every server. Tonic is the primary load
generator; gRPC-Go remains available through `CLIENT_IMPL=go` to expose results
that depend on client implementation. Each closed-loop worker warms continuously,
joins a barrier, and begins measurement at one shared absolute time. An RPC is
counted when it starts before the measurement deadline; the client drains that
RPC even if it completes after the deadline. Workers keep local latency slices,
which are merged and sorted after measurement.

Tonic creates exactly `CONNECTIONS` real channels. Generated clients cloned by
workers round-robin across those channels, so `CONNECTIONS=1` multiplexes every
worker over one HTTP/2 connection. `CLIENT_THREADS` controls its Tokio runtime.
Neither client enables TLS, compression, retries, or application logging.

Throughput is completed RPCs divided by the configured duration. Percentiles use
nearest-rank selection. Reported comparisons use the median of five rounds.

## Fairness and Scope

`run.sh` pins every server to one logical CPU and the selected client to a
separate CPU set. The gRPC-Go server uses `GOMAXPROCS=1`; the Tonic server uses
one Tokio worker. Only one server runs during each measured case. The harness
does not change CPU governor, NUMA policy, interrupt affinity, or background
services, so those controls remain the operator's responsibility.

The included conclusion applies only to a single-core server, one connection,
four concurrent closed-loop workers, and 1 KiB unary requests and responses.
The historical Go client limits grpc-lite to about 124k RPC/s here, compared with
about 180k RPC/s in its native harness, which compresses server-side differences.
The gRPC C++ 1.48.4 result uses an old system package and does not represent a
modern gRPC C++ release.

## Versions

The checked-in result used:

| Component | Version |
| --- | --- |
| Go | 1.26.5 |
| gRPC-Go client and server | 1.82.1 |
| Rust / Cargo | 1.97.1 |
| Tonic | 0.14.6 |
| Tokio | 1.53.1 |
| Prost | 0.14.4 |
| protoc / libprotobuf | 3.19.6 |
| gRPC C++ | 1.48.4 |
| C++ build | C++17, `-O3 -DNDEBUG` |

The grpc-lite version is supplied at run time through `GRPC_LITE_VERSION`; the
harness does not assume a source revision.

## Results

### Primary: Tonic client

The primary run used the Tonic 0.14.6 client, one active server at a time, and a
rotated framework order. Full data and methodology are in
[`results/2026-07-27-single-core-unary-tonic-client/`](results/2026-07-27-single-core-unary-tonic-client/).

| Server | RPC/s | p50 (us) | p95 (us) | p99 (us) |
| --- | ---: | ---: | ---: | ---: |
| Tonic | 78182.4 | 50.165 | 66.375 | 75.692 |
| grpc-lite | 76736.8 | 50.946 | 71.585 | 81.383 |
| grpc-go | 72488.8 | 52.088 | 77.126 | 133.171 |
| gRPC C++ | 61660.5 | 58.841 | 109.816 | 131.057 |

### Secondary: Go client

The historical secondary run used the gRPC-Go 1.82.1 client and a fixed order.
Full data and its additional environmental caveats are in
[`results/2026-07-27-single-core-unary-go-client/`](results/2026-07-27-single-core-unary-go-client/).

| Server | RPC/s | p50 (us) | p95 (us) | p99 (us) |
| --- | ---: | ---: | ---: | ---: |
| grpc-lite | 123796.6 | 31.390 | 48.271 | 84.659 |
| Tonic | 98387.1 | 39.104 | 59.792 | 107.583 |
| grpc-go | 91445.9 | 41.949 | 81.153 | 144.592 |
| gRPC C++ | 88921.4 | 35.747 | 97.824 | 156.205 |

The grpc-lite/Tonic ranking reverses with the client: Tonic leads grpc-lite by
about 1.9% under the Tonic client, while grpc-lite leads Tonic by about 25.8%
under the Go client. This is evidence of material client-server stack affinity,
not a general framework ranking. grpc-lite leads grpc-go and the older gRPC C++
server under both clients. The common load generator remains part of each stack
and still constrains observed throughput, so neither table is a server-only
maximum.

## References

- [Official gRPC benchmarking guide](https://grpc.io/docs/guides/benchmarking/)
- [gRPC-Go benchmark sources and documentation](https://github.com/grpc/grpc-go/tree/master/benchmark)
- [Tonic 0.14.6 documentation](https://docs.rs/tonic/0.14.6/tonic/)
