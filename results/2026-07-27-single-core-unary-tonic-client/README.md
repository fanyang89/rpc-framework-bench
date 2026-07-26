# Single-core unary benchmark with Tonic client, 2026-07-27

## Environment

- Linux kernel `7.1.4-200.fc44.x86_64`
- AMD Ryzen 9 9955HX, 16 cores / 32 hardware threads
- 16 physical cores, 32 hardware threads, one socket, NUMA node 0
- CPU frequency governor: `performance`
- Server affinity: logical CPU 15
- Client affinity: logical CPUs 8-14
- Low-volume system services and OpenCode processes remained in the background

This was not a dedicated benchmark host. `run.sh` started only the measured
server, stopped it after the case, and rotated framework order between rounds.
The order was grpc-lite, grpc-go, Tonic, gRPC C++ in round one, with a one-place
left rotation in each subsequent round. Round five repeated round one's order.

## Workload

- Tonic 0.14.6 client with four Tokio runtime threads
- Plaintext HTTP/2
- Official wire-compatible `grpc.testing.TestService/UnaryCall`
- 1 KiB request payload and 1 KiB response payload
- One connection and four closed-loop workers
- 2 second warmup and 10 second measurement
- Five rounds per framework
- One server logical CPU; client on a separate CPU set

## Median Results

| Server | RPC/s | p50 (us) | p95 (us) | p99 (us) |
| --- | ---: | ---: | ---: | ---: |
| Tonic | 78182.4 | 50.165 | 66.375 | 75.692 |
| grpc-lite | 76736.8 | 50.946 | 71.585 | 81.383 |
| grpc-go | 72488.8 | 52.088 | 77.126 | 133.171 |
| gRPC C++ | 61660.5 | 58.841 | 109.816 | 131.057 |

`raw.jsonl` contains all 20 measured cases with zero reported errors.
`summary.json` contains these medians and percentage differences computed as
`(framework / grpc-lite - 1) * 100` for each metric.

## Interpretation

These numbers apply only to this single-core server, single connection, four
worker, 1 KiB unary configuration. Tonic leads grpc-lite throughput by about
1.9% with the Tonic client, a small difference that should not be generalized.
The common client remains part of the measured stack and still constrains
observed throughput, so these are not isolated server maxima. Request-template
cloning is also included in the Tonic client path. The gRPC C++ 1.48.4 system
package is old and should not be treated as representative of current gRPC C++
releases.
