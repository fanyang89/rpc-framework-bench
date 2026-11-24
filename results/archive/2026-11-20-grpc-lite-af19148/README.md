# grpc-lite main vs. the November 18 build

On the same host, single-core TLS throughput fell with `af19148` relative
to `de94bc0`. Plaintext unary throughput with the Go and Tonic clients
changed by −1.1% to +1.7%. All 86 smoke runs and 258 measured runs passed.
The measured runs completed 237,361,278 RPCs with zero errors.

| Scenario | Go RPC/s, old → new | Change | Tonic RPC/s, old → new | Change |
| --- | ---: | ---: | ---: | ---: |
| Plaintext unary, 1 core, 1 KiB | 435,325 → 430,415 | −1.1% | 394,974 → 401,586 | +1.7% |
| TLS unary, 1 core, 1 KiB | 371,900 → 347,098 | −6.7% | 323,547 → 270,730 | −16.3% |
| TLS unary, 1 core, 64 KiB | 14,151 → 13,023 | −8.0% | 16,547 → 15,036 | −9.1% |
| TLS unary, 4 cores, 1 KiB | 558,913 → 551,557 | −1.3% | 476,314 → 474,334 | −0.4% |
| TLS unary, 8 cores, 1 KiB | 545,955 → 540,985 | −0.9% | 450,511 → 450,016 | −0.1% |
| TLS client-stream, 1 core | 50,107 → 47,091 | −6.0% | 47,722 → 42,141 | −11.7% |
| TLS server-stream, 1 core | 41,897 → 38,126 | −9.0% | 38,031 → 35,295 | −7.2% |
| TLS bidi, 1 core | 5,289 → 4,793 | −9.4% | 8,012 → 7,204 | −10.1% |

Values are medians of three five-second measurements. In every single-core TLS
case, all three new rounds landed below the lowest old round. The TLS backend
also changed, from wolfSSL to OpenSSL 4.0.3; this A/B measures the two builds
as shipped with their default TLS backends, not the isolated cost of either backend.

The separate native C++ plaintext matrix showed 1 MiB symmetric requests and
responses up 3.9% (1,239 → 1,287 RPC/s). The 64 B request / 64 KiB response
case fell 3.8% (43,921 → 42,239 RPC/s), with overlapping round ranges.

## Reproduction and data

- New server: upstream `af1914862dd9552bf25fb03e7544c213a23202a7` with the
  saved `grpc-lite.patch` applied to the interop server. Built using Zig 0.17.0:
  `zig build streaming-soak-peer -Doptimize=ReleaseFast -Dtls=true`.
- Old server: the November 18 `de94bc0` binary, verified against the saved
  SHA-256 and rerun in this campaign. Server and client hashes are in
  `binary-sha256.json`. The harness (`dd1f8f6`) and client binaries were reused:
  gRPC-Go 1.82.1, Tonic 0.14.6 and the native C++ driver.
- AMD Ryzen 9 9955HX, localhost. Clients on physical cores 0–7; server on 15,
  12–15 or 8–15. Extended runs used two clients, 32 connections total and a
  2 s warmup. Unary concurrency was 512; streaming concurrency was 32, with
  16 messages or exchanges per complete RPC. Native runs used a 1 s warmup.
- Run `RESULT_DIR=/absolute/path/to/new-results python compare.py` with the
  Benchkit environment and local paths recorded in the driver. The extended
  matrix runs old then new for each case; native configurations are shuffled.
- `extended/comparison.tsv` covers all 28 Go/Tonic pairs; `native/comparison.tsv`
  covers all 15 native pairs. Both contain medians and round ranges.
  These fixed-load loopback tests do not establish server capacity.
