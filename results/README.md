# Benchmark Results Index

## Current Dataset

| Dataset | Status | Date | Scope |
| --- | --- | --- | --- |
| [`2026-11-25-h2load-0f6c850/`](2026-11-25-h2load-0f6c850/README.md) | **Latest** | 2026-11-25 | 780 formal + 260 smoke runs; 524.3M client-reported completions. `h2load` counts HTTP/2 completions for gRPC unary (`-m 1` and multiplexed plaintext/TLS); native and Go/Tonic streaming clients count checked RPCs. Featured in [`report/README.md`](../report/README.md). |

> Earlier pre-fix datasets and the Go/Tonic/`grpc-native` client-limited baseline have been removed from the working tree so this directory contains only the clean, neutral `h2load` benchmark dataset.
