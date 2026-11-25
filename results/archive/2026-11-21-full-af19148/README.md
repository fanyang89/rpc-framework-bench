# [DEPRECATED / INVALIDATED] Full RPC comparison: grpc-lite af19148

> [!WARNING]
> **DEPRECATED / INVALIDATED（已作废）** — Archived for historical provenance only. This dataset was collected before fixing benchmark implementation bugs in `workloads/` and `harness/` (see [`results/README.md`](../../README.md) and [`report/README.md`](../../../report/README.md)). Do not use these measurements for framework comparisons.

All 302 smoke and 906 formal runs passed. The formal runs completed 563,735,713
RPCs with zero RPC or validation errors.

## Results

OpenSSL had higher median throughput than historical mbedTLS in all 14 TLS
client/workload pairs. Each cell below is a throughput ratio (Go / Tonic),
based on three-round medians:

| TLS workload | New OpenSSL / old mbedTLS | New OpenSSL / new mbedTLS |
| --- | ---: | ---: |
| Unary, 1 KiB, 1 core | 7.18× / 6.26× | 2.21× / 1.98× |
| Unary, 1 KiB, 4 cores | 2.72× / 2.78× | 1.00× / 1.04× |
| Unary, 1 KiB, 8 cores | 1.64× / 1.41× | 1.02× / 0.98× |
| Unary, 64 KiB, 1 core | 4.64× / 5.72× | 3.88× / 4.75× |
| Client-stream, 1 core | 2.76× / 2.45× | 2.25× / 2.27× |
| Server-stream, 1 core | 4.91× / 4.71× | 2.12× / 2.21× |
| Bidi ping-pong, 1 core | 1.18× / 1.34× | 1.19× / 1.34× |

For single-core 1 KiB unary, old mbedTLS achieved 48,320 / 43,007 RPC/s,
new mbedTLS 156,919 / 136,016, and new OpenSSL 346,908 / 269,438.
The historical comparison therefore includes substantial gains beyond changing
the TLS backend. At the tested four- and eight-core loads neither current
backend consistently wins.

In the separate extended matrix, OpenSSL grpc-lite led the five gRPC server
configurations on single-core 1 KiB TLS unary (346,546 / 265,583 RPC/s);
Tonic was next (84,607 / 80,773). See `extended-measured/summary.tsv`.

In the native plaintext eight-core, 1 KiB, concurrency-128 case, Thrift led
at 992,615 RPC/s, followed by Cap'n Proto (772,100), brpc (632,113) and
grpc-lite (421,511). grpc-lite led the gRPC configurations in that case but
was last in the single-core symmetric 1 MiB case (1,289 RPC/s). These
closed-loop localhost results are workload-specific, not capacity ceilings.

## Method

This campaign runs three matrices sequentially on the same host. Each formal case has three repetitions.

| Matrix | Smoke runs | Formal runs | Scope |
| --- | ---: | ---: | --- |
| TLS backend comparison | 42 | 126 | Historical `a20631f` mbedTLS, current `af19148` OpenSSL 4.0.3, current `af19148` mbedTLS 3.6.6 |
| Native cross-protocol | 120 | 360 | grpc-lite, gRPC-Go, Tonic, C++ sync/callback, brpc, Thrift, Cap'n Proto |
| Extended gRPC | 140 | 420 | Five gRPC server configurations, Go and Tonic clients, plaintext/TLS, unary and three streaming modes |

The TLS comparison uses the previous interop reactor patch for both current builds,
Zig 0.17.0 and `ReleaseFast`. OpenSSL is the default for `-Dtls=true`;
`-Dtls-backend=mbedtls` selects the pinned mbedTLS package. The historical
server and the unchanged native matrix binaries are checked against the November
16 hash manifest; the OpenSSL binary and extended clients are checked against
the November 21 spot check. All clients verify the same TLS certificate and
hostname. The matrices reuse the `dd1f8f6` harness and frozen client binaries;
the Go client runs gRPC-Go 1.82.1.

In `tls/measured/comparison.tsv`, `upgrade_ratio` compares current OpenSSL to
historical mbedTLS; `same_commit_openssl_over_mbedtls` compares current builds.
Both divide median completed RPC/s for the same client and workload. The order
of the three servers rotates across cases. Each streaming RPC includes 16
messages or exchanges. `native-measured/` uses a different C++ client and
connection layout; do not combine throughput across matrices.

`run-all.sh` records the exact invocations. Stage logs sit alongside the
measurement directories. Reproduction requires fresh stage directories. Earlier
interrupted measurements are not inputs.
