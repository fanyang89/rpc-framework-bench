# Formal single-core unary capacity, grpc-lite c2d05b8, 2026-07-27

This result compares single-core server capacity under two independent load
generators. It records aggregate throughput and server process CPU only.

## Environment

- Linux kernel `7.1.4-200.fc44.x86_64`
- AMD Ryzen 9 9955HX, 16 cores / 32 hardware threads
- CPU frequency governor: `performance`
- Both OpenCode PIDs pinned to logical CPU 4
- Server affinity: logical CPU 15
- For both load generators, client process 0 affinity: logical CPUs `1-3,6-8`
- For both load generators, client process 1 affinity: logical CPUs `9-10,12-14`
- grpc-lite revision: `c2d05b8`

Only one server ran during each case. Server CPU is sampled from the server
process and includes all of its threads. It does not include loopback network
softirq CPU.

## Workload

- gRPC-Go 1.82.1 and Tonic 0.14.6 load generators
- Plaintext HTTP/2 unary RPC
- 1 KiB request payload and 1 KiB response payload
- Two client processes
- Concurrency 256 and 8 connections per process
- Total concurrency 512 and 16 total connections
- 2 second warmup and 10 second measurement
- Five rounds per server and client implementation
- Zero reported RPC errors

The server order rotated one place per round:

1. grpc-lite, grpc-go, Tonic, gRPC C++
2. grpc-go, Tonic, gRPC C++, grpc-lite
3. Tonic, gRPC C++, grpc-lite, grpc-go
4. gRPC C++, grpc-lite, grpc-go, Tonic
5. grpc-lite, grpc-go, Tonic, gRPC C++

## Round Medians

Relative throughput is `median RPC/s / grpc-lite median RPC/s` within the same
client implementation.

| Client | Server | Median RPC/s | Min RPC/s | Max RPC/s | Median server CPU (%) | Relative to grpc-lite |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Go | grpc-lite | 501737.0 | 497566.7 | 507903.5 | 89.496 | 1.000000 |
| Go | Tonic | 119371.4 | 118443.0 | 120132.3 | 78.530 | 0.237916 |
| Go | grpc-go | 90548.6 | 89362.9 | 91354.5 | 87.579 | 0.180470 |
| Go | gRPC C++ | 75685.8 | 73309.8 | 76341.6 | 85.619 | 0.150848 |
| Tonic | grpc-lite | 432794.2 | 427690.1 | 440563.8 | 86.593 | 1.000000 |
| Tonic | Tonic | 114829.9 | 114066.0 | 115541.4 | 82.036 | 0.265322 |
| Tonic | grpc-go | 89474.0 | 89083.4 | 89993.1 | 89.266 | 0.206736 |
| Tonic | gRPC C++ | 73872.9 | 72302.9 | 75917.8 | 88.183 | 0.170688 |

`go-client.jsonl` and `tonic-client.jsonl` each contain 20 cases: five rounds
for each of four servers. Every case has complete `server_cpu` telemetry. Their
aggregate latency fields are `null` because percentiles from independent
client-process histograms are not combined. `process_results` retains
per-process latency for diagnostics only and is not used for cross-process
percentile reporting.

## Revision Comparison

The previous baseline is
[`../2026-07-27-single-core-unary-capacity/`](../2026-07-27-single-core-unary-capacity/).
Delta is `(new median / old median - 1) * 100`.

| Load generator | Previous revision | New revision | Previous grpc-lite RPC/s | New grpc-lite RPC/s | Delta RPC/s | Delta (%) |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Go | `e24e5a2` | `c2d05b8` | 480009.6 | 501737.0 | +21727.4 | +4.526451 |
| Tonic | `e24e5a2` | `c2d05b8` | 431940.8 | 432794.2 | +853.4 | +0.197573 |

As environment controls, the Tonic and grpc-go server medians changed by
`+1.085276%` and `+0.415085%` under the Go load generator. Under the Tonic load
generator, they changed by `+1.417622%` and `-0.346383%`, respectively.

## Interpretation

grpc-lite retains the capacity lead and the ordering remains grpc-lite, Tonic,
grpc-go, then gRPC C++ for both load generators. Under the Go load generator,
grpc-lite's observed `+4.526451%` increase is larger than the changes in the two
main environment controls. Under the Tonic load generator, grpc-lite remains on
an approximately 433k RPC/s platform; this result cannot establish that there
was no server improvement. These are median comparisons and make no claim of
statistical significance. The gRPC C++ 1.48.4 system package is old and should
not be treated as representative of a current gRPC C++ release.
