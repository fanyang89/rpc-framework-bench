# Cross-Framework RPC Benchmark

A reproducible Linux benchmarking suite evaluating **8 production RPC server stacks** across wire protocols, latency, concurrency, multicore scaling, large & asymmetric payloads, certificate-verified TLS, and gRPC streaming.

<p align="center">
  <strong>8 RPC Stacks</strong> &nbsp;·&nbsp;
  <strong>780 Formal Runs</strong> (+260 Smoke) &nbsp;·&nbsp;
  <strong>523.2M Client-Reported Completions</strong> &nbsp;·&nbsp;
  <strong>246.5M Companion-Verified Unary RPCs</strong> &nbsp;·&nbsp;
  <strong>10 Benchkit Charts (PNG + PDF)</strong>
</p>

<p align="center">
  <a href="#executive-scorecard">Scorecard</a> &nbsp;·&nbsp;
  <a href="#key-visualizations">Visualizations</a> &nbsp;·&nbsp;
  <a href="#benchmarked-stacks-and-environment">Stacks &amp; Hardware</a> &nbsp;·&nbsp;
  <a href="#experimental-design-and-fairness">Methodology</a> &nbsp;·&nbsp;
  <a href="#reproducing-the-benchmark">Reproduce</a> &nbsp;·&nbsp;
  <a href="#threats-to-validity">Caveats</a> &nbsp;·&nbsp;
  <a href="report/README.md"><strong>Full Report →</strong></a>
</p>

> [!TIP]
> **Want all 10 Benchkit figures, full data tables, and workload-specific selection advice?**  
> Read the complete curated performance report in **[`report/README.md`](report/README.md)**. Raw machine-readable CSV/JSONL outputs, 465 companion Go validation replays, and SHA-256 binary provenance live under [`results/2026-11-26-h2load-0f6c850/`](results/2026-11-26-h2load-0f6c850/README.md).

---

## Executive Scorecard

All values below are **medians of 3 × 5 s formal runs** on an **AMD Ryzen 9 9955HX** (16 physical cores, `performance` governor, localhost TCP). For gRPC unary rows, **RPC/s means HTTP/2 completions per second** reported by `h2load` (`nghttp2/1.69.0`, `completion_scope: "http2"`). MiB/s uses that rate times the configured request and response payload sizes. Every unary `h2load` run is paired with an archived companion `bin/bench-client` replay verifying gRPC `OK` and response payload lengths on its own replay calls (`465` formal files, `246.5M` verified unary RPCs, `0` errors), while timed `h2load` rows themselves remain HTTP/2 completion counts. Native and streaming clients count application-checked RPCs inline.

| RPC Stack | Protocol | 1 KiB Latency P50 / P99 (`C=1`, 1c) | 8-Core Plaintext `-m 1` (`C=128`, P99) | 1 MiB Plaintext (`C=32`, 1c) | Multiplexed `h2load` 8c PT / TLS (`1 KiB`) | Server-Stream 1c PT / TLS (`16×1 KiB`, Tonic cli) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| **grpc-lite** (`0f6c850`, Zig) | gRPC / HTTP/2 | 16.0 / 21.0 µs | **991k** (**201 µs**) | 2,778 MiB/s | **2.52M / 2.16M** | 48.9k / 35.1k |
| **Apache Thrift** (`0.25.0`) | Binary / Framed | **15.5 / 19.9 µs** | 983k (231 µs) | 4,424 MiB/s | — | — |
| **Cap'n Proto** (`1.5.0`) | Two-party RPC | 17.5 / 22.8 µs | 759k (993 µs) | **6,380 MiB/s*** | — | — |
| **brpc** (`1.18.0`) | `baidu_std` | 20.7 / 27.3 µs | 633k (258 µs) | 3,892 MiB/s | — | — |
| **Tonic** (`0.14.6`, Rust) | gRPC / HTTP/2 | 22.0 / 28.0 µs | 510k (466 µs) | 2,868 MiB/s | 599k / 548k | **54.5k / 43.8k** |
| **gRPC C++ callback** (`1.84.3`) | gRPC / HTTP/2 | 24.0 / 36.0 µs | 432k (1,270 µs) | 3,011 MiB/s | 566k / 524k | 8.5k / 7.7k |
| **gRPC-Go** (`1.83.2`) | gRPC / HTTP/2 | 22.0 / 44.0 µs | 406k (1,270 µs) | 3,347 MiB/s | 455k / 451k | 37.1k / 29.8k |
| **gRPC C++ sync** (`1.84.3`) | gRPC / HTTP/2 | 27.0 / 33.0 µs | 400k (731 µs) | 3,065 MiB/s | 367k / 334k | 8.6k / 7.8k |

