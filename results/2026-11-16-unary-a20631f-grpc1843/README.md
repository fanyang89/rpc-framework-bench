# Unary comparison, 2026-11-16

168 measured cases completed with zero RPC errors. These are observed
single-logical-CPU, localhost results, not portable framework capacity limits.

## Versions and host

- grpc-lite: `a20631f2dbe82d69b0352dc95dd1550343265f6f`, Zig 0.17.0,
  `zig build streaming-soak-peer -Doptimize=ReleaseFast`.
- gRPC C++: 1.84.3, commit `df3cb48d44b5bddd0f49620d4f01dd29e2ac5443`.
  Upstream bundled protobuf 35.1.0 and Abseil 20250512; CMake Release,
  C++17, benchmark `-O3 -DNDEBUG`, static gRPC and protobuf libraries.
- gRPC-Go: 1.82.1; Tonic: 0.14.6. Existing lock files retained.
- AMD Ryzen 9 9955HX, 16 cores / 32 threads. Server pinned to CPU 15;
  its SMT sibling is CPU 31. CPU governor was not changed.
- Request and response: 1024 bytes each; plaintext unary; no compression.
- Three rounds per case, 2-second warmup and 10-second measurement.
  Server order rotates between rounds. Tables use round medians.

Machine details, compiler versions, and binary SHA-256 values are in
`single-client/environment.json`. Full summaries and raw records are retained
in both subdirectories.

## High-load comparison

Two client processes, CPUs `1-7` and `8-14`; eight connections and concurrency
256 per process: total concurrency 512, total connections 16.

| Server | Go client RPC/s | Tonic client RPC/s |
| --- | ---: | ---: |
| grpc-lite | 534,006 | 448,438 |
| Tonic | 108,465 | 104,632 |
| gRPC-Go | 82,046 | 80,236 |
| gRPC C++ (synchronous API) | 71,075 | 71,988 |

At this configuration, grpc-lite achieved 4.92x Tonic's throughput with the Go
client and 4.29x with the Tonic client. These are configuration-specific ratios.
The C++ result represents the synchronous server, not its async/callback APIs.

The sweep also measured total concurrency 128 and 1024. grpc-lite throughput
peaked at 512 for both clients; at 1024 it fell to 504,602 and 409,892 RPC/s.
C++ throughput also fell with increasing concurrency, while Go continued
improving. Therefore this table does not select each framework's optimal tuning.

Host telemetry sampled `/proc/stat` and `/proc/net/dev` every second throughout
the multi-client sweep. CPU 15 averaged 99.0% busy, including 16.4% softirq.
Its SMT sibling averaged 0.1% busy. Client cores averaged 10.2%-26.8% busy,
with per-core p95 busy at most 71.0%; no sampled CPU steal was observed.
These whole-sweep measurements show host CPU headroom but do not rule out
single-thread/client scheduling bottlenecks in individual cases.

Server process CPU excludes network softirq and includes warmup/startup/draining.
At concurrency 512 it was 88.2%/85.0% for grpc-lite (Go/Tonic clients), despite
the host server CPU being busy. Do not interpret the gap as unused server-core
capacity. The two load generators still differ materially for grpc-lite;
an exact client-independent capacity ceiling remains inconclusive.

## Low-concurrency latency

One client process on CPUs `8-14`, concurrency 4, one connection.
Values are median round p99 in microseconds.

| Server | Go client p99 | Tonic client p99 |
| --- | ---: | ---: |
| grpc-lite | 82.986 | 91.332 |
| Tonic | 117.120 | 84.899 |
| gRPC-Go | 145.664 | 144.763 |
| gRPC C++ | 136.297 | 151.476 |

Latency ordering depends on the client. Multi-process latency percentiles are
intentionally null; independent percentiles cannot be aggregated.

## Reproduction

Build grpc-lite at the recorded commit. `mise run prepare-dependencies`
successfully verified all 17 packages. Direct network bootstrap initially
produced hash mismatches; use the verified canonical archives unchanged:

```sh
mkdir -p .zig-cache/bench-global/p
cp .zig-cache/dependencies/p/*.tar.gz .zig-cache/bench-global/p/
export ZIG_GLOBAL_CACHE_DIR="$PWD/.zig-cache/bench-global"
mise run bootstrap
mise exec -- zig build streaming-soak-peer -Doptimize=ReleaseFast
```

Build upstream gRPC v1.84.3 with its pinned submodules, Ninja,
`CMAKE_BUILD_TYPE=Release`, `CMAKE_CXX_STANDARD=17`, `gRPC_INSTALL=ON`,
`gRPC_BUILD_TESTS=OFF`, and a private `CMAKE_INSTALL_PREFIX`.
Use the prefix's `protoc` and `grpc_cpp_plugin` to regenerate benchmark sources.
Both `lib/pkgconfig` and `lib64/pkgconfig` must be on `PKG_CONFIG_PATH`.
The static C++ benchmark link used:

```sh
read -r -a flags <<<"$(pkg-config --cflags grpc++ protobuf)"
read -r -a libs <<<"$(pkg-config --static --libs grpc++ protobuf libsystemd)"
c++ -std=c++17 -O3 -DNDEBUG -pthread "${flags[@]}" \
  -I cpp-server/generated cpp-server/server.cc \
  cpp-server/generated/benchmark.pb.cc \
  cpp-server/generated/benchmark.grpc.pb.cc \
  -o bin/cpp-server -Wl,--start-group "${libs[@]}" -Wl,--end-group
```

The plain `build.sh` link does not include all static-library dependencies;
the explicit link above was necessary for this installation.

```sh
export GRPC_LITE_SERVER=/absolute/path/to/grpc-lite-interop-server
export GRPC_LITE_VERSION=grpc-lite/a20631f
export GRPC_CPP_VERSION=grpc-cpp/1.84.3
export SERVER_CPU=15 ROUNDS=3 WARMUP=2s DURATION=10s
CLIENT_CPUS=8-14 RUN_ID=new-single-client ./sweep.sh
CLIENT_PROCESSES=2 CLIENT_CPU_SETS="1-7 8-14" CONNECTIONS=8 \
  CONCURRENCY_LEVELS="64 256 512" RUN_ID=new-multi-client ./sweep.sh
go run ./scripts/summarize.go results/sweeps/new-multi-client/*.jsonl
```

## Limits

No TLS, streaming, cross-host network, multicore server, or alternate C++ API
measurement. CPU governor, interrupts, background services and thermal state
were not controlled. No same-host old-version rerun was made, so these results
do not establish an improvement or regression against historical results.
