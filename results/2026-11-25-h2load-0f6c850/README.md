# Full Neutral `h2load` RPC Comparison: `grpc-lite 0f6c850`

All **260 smoke runs** and **780 formal runs** passed their original checks. The formal runs recorded **524,313,126 client-reported completions** across three matrices. For `h2load` rows, `succeeded`/`completed` count HTTP/2 completions and RPC/s denotes completions per second. Only the preflight checked gRPC status and response length. `total_bytes` includes headers and framing; `data_bytes` includes warmup and exceeds `total_bytes` in 210 of the 240 multiplexed runs. The archive cannot determine the per-RPC success rate of timed unary requests. Native and streaming counts retain their application-level checks.

See [`report/README.md`](../../report/README.md) for the complete analysis and all 10 Benchkit PNG/PDF charts.

## Scope & Matrices

All three matrices ran sequentially on `AMD Ryzen 9 9955HX` (16 physical cores / 32 threads, `performance` governor on CPUs `0–15`, Linux x86_64, localhost TCP). Every formal configuration ran **3 repetitions × 5 seconds** after warmup.

| Matrix | Directory | Smoke Runs | Formal Runs | Client-reported completions | Client & Scope |
| --- | --- | ---: | ---: | ---: | --- |
| **Native + `h2load (-m 1)` Cross-Protocol** | `native-measured/` | 120 | 360 | 216,757,899 | 8 frameworks Plaintext: Thrift / Cap'n Proto / brpc on native C++ clients; all 5 gRPC servers on `h2load -m 1` (exact 1-conn/worker parity) |
| **Multiplexed gRPC via `h2load`** | `h2load-measured/` | 80 | 240 | 286,684,053 | 5 gRPC servers, Plaintext (`h2c`) & TLS (`h2`), `1/2/4` cores (12c cli) + `8` cores (8c cli) 1 KiB (`-m 16`), plus 1-core `64 KiB`, `1 MiB`, and asymmetric `64 B ↔ 64 KiB` |
| **gRPC Streaming** | `streaming-measured/` | 60 | 180 | 20,871,174 | 5 gRPC servers, Go (`1.83.2`) & Tonic (`0.14.6`) dual-process clients, Plaintext & TLS, `client-stream` / `server-stream` / `bidi` (`16 × 1 KiB`) |

## Headline Numbers (3-Round Medians)

- **1 KiB Latency (`C=1`, 1 core, `-m 1`):** `Apache Thrift` **15.6 µs** P50 (`21.3 µs` P99), **`grpc-lite` 16.0 µs P50 (`22.0 µs` P99)**, `Cap'n Proto` `17.6 µs`, `brpc` `20.9 µs`, `Tonic` `22.0 µs`, `gRPC-Go` `22.0 µs`, `gRPC C++ callback` `24.0 µs`, `gRPC C++ sync` `27.0 µs`.
- **8-Core Plaintext (`C=128`, 1 KiB, `-m 1`):** **`grpc-lite` #1 overall at `1,069,242 RPC/s` (`8.13×`, `159 µs` P99)**, ahead of `Apache Thrift` (`983,616`), `Cap'n Proto` (`771,516`), `brpc` (`637,172`), `Tonic` (`510,676`), `gRPC C++ callback` (`446,897`), `gRPC-Go` (`405,125`), and `gRPC C++ sync` (`396,806`).
- **Multiplexed `h2load` Multicore (all 5 gRPC servers at 92%–100% server CPU):**
  - **Plaintext (`1→2→4→8` cores):** `grpc-lite` **`457k → 900k → 1.80M → 2.48M`** (`2.86 ms` P99), `Tonic` `89k → 173k → 339k → 604k`, `C++ callback` `81k → 159k → 317k → 571k`, `gRPC-Go` `75k → 138k → 264k → 450k`, `C++ sync` `61k → 110k → 205k → 370k`.
  - **TLS (`1→2→4→8` cores):** `grpc-lite` **`415k → 819k → 1.53M → 2.18M`** (`1.35 ms` P99), `Tonic` `84k → 162k → 318k → 552k`, `C++ callback` `76k → 150k → 300k → 531k`, `gRPC-Go` `76k → 140k → 266k → 452k`, `C++ sync` `55k → 100k → 188k → 337k`.
- **Multiplexed `h2load` Large & Asymmetric (1 core, PT / TLS):** `C++ callback` leads 64 KiB (`3,431 / 2,329 MiB/s`) and 1 MiB (`3,595 / 2,262 MiB/s`); `grpc-lite` leads `64 B → 64 KiB` PT (`47.0k`) with tightest P99 across all large payloads; `Tonic` leads `64 KiB → 64 B` (`57.2k` PT / `38.6k` TLS).
- **Streaming (1 core, `16 × 1 KiB`, Go / Tonic):** `Tonic` leads server-streaming (`44.7k / 53.1k` PT, `39.1k / 43.9k` TLS); `grpc-lite` leads Go-client client-stream (`68.4k / 47.7k`) and Tonic-client bidi (`8.80k / 6.98k`).

## Provenance

- `grpc-lite` built from upstream `main` commit `0f6c850feb8e8cca6038fabe4aeca07af43cb832` with [`grpc-lite.patch`](grpc-lite.patch), Zig `0.17.0`, `-Doptimize=ReleaseFast -Dtls=true` (OpenSSL 4.0.3).
- `native-measured/environment.json`, `h2load-measured/environment.json`, and `streaming-measured/environment.json` record binary SHA-256 hashes, `h2load nghttp2/1.69.0`, `lscpu`, CPU governors, and compiler toolchains.