*Note: Compare stacks **within each column/matrix** only — Part 1 uses strict 1-connection-per-worker (`-m 1`), Part 2 uses multiplexed HTTP/2 (`-m 16`), and Part 3 uses Go/Tonic streaming clients. Non-HTTP/2 stacks (`—`) are evaluated in Part 1 only. \*`Cap'n Proto` 1 MiB showed a bimodal 3-round spread (`50.4%`: `3,531–6,744 MiB/s`).*

### Key Takeaways

- **HTTP/2 + gRPC can match raw binary protocols on latency & 8-core throughput:** Freed from blocking C++ gRPC client overhead (`~14 µs`), **`grpc-lite` hits `16.0 µs` P50** (within `0.5 µs` of Apache Thrift at `15.5 µs`) and scales **`7.49×` to `991k RPC/s`** (`201 µs` P99) on 8 cores even with multiplexing disabled (`-m 1`).
- **Multiplexed gRPC throughput:** `grpc-lite` delivers **1.72M plaintext / 1.45M TLS RPC/s** in the 4-core configuration. In the 8-core configuration it reaches **2.52M / 2.16M RPC/s** (`<1%` 3-round spread), **4.2× / 3.95×** Tonic's throughput under the same load settings.
- **No single winner across every workload:**
  - **Bulk plaintext & non-gRPC binary:** **Apache Thrift** leads 64 KiB (`5,276 MiB/s`) with steady 1 MiB throughput (`4,424 MiB/s`, `15.4 ms` P99), while **Cap'n Proto** posts the highest 1 MiB median (`6,380 MiB/s`, bimodal spread).
  - **Multiplexed gRPC large payloads (64 KiB / 1 MiB):** **gRPC C++ (callback)** leads bandwidth (`3,422 / 2,325 MiB/s` @ 64 KiB; `3,539 / 2,237 MiB/s` @ 1 MiB), while **`grpc-lite`** delivers **4×–9× tighter P99** (`8.5–13.9 ms` @ 64 KiB; `34.0–52.1 ms` @ 1 MiB).
  - **Server-streaming & large uploads:** **Tonic (Rust)** leads server-streaming across all 4 client/transport cells (`54.5k` PT / `43.8k` TLS) and dominates `64 KiB → 64 B` uploads (`57.5k` PT / `38.8k` TLS).
  - **Zero TLS penalty in Go:** **gRPC-Go (`1.83.2`)** maintains ~99%–100% TLS parity on 1–8 cores (`455k` PT vs `451k` TLS @ 8c) and leads Go-client bidi ping-pong (`5.84k / 5.45k`).

---

## Key Visualizations

