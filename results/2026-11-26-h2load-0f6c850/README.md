# Full Neutral `h2load` RPC Comparison: `grpc-lite 0f6c850`

All **260 smoke runs** and **780 formal runs** completed with **0 errors**, recording **523,239,246 client-reported completions/RPCs** across three matrices.

For all `h2load` unary rows (`completion_scope: "http2"`), `succeeded`/`completed` count HTTP/2 completions and RPC/s denotes HTTP/2 completions per second. Each `h2load` run is preceded by a `curl` preflight check (HTTP/2 `200`, `grpc-status: 0`, response payload length) and followed by a companion **`bin/bench-client` (Go `1.83.2`)** validation replay under matching CPU placement, connections, streams per connection, payload sizes, TLS mode, warmup, and duration: across all **465 formal unary `h2load` runs** (**225** in `native-measured/**/validation.json` and **240** in `h2load-measured/logs/*.validation.json`), the companion replays verified **246,487,488 unary RPCs** with **0 errors** (plus **155** smoke replays verifying **17,331,425** unary RPCs with 0 errors). Each replay verifies its own calls rather than the separate `h2load` stream directly, and its achieved rate can differ from `h2load`'s. Native C++ and Go/Tonic streaming clients verify every RPC inline during timing.

See [`report/README.md`](../../report/README.md) for the complete analysis and all 10 Benchkit PNG/PDF charts.

## Scope & Matrices

All three matrices ran sequentially on `AMD Ryzen 9 9955HX` (16 physical cores / 32 threads, `performance` governor on CPUs `0–15`, Linux x86_64, localhost TCP). Every formal configuration ran **3 repetitions × 5 seconds** after warmup.

| Matrix | Directory | Smoke Runs | Formal Runs | Client-Reported Completions | Companion Go Validations | Client & Scope |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| **Native + `h2load (-m 1)` Cross-Protocol** | `native-measured/` | 120 | 360 | 215,694,195 | 225 files (`79,271,066` RPCs, 0 err) | 8 frameworks Plaintext: Thrift / Cap'n Proto / brpc on native C++ clients (`completion_scope: "rpc"`); all 5 gRPC servers on `h2load -m 1` (`completion_scope: "http2"`, exact 1-conn/worker parity) |
| **Multiplexed gRPC via `h2load`** | `h2load-measured/` | 80 | 240 | 286,631,598 | 240 files (`167,216,422` RPCs, 0 err) | 5 gRPC servers, Plaintext (`h2c`) & TLS (`h2`), `1/2/4` cores (12c cli) + `8` cores (8c cli) 1 KiB (`-m 16`), plus 1-core `64 KiB`, `1 MiB`, and asymmetric `64 B ↔ 64 KiB` |
| **gRPC Streaming** | `streaming-measured/` | 60 | 180 | 20,913,453 | inline checked | 5 gRPC servers, Go (`1.83.2`) & Tonic (`0.14.6`) dual-process clients, Plaintext & TLS, `client-stream` / `server-stream` / `bidi` (`16 × 1 KiB`) |

## Headline Numbers (3-Round Medians)

- **1 KiB Latency (`C=1`, 1 core, `-m 1`):** `Apache Thrift` **15.5 µs** P50 (`19.9 µs` P99), **`grpc-lite` 16.0 µs P50 (`21.0 µs` P99)**, `Cap'n Proto` `17.5 µs`, `brpc` `20.7 µs`, `Tonic` `22.0 µs`, `gRPC-Go` `22.0 µs`, `gRPC C++ callback` `24.0 µs`, `gRPC C++ sync` `27.0 µs`.
- **8-Core Plaintext (`C=128`, 1 KiB, `-m 1`):** **`grpc-lite` #1 overall at `991,232 RPC/s` (`7.49×`, `201 µs` P99)**, ahead of `Apache Thrift` (`982,999`), `Cap'n Proto` (`759,179`), `brpc` (`633,357`), `Tonic` (`510,026`), `gRPC C++ callback` (`432,215`), `gRPC-Go` (`405,848`), and `gRPC C++ sync` (`399,737`).
- **Multiplexed `h2load` Multicore (all 5 gRPC servers at 92%–100% server core CPU):**
  - **Plaintext (`1→2→4→8` cores):** `grpc-lite` **`458k → 910k → 1.72M → 2.52M`** (`2.80 ms` P99, `0.40%` spread), `Tonic` `89k → 174k → 341k → 599k`, `C++ callback` `81k → 160k → 315k → 566k`, `gRPC-Go` `76k → 139k → 266k → 455k`, `C++ sync` `61k → 114k → 205k → 367k`.
  - **TLS (`1→2→4→8` cores):** `grpc-lite` **`417k → 819k → 1.45M → 2.16M`** (`1.40 ms` P99, `0.95%` spread), `Tonic` `84k → 163k → 317k → 548k`, `C++ callback` `77k → 150k → 299k → 524k`, `gRPC-Go` `76k → 140k → 262k → 451k`, `C++ sync` `56k → 102k → 188k → 334k`.
- **Multiplexed `h2load` Large & Asymmetric (1 core, PT / TLS):** `C++ callback` leads 64 KiB (`3,422 / 2,325 MiB/s`) and 1 MiB (`3,539 / 2,237 MiB/s`); `grpc-lite` leads `64 B → 64 KiB` PT (`48.2k`) with tightest P99 across all large payloads; `Tonic` leads `64 KiB → 64 B` (`57.5k` PT / `38.8k` TLS).
- **Streaming (1 core, `16 × 1 KiB`, Go / Tonic):** `Tonic` leads server-streaming across all 4 cells (`44.9k / 54.5k` PT, `39.1k / 43.8k` TLS); `grpc-lite` leads Go-client client-stream (`68.6k / 46.4k`) and Tonic-client bidi (`8.73k / 6.91k`); `gRPC-Go` leads Go-client bidi (`5.84k / 5.45k`).
- **Median 3-round spread:** `0.99%` (Part 1), `1.32%` (Part 2), and `0.85%` (Part 3).

## Provenance

- Harness executed on repo commit `77bfdd9` (with companion `bin/bench-client` gRPC unary validation enabled and zero machine-specific temp paths).
- `grpc-lite` built from upstream `main` commit `0f6c850feb8e8cca6038fabe4aeca07af43cb832` with [`grpc-lite.patch`](grpc-lite.patch), Zig `0.17.0`, `-Doptimize=ReleaseFast -Dtls=true` (OpenSSL 4.0.3), binary SHA-256 `76886c831fb5359b13b902be0cd936edc1a11689ed3cc65573e79c79e071d8cd`.
- `bin/bench-client` (Go `1.26.2`, `gRPC-Go 1.83.2`) rebuilt with 0 B unary response handling, binary SHA-256 `914f486fc4320573b9be7dd7d32606dc39dfd741406c77d687028b540b80b6f6`.
- `native-measured/environment.json`, `h2load-measured/environment.json`, and `streaming-measured/environment.json` record full binary SHA-256 hashes, `h2load nghttp2/1.69.0`, `lscpu`, CPU governors, and compiler toolchains.
