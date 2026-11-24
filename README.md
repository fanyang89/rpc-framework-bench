# Cross-Framework RPC Benchmark

A reproducible Linux benchmarking suite comparing **8 RPC server stacks** across wire protocols, concurrency levels, payload sizes, multicore CPU budgets, streaming cardinalities, and certificate-verified TLS:

- **gRPC stacks:** `grpc-lite` (Zig), `Tonic` (Rust), `gRPC-Go`, `gRPC C++` (sync & callback/reactor APIs)
- **Native binary RPC stacks:** `Apache Thrift`, `Cap'n Proto`, `brpc` (`baidu_std`)

> **Looking for the performance results and Benchkit charts?**  
> Read the full human-readable report in **[`report/README.md`](report/README.md)** (906 formal runs, 563.7M RPCs, 0 errors, 11 Benchkit PNG/PDF charts).

---

## Latest Benchmark Highlights (`2026-11-21`, `grpc-lite af19148`)

| Benchmark Matrix | Scope | Headline Result (Median of 3 × 5 s Rounds) |
| --- | --- | --- |
| **Native Cross-Protocol (Benchkit)** | 8 frameworks, plaintext C++ clients, 360 runs | **Thrift** leads 1 KiB P50 (**15.5 µs**) and 8-core throughput (**992.6k RPC/s**); **grpc-lite** leads gRPC on 1-core latency (**30.0 µs**) and 1–8 core throughput (**136.5k–421.5k**); **Tonic** matches Thrift/Cap'n Proto at 1 MiB (**4,382 MiB/s**). |
| **Extended gRPC Matrix** | 5 gRPC servers, Go & Tonic clients, Plaintext & TLS, streaming, 420 runs | **grpc-lite** (OpenSSL 4.0.3) leads 1-core 1 KiB unary in both Plaintext (**434k / 399k**) and TLS (**347k / 266k**) — **4.1×–4.7×** Tonic; **gRPC C++ callback** leads 64 KiB with Tonic client (**28.7k / 19.5k**). |
| **`grpc-lite` TLS Evolution** | `a20631f` mbedTLS → `af19148` mbedTLS → `af19148` OpenSSL, 126 runs | **7.18× / 6.27×** faster on 1-core 1 KiB TLS unary (`48.3k → 346.9k` Go / `43.0k → 269.4k` Tonic); **4.64×–5.72×** faster at 64 KiB TLS. |

![Multicore Scaling Across 8 RPC Frameworks](report/figures/04-native-multicore-scaling.png)

![gRPC 1-Core Unary: Plaintext vs Certificate-Verified TLS](report/figures/07-grpc-unary-plaintext-vs-tls.png)

See **[`report/README.md`](report/README.md)** for all 11 Benchkit figures (latency, concurrency, P99 tail latency, payload bandwidth, asymmetric payloads, multicore TLS, streaming, and TLS backend evolution), complete data tables, and methodology limits.

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
│   ├── plot_report.py              # Generates all 11 PNG/PDF report figures via Benchkit lwchart
│   ├── certificates.sh             # Generates local CA and localhost SAN TLS certificates
│   ├── summarize/                  # Go JSONL summarizer (summarize.go) + unit tests
│   └── tests/                      # Parameter validation & TLS trust/hostname rejection tests
├── report/                         # Human-readable performance report and Benchkit figures
│   ├── README.md                   # Curated report with tables, insights, and caveats
│   └── figures/                    # 11 publication-grade PNG + PDF Benchkit charts
└── results/                        # Raw benchmark datasets and historical archives
    ├── README.md                   # Dataset index
    ├── 2026-11-21-full-af19148/    # Latest complete 906-run formal dataset
    └── archive/                    # Historical campaigns (2026-07 to 2026-11-20) & spot checks
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
bash harness/tests/run-validation-test.sh
python3 harness/tests/tls-validation-test.py
```

### 2. Run Benchmark Campaigns

All formal campaigns require CPUs `0–15` to be distinct physical cores (clients pinned to `0–7`, servers pinned to `15`, `12–15`, or `8–15`). Output directories must not already exist.

```sh
export GRPC_LITE_SERVER=/absolute/path/to/grpc-lite-interop-server
export GRPC_LITE_VERSION=grpc-lite/af19148-openssl
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
  --dataset results/2026-11-21-full-af19148 \
  --output report/figures
```

---

## Methodology Principles

- **Separate latency from capacity:** Low-concurrency runs (`C = 1–4`) measure end-to-end RTT and expose client-server stack affinity; capacity conclusions require checking both server CPU saturation and client headroom.
- **Zero-error gating:** Every client validates response lengths, streaming message counts, aggregate bytes, and terminal `OK` status; any error invalidates the run.
- **Honest multi-process aggregation:** Multi-process runs report aggregate throughput and server CPU while keeping merged latency percentiles `null` rather than incorrectly averaging independent histograms.
- **Transparent variance:** All tables report round medians and all Benchkit charts show the full observed min–max range across rounds.

## References

- [Official gRPC Benchmarking Guide](https://grpc.io/docs/guides/benchmarking/)
- [Benchkit Benchmarking Framework](https://github.com/open-sourcing/benchkit)
- [gRPC-Go Benchmark Sources](https://github.com/grpc/grpc-go/tree/master/benchmark)
- [Tonic 0.14.6 Documentation](https://docs.rs/tonic/0.14.6/tonic/)
