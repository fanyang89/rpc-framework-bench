# Cross-Framework RPC Benchmark

A reproducible Linux benchmarking suite comparing **8 RPC server stacks** across wire protocols, concurrency levels, payload sizes, multicore CPU budgets, streaming cardinalities, and certificate-verified TLS:

- **gRPC stacks:** `grpc-lite` (Zig), `Tonic` (Rust), `gRPC-Go`, `gRPC C++` (sync & callback/reactor APIs)
- **Native binary RPC stacks:** `Apache Thrift`, `Cap'n Proto`, `brpc` (`baidu_std`)

> **Looking for the performance results and Benchkit charts?**  
> Read the full human-readable post-fix report in **[`report/README.md`](report/README.md)** (780 formal runs, 474.0M RPCs, 0 errors, 10 Benchkit PNG/PDF charts).  
> *(All pre-`2026-11-25` pre-fix datasets are archived & marked invalidated under [`results/archive/`](results/README.md).)*

---

## Latest Post-Fix Highlights (`2026-11-25`, `grpc-lite 0f6c850`)

| Benchmark Matrix | Scope | Headline Result (Median of 3 × 5 s Rounds) |
| --- | --- | --- |
| **Native Cross-Protocol (Benchkit)** | 8 frameworks, plaintext C++ clients, 360 runs | **Thrift** leads 1 KiB P50 (**15.5 µs**) and 8-core throughput (**984.2k RPC/s**); **grpc-lite** leads gRPC on 1-core latency (**29.8 µs**) and 1–8 core throughput (**134k–425k**); **Cap'n Proto** leads 1 MiB bandwidth (**7,166 MiB/s**, `11.1 ms` P99), while **Tonic** leads gRPC at 1 MiB (**4,214 MiB/s**). |
| **Extended gRPC Matrix** | 5 gRPC servers, Go (`1.83.2`) & Tonic (`0.14.6`) clients, Plaintext & TLS, streaming, 420 runs | **grpc-lite** (`0f6c850`, OpenSSL 4.0.3) leads 1-core 1 KiB unary in Plaintext (**435k / 407k**) and TLS (**346k / 272k**) — **3.4×–4.7×** Tonic; **Tonic** leads server-streaming (**44.8k / 54.2k** PT, **38.7k / 43.6k** TLS); **gRPC C++ callback** leads 64 KiB Tonic-client unary (**27.5k / 17.7k**). |

![Multicore Scaling Across 8 RPC Frameworks](report/figures/04-native-multicore-scaling.png)

![gRPC Streaming Performance: Plaintext vs TLS](report/figures/09-grpc-streaming-plaintext-vs-tls.png)

See **[`report/README.md`](report/README.md)** for all 10 Benchkit figures (latency, concurrency, P99 tail latency, payload bandwidth, asymmetric payloads, unary & multicore TLS, streaming, and 64 KiB unary), complete tables, bug-fix impact audit, and methodology limits.

---

## Repository Layout

```text
rpc-framework-bench/
├── proto/                          # Shared gRPC wire schema (grpc.testing.TestService)
├── workloads/                      # Benchmark servers, clients, and native protocol adapters
│   ├── go/                         # gRPC-Go load generator (client/) and server (server/)
│   ├── rust-tonic/                 # Rust Tonic server and load generator (Cargo project)
│   ├── cpp-grpc/                   # gRPC C++ server supporting sync & callback APIs
│   └── native-cpp/                 # Closed-loop C++ adapters (brpc, Thrift, Cap'n Proto, gRPC)
├── harness/                        # Build, execution, validation, and Benchkit reporting scripts
│   ├── build.sh                    # Builds Go, Rust Tonic, and C++ gRPC binaries into bin/
│   ├── run.sh                      # Controlled single-matrix runner with CPU pinning & telemetry
│   ├── sweep.sh                    # Concurrency saturation sweep driver
│   ├── campaign_native.py          # Benchkit 8-framework native plaintext campaign driver
│   ├── campaign_extended.py        # 420-run extended gRPC (TLS, streaming, multicore) driver
│   ├── plot_report.py              # Generates PNG/PDF report figures via Benchkit lwchart
│   ├── certificates.sh             # Generates local CA and localhost SAN TLS certificates
│   ├── summarize/                  # Go JSONL summarizer (summarize.go) + unit tests
│   └── tests/                      # Parameter validation & TLS trust/hostname rejection tests
├── report/                         # Human-readable performance report and Benchkit figures
│   ├── README.md                   # Curated post-fix report with tables, insights, and caveats
│   ├── figures/                    # 10 publication-grade PNG + PDF Benchkit charts
│   └── archive/                    # Archived & invalidated pre-fix reports and figures
└── results/                        # Raw benchmark datasets
    ├── README.md                   # Dataset index
    ├── 2026-11-25-full-0f6c850/    # Latest complete post-fix 780-run formal dataset
    └── archive/                    # Historical pre-fix datasets & spot checks (invalidated)
```

