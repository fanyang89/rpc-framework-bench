# Cross-framework unary gRPC benchmark

This repository measures four plaintext unary gRPC servers with Tonic and Go
load generators using a wire-compatible
`grpc.testing.TestService/UnaryCall` schema. It distinguishes low-concurrency
latency matrices from saturation sweeps; only a validated saturation sweep can
support a server-capacity comparison.

The harness does not enable TLS, compression, retries, reflection, or
application logging.

## Requirements

- Linux with `/proc`, `taskset`, `getconf`, and nanosecond GNU `date`
- `jq`
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
"$GRPC_LITE_SERVER" --port=25051 --use_tls=false

GOMAXPROCS=1 ./bin/go-server -addr=127.0.0.1:25052

SERVER_THREADS=1 SERVER_ADDR=127.0.0.1:25053 ./bin/tonic-server

./bin/cpp-server 127.0.0.1:25054
```

`GOMAXPROCS=1` limits the Go scheduler. `SERVER_THREADS=1` sets the Tonic Tokio
worker count. The C++ implementation uses the synchronous gRPC server API.

## Single Client Run

Start one server, then run the selected client:

```sh
CLIENT_THREADS=4 ./bin/tonic-client \
  -addr=127.0.0.1:25051 \
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
worker its own generated client and Prost request template. Generated unary
methods take request ownership, so each call clones the template; the payload is
generated as reference-counted `Bytes`, making this a refcount clone rather than
a payload allocation and copy.

Use `./bin/bench-client` with the same flags for the gRPC-Go client.

## Controlled Run

Build all local servers first, then provide the external grpc-lite binary:

```sh
GRPC_LITE_SERVER=/path/to/grpc-lite-interop-server \
GRPC_LITE_VERSION=grpc-lite/VERSION \
CLIENT_IMPL=tonic \
./run.sh
```

`run.sh` uses strict Bash error handling and always cleans up its current server.
For every case it starts one server, waits for its port, samples that process,
starts the configured client processes in the background, waits for all of them,
samples and immediately stops the server, validates the client JSON with `jq`,
and records one compact JSON object. Framework order rotates between rounds to
reduce time-order bias.

Each result has a `server_cpu` object containing `cpu_seconds`, `wall_seconds`,
and `utilization_percent`. This is the server process's average CPU usage from
immediately before client process startup until all client processes exit. The
interval includes client process startup, connection setup, warmup, measurement,
draining, and client result serialization. Linux process CPU includes all server
threads but excludes network softirq CPU. It can exceed 100% without single-CPU
affinity.
With the server fixed to one logical CPU, about 95-100% means that logical CPU is
saturated. Kernel or network processing can instead limit a platform while the
server process remains below 100%. A capacity conclusion still requires checking
client headroom and host-level CPU and network telemetry.

Defaults:

| Setting | Default |
| --- | --- |
| grpc-lite port | `25051` |
| grpc-go port | `25052` |
| Tonic port | `25053` |
| gRPC C++ port | `25054` |
| `SERVER_CPU` | `15` |
| `CLIENT_CPUS` | `8-14` |
| `CLIENT_PROCESSES` | `1` |
| `CLIENT_CPU_SETS` | empty |
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
Ports `25051-25054` are below Linux's default ephemeral range
`32768-60999`. This avoids rapid-restart bind conflicts seen with the old gRPC
C++ server when benchmark ports were inside that range.

## Capacity Run

Use multiple independently pinned client processes when one load-generator
process cannot provide enough offered load. This example starts two gRPC-Go
clients, each with concurrency 256 and 8 connections:

```sh
GRPC_LITE_SERVER=/path/to/grpc-lite-interop-server \
GRPC_LITE_VERSION=grpc-lite/VERSION \
CLIENT_IMPL=go \
CLIENT_PROCESSES=2 \
CLIENT_CPU_SETS="1-3,6-8 9-10,12-14" \
CONCURRENCY=256 \
CONNECTIONS=8 \
./run.sh
```

Each process has its own `taskset` CPU set. The aggregate has total concurrency
512 and 16 total connections. Compare aggregate throughput and server CPU only;
do not compare aggregate latency or calculate cross-process percentiles.
Percentiles cannot be merged from independent histograms, so aggregate latency
fields are `null`. Per-process latency remains available in `process_results`
for diagnostics only.

With `CLIENT_PROCESSES=1`, `CLIENT_CPUS` is used and the original client JSON
schema, including complete latency fields, is retained. With more processes,
`CLIENT_CPU_SETS` must contain exactly one whitespace-separated set per process.

## Saturation Sweep

`sweep.sh` only coordinates repeated `run.sh` invocations. It does not select a
winner or duplicate server lifecycle logic.

