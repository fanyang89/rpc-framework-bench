# Full Post-Fix RPC Comparison: `grpc-lite 0f6c850`

All 260 smoke runs and 780 formal runs passed on the post-fix benchmark suite. The formal runs completed **474,043,546 RPCs** with **zero RPC or validation errors** (`192,692,645` native + `281,350,901` extended).

See [`report/README.md`](../../report/README.md) for the full narrative analysis and all 10 Benchkit PNG/PDF charts.

## Scope & Matrices

Both matrices ran sequentially on the same host (`AMD Ryzen 9 9955HX`, 16 physical cores / 32 threads, `performance` governor on CPUs `0–15`, Linux x86_64, localhost TCP). Every formal configuration ran **3 repetitions × 5 seconds** after warmup.

| Matrix | Directory | Smoke Runs | Formal Runs | Completed RPCs | Scope |
| --- | --- | ---: | ---: | ---: | --- |
| **Native Cross-Protocol (Benchkit)** | `native-measured/` | 120 | 360 | 192,692,645 | 8 frameworks (`Apache Thrift 0.25.0`, `Cap'n Proto 1.5.0`, `brpc 1.18.0`, `grpc-lite 0f6c850`, `Tonic 0.14.6`, `gRPC C++ 1.84.3` sync & callback, `gRPC-Go 1.83.2`) |
| **Extended gRPC (Plaintext & TLS)** | `extended-measured/` | 140 | 420 | 281,350,901 | 5 gRPC server configs, Go (`1.83.2`) & Tonic (`0.14.6`) dual-process clients, Plaintext & TLS, 1/4/8 cores, 1 KiB & 64 KiB, unary + 3 streaming modes |

## Key Highlights (3-Round Medians)

- **Native 8-Core Plaintext (`1 KiB`, `C=128`):** `Apache Thrift` leads at **984,166 RPC/s** (`7.68×`, `231 µs` P99), followed by `Cap'n Proto` (`764,558`), `brpc` (`637,083`), `grpc-lite` (`425,078`, client-limited at `602%` client / `262%` server CPU), `gRPC C++ sync` (`374,192`, `6.00×`, `801 µs` P99), `gRPC C++ callback` (`343,594`), `Tonic` (`305,858`), and `gRPC-Go` (`283,320`).
- **Native 1 MiB Symmetric (`1 core`, `C=32`):** `Cap'n Proto` leads at **7,166 MiB/s** (`3,583 RPC/s`, `11.1 ms` P99), followed by `Apache Thrift` (`4,419 MiB/s`) and `Tonic` (`4,214 MiB/s`). `grpc-lite 0f6c850` reaches `3,022 MiB/s` (`25.7 ms` P99).
- **Extended 1-Core 1 KiB Unary (Go / Tonic):** `grpc-lite 0f6c850` (OpenSSL 4.0.3) leads Plaintext at **434,887 / 407,390 RPC/s** and TLS at **345,802 / 271,739 RPC/s** (`3.4×–4.7×` second-place `Tonic` at `92,307 / 88,298` PT and `84,370 / 79,381` TLS).
- **Extended 1-Core Server-Streaming (`16 × 1 KiB`, Go / Tonic):** `Tonic` leads overall at **44,817 / 54,196 RPC/s** (Plaintext) and **38,749 / 43,633 RPC/s** (TLS), ahead of `grpc-lite` (`44,386 / 47,725` PT, `37,288 / 34,609` TLS) and `gRPC-Go` (`38,717 / 36,724` PT, `31,358 / 29,389` TLS).
- **Extended 1-Core 64 KiB Unary (Go / Tonic):** `grpc-lite` leads Go-client Plaintext (`17,075`), while `gRPC C++ callback` leads Tonic-client Plaintext (`27,499`) and both TLS cells (`15,228 / 17,715`); `grpc-lite` hits `24,004` Tonic-client Plaintext.

## Provenance & Artifacts

- `grpc-lite` built from upstream `main` commit `0f6c850feb8e8cca6038fabe4aeca07af43cb832` with [`grpc-lite.patch`](grpc-lite.patch), Zig `0.17.0`, `-Doptimize=ReleaseFast -Dtls=true` (OpenSSL 4.0.3).
- `native-measured/environment.json` and `extended-measured/environment.json` capture binary SHA-256 hashes, `lscpu`, toolchain versions (`go1.26.2`, `rustc 1.100.0`, `GCC 16.2.1`), CPU affinity, and source diffs.
- `extended-measured/summary.tsv`, `extended-measured/benchkit-extended.csv`, `extended-measured/host-telemetry.jsonl.gz`, and `native-measured/benchmark_*.csv` hold the complete machine-readable outputs.
- Do **not** mix throughput directly across `native-measured/` and `extended-measured/`: they use different client stacks and connection topologies.
