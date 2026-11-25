# Benchmark Results Index

## Current Valid Post-Fix Campaigns

| Dataset | Status | Date | Scope |
| --- | --- | --- | --- |
| [`2026-11-25-full-0f6c850/`](2026-11-25-full-0f6c850/README.md) | **VALID (Current)** | 2026-11-25 | Post-fix 2-matrix campaign (780 formal + 260 smoke runs, 474.0M RPCs, 0 errors; `grpc-lite 0f6c850` OpenSSL 4.0.3, `grpc-go 1.83.2`, `tonic 0.14.6`, `grpc-cpp 1.84.3`, `Thrift 0.25.0`, `Cap'n Proto 1.5.0`, `brpc 1.18.0`). Featured in [`report/README.md`](../report/README.md). |

---

## Archived & Invalidated Historical Datasets (`archive/`)

> [!WARNING]
> **All datasets under `archive/` (pre-`2026-11-25`) are ARCHIVED & MARKED INVALIDATED（已作废）.**  
> They were collected before fixing material benchmark implementation defects (Tonic `server-stream` `mpsc::channel(1)` + `tokio::spawn`, `cpp-server` ignoring `SERVER_THREADS`, Cap'n Proto `SO_REUSEPORT` startup race, `load.h` straggler denominator & missing post-warmup barrier, client latency-sort CPU skew, and per-RPC payload allocations).  
> Preserved strictly for provenance and regression auditing — **do not cite these numbers as current performance results.**

| Dataset | Status | Date | Scope |
| --- | --- | --- | --- |
| [`archive/2026-11-21-full-af19148/`](archive/2026-11-21-full-af19148/README.md) | **INVALIDATED (作废)** | 2026-11-21 | Pre-fix 3-matrix campaign (906 runs, `grpc-lite af19148` OpenSSL 4.0.3 + 8-framework native + extended gRPC + TLS evolution) |
| [`archive/2026-11-20-grpc-lite-af19148/`](archive/2026-11-20-grpc-lite-af19148/README.md) | **INVALIDATED (作废)** | 2026-11-20 | Pre-fix A/B comparison of `grpc-lite de94bc0` (wolfSSL) vs `af19148` (OpenSSL 4.0.3), 258 runs |
| [`archive/2026-11-16-benchkit-native/`](archive/2026-11-16-benchkit-native/README.md) | **INVALIDATED (作废)** | 2026-11-16 | Pre-fix 8-framework Benchkit native comparison (`grpc-lite a20631f`), 360 runs |
| [`archive/2026-11-16-extended/`](archive/2026-11-16-extended/README.md) | **INVALIDATED (作废)** | 2026-11-16 | Pre-fix 5-server extended gRPC TLS/streaming/multicore matrix (`grpc-lite a20631f` mbedTLS), 420 runs |
| [`archive/2026-11-16-unary-a20631f-grpc1843/`](archive/2026-11-16-unary-a20631f-grpc1843/README.md) | **INVALIDATED (作废)** | 2026-11-16 | Pre-fix single-core unary concurrency sweep (`grpc-lite a20631f`, gRPC C++ 1.84.3) |
| [`archive/2026-07-27-single-core-unary-capacity-c2d05b8/`](archive/2026-07-27-single-core-unary-capacity-c2d05b8/README.md) | **INVALIDATED (作废)** | 2026-07-27 | Pre-fix early two-process single-core unary capacity run (`grpc-lite c2d05b8`, gRPC C++ 1.48.4) |
| [`archive/2026-07-27-single-core-unary-capacity/`](archive/2026-07-27-single-core-unary-capacity/README.md) | **INVALIDATED (作废)** | 2026-07-27 | Pre-fix early two-process single-core unary capacity baseline (`grpc-lite e24e5a2`) |
| [`archive/2026-07-27-single-core-unary-tonic-client/`](archive/2026-07-27-single-core-unary-tonic-client/README.md) | **INVALIDATED (作废)** | 2026-07-27 | Pre-fix early low-concurrency (C=4) latency matrix with Tonic client |
| [`archive/2026-07-27-single-core-unary-go-client/`](archive/2026-07-27-single-core-unary-go-client/README.md) | **INVALIDATED (作废)** | 2026-07-27 | Pre-fix early low-concurrency (C=4) latency matrix with gRPC-Go client |

## Archived Exploratory Spot Checks (`archive/spot-checks/`)

Also **INVALIDATED (作废)** and preserved for provenance only:

- `2026-11-21-openssl-spot-af19148/`: Quick OpenSSL spot check & binary hash manifest (`sha256.txt`)
- `2026-11-21-mbedtls-baseline-vs-openssl/`: Quick 12-run mbedTLS vs OpenSSL check
- `2026-11-21-cross-framework-af19148/`: Interrupted native campaign (8/360 runs)
- `2026-11-21-grpc-lite-tls-af19148/`: Unexecuted TLS comparison script template