```sh
RUN_ID=quiet-host-1
GRPC_LITE_SERVER=/path/to/grpc-lite-interop-server \
GRPC_LITE_VERSION=grpc-lite/VERSION \
RUN_ID="$RUN_ID" \
bash ./sweep.sh

go run ./scripts/summarize.go results/sweeps/quiet-host-1/*.jsonl
```

Sweep defaults:

| Setting | Default |
| --- | --- |
| `CLIENT_IMPLS` | `go tonic` |
| `CONCURRENCY_LEVELS` | `4 16 64 256` |
| `ROUNDS` | `3` |
| `WARMUP` | `2s` |
| `DURATION` | `10s` |
| `CLIENT_PROCESSES` | `1` |
| `CLIENT_CPU_SETS` | empty |

All lists may be overridden, for example
`CLIENT_IMPLS="tonic go" CONCURRENCY_LEVELS="8 32 128"`. The script passes the
grpc-lite executable and version, ports, CPU affinity, payload sizes,
connections, client process count and CPU sets, and Tonic client thread count
through to `run.sh`. It exits on the first failed run and writes one file per
client and concurrency level under `results/sweeps/<RUN_ID>/`; filenames include
`p<N>-conn<connections-per-process>` to prevent process/connection configurations
from overwriting each other. Without `RUN_ID`, it uses a UTC timestamp.

`scripts/summarize.go` accepts one or more JSONL files, discovers every server
label present, and groups by client version, server, client process count, total
concurrency, and total connections. It prints the across-round median throughput,
p50/p95/p99 latency, server CPU utilization, and minimum and maximum round
throughput. Legacy JSON falls back to one process and its `config.concurrency`
and `config.connections` values. Nullable aggregate latency prints `n/a`. The
telemetry count distinguishes
measured rounds from legacy JSON without `server_cpu`; a group with no telemetry
prints `n/a` rather than zero. The summarizer still rejects any result with RPC
errors and does not infer saturation or rank servers.

`results/sweeps/` is ignored because sweeps are provisional until host and CPU
conditions are reviewed. To publish an accepted sweep, explicitly move it to a
dated result directory, for example:

```sh
mv results/sweeps/quiet-host-1 results/2026-07-27-single-core-unary-saturation
```

## Method

The proto retains the official gRPC interop field numbers and service path for
`grpc.testing.TestService/UnaryCall`. A request carries a zero-filled payload
and asks the server to return a zero-filled payload of the configured size.

The same selected client binary drives every server within a matrix or sweep.
Both `CLIENT_IMPL=go` and `CLIENT_IMPL=tonic` are required to expose results that
depend on client implementation. Each closed-loop worker warms continuously,
joins a barrier, and begins measurement at one shared absolute time. An RPC is
counted when it starts before the measurement deadline; the client drains that
RPC even if it completes after the deadline. Workers keep local latency slices,
which are merged and sorted after measurement.

Each client process creates exactly `CONNECTIONS` real channels. Generated Tonic
clients cloned by workers round-robin across those channels, so `CONNECTIONS=1`
multiplexes every worker in that process over one HTTP/2 connection.
`CLIENT_THREADS` controls each Tonic process's Tokio runtime. Neither client
enables TLS, compression, retries, or application logging.

Throughput is completed RPCs divided by the configured duration. Percentiles use
nearest-rank selection. Reported comparisons use round medians.

### Latency Matrix

The retained matrices use four closed-loop workers. In a closed loop, each
worker waits for its previous RPC before issuing another, so approximately:

```text
QPS ~= concurrency / mean RTT (seconds)
```

At `CONCURRENCY=4`, lower pair latency directly produces higher QPS even when
the server has substantial unused capacity. The result therefore measures the
complete client-server pair and cannot be used as a server-only throughput
ranking. If switching to the Tonic client increases client scheduling,
serialization, or transport time, mean RTT rises and every server appears
slower by this formula; that does not imply every server regressed.

The separate Tonic-client and Go-client result directories are retained as
evidence of client-server stack affinity. Differences in their relative order
reinforce why neither C=4 table is a server-capacity ranking.

### Saturation Sweep

A capacity comparison must progressively increase concurrency. A server may be
compared only after its assigned CPU is saturated, the client still has CPU and
load-generation headroom, and throughput has entered a plateau as offered load
increases. The per-case server process telemetry records CPU saturation, but
client headroom must still be confirmed from host telemetry alongside the JSONL
output; latency growth alone is insufficient.

Run the complete sweep with both clients. A server-capacity conclusion should
converge under enough offered load for both clients. If one client plateaus
first, the server CPU remains unsaturated, throughput keeps rising, or the two
clients do not converge, report the comparison as inconclusive rather than
choosing a winner.

## Fairness and Scope

