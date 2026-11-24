# Benchmark Results Index

All raw datasets are preserved here. For the curated, human-readable analysis and Benchkit visualizations of the latest formal benchmark suite, see [`report/README.md`](../report/README.md).

## Latest Formal Campaign

| Dataset | Date | Runs (Formal) | Total RPCs | Scope |
| --- | --- | ---: | ---: | --- |
| [`2026-11-21-full-af19148/`](2026-11-21-full-af19148/README.md) | 2026-11-21 | 906 | 563,735,713 | Complete 3-matrix campaign on `grpc-lite af19148` (OpenSSL 4.0.3): (1) 8-framework native plaintext comparison via Benchkit, (2) 5-server extended gRPC matrix (Plaintext & TLS, 1/4/8 cores, unary + streaming), (3) `grpc-lite` TLS backend evolution (`a20631f` mbedTLS vs `af19148` mbedTLS 3.6.6 vs `af19148` OpenSSL 4.0.3) |

## Historical Archives (`archive/`)

| Dataset | Date | Description |
| --- | --- | --- |
| [`archive/2026-11-20-grpc-lite-af19148/`](archive/2026-11-20-grpc-lite-af19148/README.md) | 2026-11-20 | A/B comparison of `grpc-lite de94bc0` (wolfSSL) vs `af19148` (OpenSSL 4.0.3), 258 measured runs |
| [`archive/2026-11-16-benchkit-native/`](archive/2026-11-16-benchkit-native/README.md) | 2026-11-16 | Prior 8-framework Benchkit native comparison (`grpc-lite a20631f`), 360 measured runs |
| [`archive/2026-11-16-extended/`](archive/2026-11-16-extended/README.md) | 2026-11-16 | Prior 5-server extended gRPC TLS/streaming/multicore matrix (`grpc-lite a20631f` mbedTLS), 420 measured runs |
| [`archive/2026-11-16-unary-a20631f-grpc1843/`](archive/2026-11-16-unary-a20631f-grpc1843/README.md) | 2026-11-16 | Single-core unary concurrency sweep with gRPC C++ 1.84.3 and `grpc-lite a20631f` |
| [`archive/2026-07-27-single-core-unary-capacity-c2d05b8/`](archive/2026-07-27-single-core-unary-capacity-c2d05b8/README.md) | 2026-07-27 | Early two-process single-core unary capacity run (`grpc-lite c2d05b8`, gRPC C++ 1.48.4) |
| [`archive/2026-07-27-single-core-unary-capacity/`](archive/2026-07-27-single-core-unary-capacity/README.md) | 2026-07-27 | Early two-process single-core unary capacity baseline (`grpc-lite e24e5a2`) |
| [`archive/2026-07-27-single-core-unary-tonic-client/`](archive/2026-07-27-single-core-unary-tonic-client/README.md) | 2026-07-27 | Early low-concurrency (C=4) latency matrix with Tonic client |
| [`archive/2026-07-27-single-core-unary-go-client/`](archive/2026-07-27-single-core-unary-go-client/README.md) | 2026-07-27 | Early low-concurrency (C=4) latency matrix with gRPC-Go client |

## Exploratory Spot Checks (`archive/spot-checks/`)

Preserved for provenance only; superseded by [`2026-11-21-full-af19148/`](2026-11-21-full-af19148/):

- `2026-11-21-openssl-spot-af19148/`: Quick OpenSSL spot check & binary hash manifest (`sha256.txt`)
- `2026-11-21-mbedtls-baseline-vs-openssl/`: Quick 12-run mbedTLS vs OpenSSL check
- `2026-11-21-cross-framework-af19148/`: Interrupted native campaign (8/360 runs before full rerun)
- `2026-11-21-grpc-lite-tls-af19148/`: Unexecuted TLS comparison script template
