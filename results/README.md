# Benchmark Results Index

## Current Dataset

| Dataset | Status | Date | Scope |
| --- | --- | --- | --- |
| [`2026-11-25-h2load-0f6c850/`](2026-11-25-h2load-0f6c850/README.md) | **VALID (Latest)** | 2026-11-25 | Full post-fix 3-matrix `h2load` campaign (780 formal + 260 smoke runs, 524.3M RPCs, 0 errors; Native 8-framework with `h2load -m 1` for gRPC + Multiplexed `h2load` 1/2/4/8c/64KiB/1MiB/asymmetric PT & TLS + Go/Tonic Streaming; `grpc-lite 0f6c850` OpenSSL 4.0.3). Featured in [`report/README.md`](../report/README.md). |

> Earlier pre-fix datasets and the Go/Tonic/`grpc-native` client-limited baseline have been removed from the working tree so this directory contains only the clean, neutral `h2load` benchmark dataset.