All figures are generated deterministically via [Benchkit](https://github.com/open-s4c/benchkit) (`benchkit.lwchart`) with colorblind-safe palettes and exact observed min–max variance bands/whiskers.

### 1. Neutral High-Load gRPC Multicore Scaling (`h2load`, Plaintext & TLS)
![Neutral Third-Party High-Load gRPC Scaling via h2load](report/figures/07-grpc-h2load-multicore-scaling.png)

### 2. Cross-Protocol 8-Core Scaling Across All 8 Frameworks (`-m 1`, Plaintext)
![Multicore Scaling Across 8 RPC Frameworks](report/figures/04-native-multicore-scaling.png)

### 3. Single-Request Latency Across All 8 Frameworks (`0 B / 64 B / 1 KiB`, `C = 1`)
![Single-Request Latency Across 8 RPC Frameworks](report/figures/01-native-latency.png)

> See **[`report/README.md`](report/README.md)** for all 10 Benchkit figures, including concurrency saturation & log-scale P99, symmetric 64 KiB / 1 MiB bandwidth (PT & TLS), asymmetric `64 B ↔ 64 KiB` directionality, and client/server/bidi streaming.

---

## Benchmarked Stacks and Environment

### Software Matrix

| Stack | Role | Version / Commit | Wire / TLS Stack | Concurrency Model |
| --- | --- | --- | --- | --- |
| **`h2load`** | Neutral gRPC Unary client | `nghttp2/1.69.0` | HTTP/2 (`h2c` / `h2`), OpenSSL 4.0.3 | Multi-threaded `libev` event loops |
| **grpc-lite** | gRPC server | `0f6c850` (Zig `0.17.0`) | HTTP/2 + OpenSSL 4.0.3 | Per-core `io_uring` reactors (`ReleaseFast`) |
| **Tonic** | gRPC server & streaming client | `0.14.6` (Rust `1.100.0`) | HTTP/2 (`hyper`/`h2`) + `ring` | Tokio `1.53.1` multi-thread runtime (`--locked`) |
| **gRPC C++** | gRPC server (`callback` & `sync`) | `1.84.3` (GCC `16.2.1`) | HTTP/2 + BoringSSL/OpenSSL | `SERVER_THREADS`-tuned CQs / `ResourceQuota` |
| **gRPC-Go** | gRPC server, streaming & unary validation client | `1.83.2` (Go `1.26.2`) | HTTP/2 + Go `crypto/tls` | Goroutines with matched `GOMAXPROCS` |
| **Apache Thrift** | Native server & client | `0.25.0` (GCC `16.2.1`) | Framed binary TCP | Nonblocking I/O worker threads |
| **Cap'n Proto** | Native server & client | `1.5.0` (GCC `16.2.1`) | Two-party RPC over TCP | Pre-bound `SO_REUSEPORT` loops (`TCP_NODELAY`) |
| **brpc** | Native server & client | `1.18.0` (GCC `16.2.1`) | `baidu_std` over TCP | `bthread` M:N work-stealing scheduler |

### Testbed Hardware

| Parameter | Specification |
| --- | --- |
| **CPU** | AMD Ryzen 9 9955HX — 16 physical cores / 32 threads (`x86_64`, 1 socket, 1 NUMA node) |
| **Caches** | 768 KiB L1d · 512 KiB L1i · 16 MiB L2 · 64 MiB L3 (2 × 32 MiB CCDs) |
| **OS & Frequency** | Fedora Linux 45, `performance` scaling governor verified on CPUs `0–15` |
| **Transport** | Localhost IPv4 TCP (`127.0.0.1`), no compression, no retries, no request logging |
| **TLS Setup** | Local CA (`bin/certs/ca.pem`) + `localhost` SAN certificate; stack-default TLS 1.3/1.2 & ciphers |

---

## Experimental Design and Fairness

Benchmark suites are easy to distort unintentionally. This suite enforces six structural safeguards verified by automated unit and harness tests:

1. **Neutral Third-Party Load Generator (`h2load`):** No gRPC server is benchmarked with its own client. Using off-the-shelf `h2load` (`nghttp2/1.69.0`) eliminates the ~`14 µs` client RTT penalty of blocking C++ gRPC and the 8-core client CPU ceiling (`780%/800%`) of Go/Tonic load generators, saturating all 5 gRPC servers to **92%–100% server core CPU**.
2. **Strict Topology Parity Across Protocols:** In the 8-framework comparison (Part 1), `h2load` is locked to **`-m 1`** (1 in-flight RPC per TCP connection) so connection count and concurrency (`C = 1, 8, 32, 128`) match the native C++ Thrift, Cap'n Proto, and brpc clients apples-to-apples. HTTP/2 multiplexing (`-m 16`) is evaluated separately in Part 2.
3. **Physical Core Isolation & Asymmetric Headroom:** Every campaign asserts via `/sys/devices/system/cpu/*/topology/` that CPUs `0–15` are 16 distinct physical cores (no SMT sibling sharing). Part 1 & 3 pin clients to `0–7` and servers to `15` / `12–15` / `8–15`; Part 2 allocates **12 client cores (`0–11`)** for `1 / 2 / 4` server cores and `8` client cores (`0–7`) for `8` server cores. Server execution order rotates across rounds.
4. **Two levels of unary validation:** `curl` checks one response before timing. `h2load` reports HTTP status and stream completion during timing (`completion_scope: "http2"`). Both unary runners then replay every workload with `bin/bench-client`, matching CPU placement, connections, streams per connection, payload sizes, TLS mode, warmup and duration (`465` formal replays archived in [`results/2026-11-26-h2load-0f6c850/`](results/2026-11-26-h2load-0f6c850/README.md), `246.5M` verified unary RPCs, `0` errors). The Go client requires one unary message of the requested payload length and final gRPC `OK` on every call. Its achieved rate can differ from `h2load`'s. Only the replay's own calls are verified.
5. **Unbiased Timing & Zero-Allocation Histograms:** All built-in clients (`go/client`, `rust-tonic`, `native-cpp/load.h`) connect eagerly, synchronize workers on a post-warmup monotonic barrier, compute unbiased per-worker active-time throughput `sum(completed_i / active_seconds_i)`, and record latencies in fixed-memory O(1) log-linear histograms (`<0.4%` error, exact `<512 ns`) to avoid post-run sort pauses.
6. **Transparent Variance & Full Provenance:** Every formal configuration runs **3 × 5 s** rounds after warmup (preceded by a full 260-case `--smoke` gate). All tables report medians and every Benchkit chart renders full observed min–max bounds (median three-round spread: **`0.99%`** Part 1, **`1.32%`** Part 2, **`0.85%`** Part 3). Binary SHA-256 manifests, `lscpu`, compiler versions, and CPU governors are archived alongside each run.

---

## Threats to Validity

- **Localhost loopback only:** Measures end-to-end userspace + kernel loopback stack cost; excludes physical NIC latency, packet loss, switch queueing, and cross-host MTU effects.
- **Closed-loop load generation:** Measures peak sustainable throughput and queueing latency at fixed concurrency, not open-loop coordinated-omission-corrected tail latency under fixed arrival rates.
- **Part 2 multicore comparisons:** The 1- and 2-core configurations use 48 connections; the 4- and 8-core configurations use 96. From 4 to 8 server cores, the client allocation drops from 12 cores to 8. Frameworks share the same settings within each configuration. Cross-configuration gains therefore do not isolate server-core scaling, and the smaller 4-to-8-core gain cannot be attributed to temperature alone.
- **Protocol scope boundaries:** TLS, HTTP/2 multiplexing, and gRPC streaming apply to the 5 gRPC stacks only (Thrift, Cap'n Proto, and brpc are measured in plaintext unary only). `h2load` does not support gRPC streaming, so Part 3 uses Go and Tonic clients.

---

## Reproducing the Benchmark

### Prerequisites

- Linux with `/proc`, `taskset`, `getconf`, GNU `date`, `jq`, `openssl`, `curl` (HTTP/2), and **`h2load` (`nghttp2`)**
- Go `1.26+`, Rust/Cargo (matching `workloads/rust-tonic/Cargo.lock`), C++17 compiler, CMake, Ninja
- gRPC C++ & Protobuf CMake packages (and for `workloads/native-cpp/`: brpc, Thrift, Cap'n Proto — see [`workloads/native-cpp/README.md`](workloads/native-cpp/README.md))
- External `grpc-lite` interop server binary (`GRPC_LITE_SERVER`)
- Python `3.12+` with `benchkit`, `pandas`, `matplotlib`, and `seaborn` for Benchkit orchestration and plotting

### 1. Build & Run Validation Tests

```sh
CMAKE_PREFIX_PATH=/path/to/grpc-install ./harness/build.sh
bash harness/certificates.sh

# Unit, race, parameter, and TLS trust/hostname rejection tests
go test -race ./...
cargo test --locked --manifest-path workloads/rust-tonic/Cargo.toml
c++ -std=c++17 -pthread workloads/native-cpp/load_test.cc -o /tmp/rpc-load-test && /tmp/rpc-load-test
bash harness/tests/run-validation-test.sh
python3 harness/tests/tls-validation-test.py
```

### 2. Run the 3 Benchmark Matrices

Output directories must not already exist. Add `--smoke` to any campaign script for a fast end-to-end validation pass before formal measurement.

Both unary runners require a freshly built `bin/bench-client`. A failed replay aborts the run. Part 1 stores replay counts in each run's `validation.json`; Part 2 stores them in `logs/*.validation.json`, referenced by `validation_file` in `results.jsonl`. Replay counts and timings do not enter the throughput tables.

```sh
export GRPC_LITE_SERVER=/absolute/path/to/grpc-lite-interop-server
export GRPC_LITE_VERSION=grpc-lite/0f6c850-openssl
export GRPC_CPP_VERSION=grpc-cpp/1.84.3

# Part 1: 8-framework cross-protocol Plaintext (native C++ + h2load -m 1; 360 formal runs)
MPLBACKEND=Agg /path/to/benchkit-venv/bin/python harness/campaign_native.py \
  --no-graphs --output results/sweeps/native-measured

# Part 2: Multiplexed gRPC Plaintext & TLS via h2load (1/2/4/8c, 64 KiB, 1 MiB, asymmetric; 240 formal runs)
python3 harness/campaign_h2load.py --output results/sweeps/h2load-measured

# Part 3: gRPC Streaming via Go & Tonic clients (client/server/bidi, PT & TLS; 180 formal runs)
python3 harness/campaign_extended.py --streaming-only --output results/sweeps/streaming-measured
```

### 3. Regenerate All 10 Benchkit Charts (PNG + Vector PDF)

```sh
/path/to/benchkit-venv/bin/python harness/plot_report.py \
  --dataset results/2026-11-26-h2load-0f6c850 \
  --output report/figures
```

---

## Repository Structure

<details>
<summary>Click to expand full directory layout</summary>

```text
rpc-framework-bench/
├── proto/                          # Shared gRPC wire schema (grpc.testing.TestService)
├── workloads/                      # Benchmark servers, clients, and native protocol adapters
│   ├── go/                         # gRPC-Go load generator (client/) and server (server/)
│   ├── rust-tonic/                 # Rust Tonic server and load generator (Cargo project)
│   ├── cpp-grpc/                   # gRPC C++ server supporting sync & callback APIs
│   └── native-cpp/                 # Closed-loop C++ adapters (brpc, Thrift, Cap'n Proto, gRPC)
├── harness/                        # Build, execution, validation, and Benchkit plotting scripts
│   ├── build.sh                    # Builds Go, Rust Tonic, and C++ gRPC binaries into bin/
│   ├── run.sh                      # Controlled single-matrix runner with CPU pinning & telemetry
│   ├── sweep.sh                    # Concurrency saturation sweep driver
│   ├── campaign_native.py          # Benchkit 8-framework Plaintext driver (native C++ + h2load -m 1)
│   ├── campaign_h2load.py          # 240-run neutral h2load (nghttp2) multiplexed gRPC Unary driver
│   ├── campaign_extended.py        # Extended / streaming-only gRPC driver (Go & Tonic clients)
│   ├── unary_validation.py         # Companion bin/bench-client gRPC unary replay validator
│   ├── plot_report.py              # Generates all PNG/PDF report figures via Benchkit lwchart
│   ├── certificates.sh             # Generates local CA and localhost SAN TLS certificates
│   ├── summarize/                  # Go JSONL summarizer (summarize.go) + unit tests
│   └── tests/                      # Parameter validation & TLS trust/hostname rejection tests
├── report/                         # Human-readable performance report and Benchkit figures
│   ├── README.md                   # Complete report with all 10 charts, tables, and selection guide
│   └── figures/                    # 10 publication-grade PNG + PDF Benchkit charts
└── results/                        # Raw benchmark datasets
    ├── README.md                   # Dataset index
    └── 2026-11-26-h2load-0f6c850/  # Authoritative 780-run formal dataset + 465 unary validation replays
```

</details>

---

## References

- [Official gRPC Benchmarking Guide](https://grpc.io/docs/guides/benchmarking/)
- [Benchkit Benchmarking Framework](https://github.com/open-s4c/benchkit)
- [nghttp2 / `h2load` Documentation](https://nghttp2.org/documentation/h2load-howto.html)
- [gRPC-Go Benchmark Sources](https://github.com/grpc/grpc-go/tree/master/benchmark)
- [Tonic 0.14.6 Documentation](https://docs.rs/tonic/0.14.6/tonic/)
