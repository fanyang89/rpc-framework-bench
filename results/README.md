# Benchmark Results Index

## Current Dataset

| Dataset | Status | Date | Scope |
| --- | --- | --- | --- |
| [`2026-11-26-h2load-0f6c850/`](2026-11-26-h2load-0f6c850/README.md) | **Latest** | 2026-11-26 | 780 formal + 260 smoke runs; 523.2M client-reported completions/RPCs (`0` errors) plus 465 formal companion Go `bin/bench-client` gRPC unary validation replays (246.5M verified unary RPCs, `0` errors). `h2load` counts HTTP/2 completions for gRPC unary (`-m 1` and multiplexed plaintext/TLS); native and Go/Tonic streaming clients count checked RPCs. Featured in [`report/README.md`](../report/README.md). |

> Earlier pre-fix datasets, the client-limited Go/Tonic/`grpc-native` baseline, and the 2026-11-25 run prior to companion unary validation have been removed from the working tree so this directory contains only the latest validated `h2load` dataset.
