# Formal single-core unary capacity, 2026-07-27

This result compares single-core server capacity under two independent load
generators. It records aggregate throughput and server process CPU only.

## Environment

- Linux kernel `7.1.4-200.fc44.x86_64`
- AMD Ryzen 9 9955HX, 16 cores / 32 hardware threads
- CPU frequency governor: `performance`
- OpenCode PID affinity: logical CPU 4
- Server affinity: logical CPU 15
- For both load generators, client process 0 affinity: logical CPUs `1-3,6-8`
- For both load generators, client process 1 affinity: logical CPUs `9-10,12-14`

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
| Go | grpc-lite | 480009.6 | 447578.7 | 506856.0 | 89.662 | 1.000000 |
| Go | Tonic | 118089.8 | 116792.6 | 119009.0 | 79.117 | 0.246015 |
| Go | grpc-go | 90174.3 | 88888.4 | 90773.3 | 88.086 | 0.187859 |
| Go | gRPC C++ | 72676.1 | 72511.4 | 75246.1 | 86.050 | 0.151406 |
| Tonic | grpc-lite | 431940.8 | 418217.3 | 436972.7 | 86.671 | 1.000000 |
| Tonic | Tonic | 113224.8 | 111169.1 | 114471.8 | 82.120 | 0.262130 |
| Tonic | grpc-go | 89785.0 | 86174.2 | 89976.0 | 89.343 | 0.207864 |
| Tonic | gRPC C++ | 74611.4 | 73042.6 | 74993.9 | 88.255 | 0.172735 |

`go-client.jsonl` and `tonic-client.jsonl` each contain 20 cases: five rounds
for each of four servers. Every case has complete `server_cpu` telemetry. Their
aggregate latency fields are `null` because percentiles from independent
client-process histograms are not combined. `process_results` retains
per-process latency for diagnostics only and is not used for cross-process
percentile reporting.

## Interpretation

grpc-lite has a clear capacity lead under both load generators. The Tonic load
generator reports a lower absolute grpc-lite capacity bound, while the ordering
remains grpc-lite, Tonic, grpc-go, then gRPC C++ for both clients. The gRPC C++
1.48.4 system package is old and should not be treated as representative of a
current gRPC C++ release.