`run.sh` pins every server to one logical CPU and each selected client process to
its configured CPU set. The gRPC-Go server uses `GOMAXPROCS=1`; the Tonic server
uses one Tokio worker. Only one server runs during each measured case. The
harness does not change CPU governor, NUMA policy, interrupt affinity, or
background services, so those controls remain the operator's responsibility.

The retained latency matrices apply only to a single-core server, one
connection, four concurrent closed-loop workers, and 1 KiB unary requests and
responses. The gRPC C++ 1.48.4 result uses an old system package and does not
represent a modern gRPC C++ release.

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

### Formal capacity

These high-load, two-process medians are the current single-core server-capacity
result. Each client process used concurrency 256 and 8 connections, for total
concurrency 512 and 16 connections. Only aggregate throughput and server process
CPU are compared; aggregate latency is intentionally `null`. Full results and
methodology are in
[`results/2026-07-27-single-core-unary-capacity-c2d05b8/`](results/2026-07-27-single-core-unary-capacity-c2d05b8/).

| Server | Go client RPC/s | Go client server CPU (%) | Tonic client RPC/s | Tonic client server CPU (%) |
| --- | ---: | ---: | ---: | ---: |
| grpc-lite | 501737.0 | 89.496 | 432794.2 | 86.593 |
| Tonic | 119371.4 | 78.530 | 114829.9 | 82.036 |
| grpc-go | 90548.6 | 87.579 | 89474.0 | 89.266 |
| gRPC C++ | 75685.8 | 85.619 | 73872.9 | 88.183 |

Previous baseline:
[`results/2026-07-27-single-core-unary-capacity/`](results/2026-07-27-single-core-unary-capacity/).

| Load generator | Previous revision | New revision | Previous grpc-lite RPC/s | New grpc-lite RPC/s | Delta (%) |
| --- | --- | --- | ---: | ---: | ---: |
| Go | `e24e5a2` | `c2d05b8` | 480009.6 | 501737.0 | +4.526451 |
| Tonic | `e24e5a2` | `c2d05b8` | 431940.8 | 432794.2 | +0.197573 |

grpc-lite has a clear capacity lead under both load generators. The Tonic load
generator gives grpc-lite a lower absolute bound than the Go load generator, but
both produce the same server ordering. Under the Go load generator, grpc-lite's
observed increase is larger than the changes in the Tonic and grpc-go controls.
The Tonic load generator remains on an approximately 433k RPC/s platform and
cannot establish that there was no server improvement. The gRPC C++ result uses
the old 1.48.4 system package and does not represent a current release.

### Low-concurrency end-to-end matrix: Tonic client

This C=4 run used the Tonic 0.14.6 client, one active server at a time, and a
rotated framework order. It is a latency matrix, not a server ranking. Full data
and methodology are in
[`results/2026-07-27-single-core-unary-tonic-client/`](results/2026-07-27-single-core-unary-tonic-client/).

| Server | RPC/s | p50 (us) | p95 (us) | p99 (us) |
| --- | ---: | ---: | ---: | ---: |
| Tonic | 78182.4 | 50.165 | 66.375 | 75.692 |
| grpc-lite | 76736.8 | 50.946 | 71.585 | 81.383 |
| grpc-go | 72488.8 | 52.088 | 77.126 | 133.171 |
| gRPC C++ | 61660.5 | 58.841 | 109.816 | 131.057 |

### Low-concurrency end-to-end matrix: Go client

This historical C=4 run used the gRPC-Go 1.82.1 client and a fixed order. It is
a latency matrix, not a server ranking. Full data and its additional
environmental caveats are in
[`results/2026-07-27-single-core-unary-go-client/`](results/2026-07-27-single-core-unary-go-client/).

| Server | RPC/s | p50 (us) | p95 (us) | p99 (us) |
| --- | ---: | ---: | ---: | ---: |
| grpc-lite | 123796.6 | 31.390 | 48.271 | 84.659 |
| Tonic | 98387.1 | 39.104 | 59.792 | 107.583 |
| grpc-go | 91445.9 | 41.949 | 81.153 | 144.592 |
| gRPC C++ | 88921.4 | 35.747 | 97.824 | 156.205 |

The relative order of grpc-lite and Tonic changes with the client. This remains
evidence of material client-server stack affinity, not a server ranking. These
historical low-concurrency matrices are latency-oriented pair results and do not
supersede the formal capacity result above.

## References

- [Official gRPC benchmarking guide](https://grpc.io/docs/guides/benchmarking/)
- [gRPC-Go benchmark sources and documentation](https://github.com/grpc/grpc-go/tree/master/benchmark)
- [Tonic 0.14.6 documentation](https://docs.rs/tonic/0.14.6/tonic/)