---

## Quick Start

### Requirements

- Linux with `/proc`, `taskset`, `getconf`, GNU `date`, `jq`, and `openssl`
- Go 1.26+, Rust/Cargo (matching `workloads/rust-tonic/Cargo.lock`), C++17 compiler, CMake, Ninja
- gRPC C++ & Protobuf CMake packages (and for `workloads/native-cpp/`: brpc, Thrift, Cap'n Proto — see [`workloads/native-cpp/README.md`](workloads/native-cpp/README.md))
- External `grpc-lite` interop server binary (`GRPC_LITE_SERVER`)
- Python 3.12+ with `benchkit`, `pandas`, `matplotlib`, and `seaborn` for Benchkit campaigns and report plotting

### 1. Build & Validate

```sh
CMAKE_PREFIX_PATH=/path/to/grpc-install ./harness/build.sh
bash harness/certificates.sh

# Run unit and harness validation tests
go test -race ./...
cargo test --locked --manifest-path workloads/rust-tonic/Cargo.toml
c++ -std=c++17 -pthread workloads/native-cpp/load_test.cc -o /tmp/rpc-load-test && /tmp/rpc-load-test
bash harness/tests/run-validation-test.sh
python3 harness/tests/tls-validation-test.py
```

### 2. Run Benchmark Campaigns

All formal campaigns require CPUs `0–15` to be distinct physical cores (clients pinned to `0–7`, servers pinned to `15`, `12–15`, or `8–15`). Output directories must not already exist.

```sh
export GRPC_LITE_SERVER=/absolute/path/to/grpc-lite-interop-server
export GRPC_LITE_VERSION=grpc-lite/0f6c850-openssl
export GRPC_CPP_VERSION=grpc-cpp/1.84.3

# Benchkit 8-framework native plaintext campaign (120 configs × 3 rounds)
MPLBACKEND=Agg /path/to/benchkit-venv/bin/python harness/campaign_native.py \
  --output results/sweeps/native-run

# Extended gRPC campaign (TLS, streaming, multicore; 420 formal runs)
python3 harness/campaign_extended.py --output results/sweeps/extended-run

# Single controlled run or concurrency sweep
CLIENT_IMPL=tonic ./harness/run.sh
RUN_ID=my-sweep bash ./harness/sweep.sh
go run ./harness/summarize/summarize.go results/sweeps/my-sweep/*.jsonl
```

Add `--smoke` to `campaign_native.py` or `campaign_extended.py` for a fast end-to-end validation pass before formal measurement.

### 3. Regenerate Benchkit Report Charts

```sh
/path/to/benchkit-venv/bin/python harness/plot_report.py \
  --dataset results/2026-11-25-full-0f6c850 \
  --output report/figures
```

---

## Methodology Principles

- **Synchronized warmup & unbiased throughput:** All three load generators (`go/client`, `rust-tonic`, `native-cpp/load.h`) connect eagerly, synchronize workers on a post-warmup monotonic barrier, and compute unbiased per-worker active-time throughput `sum(completed_i / active_seconds_i)`.
- **Zero-allocation O(1) latency histograms:** Clients record latencies in a fixed-memory log-linear histogram (8-bit mantissa, `<0.4%` error, exact below 512 ns) rather than growing and sorting multi-million-element arrays after measurement.
- **Fair thread budgets & payload handling:** All servers align concurrency/polling budgets with `SERVER_THREADS` / CPU affinity and avoid artificial per-RPC channel ping-pong or avoidable heap churn.
- **Zero-error gating & transparent variance:** Every client validates response lengths, streaming message counts, aggregate bytes, and terminal `OK` status; any error fails the run. Reports present round medians with observed min–max bounds.

## References

- [Official gRPC Benchmarking Guide](https://grpc.io/docs/guides/benchmarking/)
- [Benchkit Benchmarking Framework](https://github.com/open-s4c/benchkit)
- [gRPC-Go Benchmark Sources](https://github.com/grpc/grpc-go/tree/master/benchmark)
- [Tonic 0.14.6 Documentation](https://docs.rs/tonic/0.14.6/tonic/)
