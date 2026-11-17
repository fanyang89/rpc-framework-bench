# RPC framework comparison — 2026-11-16

## Executive summary

Benchkit completed **360 measured runs: 120 configurations × three repetitions**,
with **185,727,574 completed RPCs and zero RPC/response-size validation errors**.
The same 120 configurations passed smoke testing before measurement.

- Thrift has the lowest 1 KiB single-outstanding latency: median P50 **16.37 µs**.
- At 1 KiB and 128 outstanding calls, Thrift reaches **866k RPC/s** on eight
  server cores, followed by Cap'n Proto at **719k** and brpc at **638k**.
- grpc-lite leads the gRPC implementations in the one-core concurrency sweep
  (**134k RPC/s** at concurrency 128), but falls behind every tested stack for
  symmetric 1 MiB messages (**935 RPC/s**).
- C++ callback increases throughput over C++ sync in the multicore experiment,
  but has worse P99 latency. Asynchronous APIs are not a universal latency win.
- Prior gRPC-only TLS and streaming tests show a substantial grpc-lite TLS gap.
  The plaintext cross-protocol results must not be extrapolated to TLS.

These are **end-to-end stack measurements**, not isolated server capacity rankings.
The gRPC servers share one C++ gRPC client implementation; other protocols use
their respective C++ libraries behind the same load-driver contract.

## Method and versions

Host: AMD Ryzen 9 9955HX, 16 physical cores / 32 logical CPUs, Linux x86_64,
localhost TCP. Clients use physical CPUs 0–7; servers use CPU 15, CPUs 12–15,
or CPUs 8–15. CPU topology is checked before running.

| Component | Version |
| --- | --- |
| grpc-lite | `a20631f2dbe82d69b0352dc95dd1550343265f6f` plus saved interop CLI patch |
| gRPC-Go | 1.82.1 |
| Tonic | 0.14.6 |
| gRPC C++ sync / callback | 1.84.3 |
| brpc, `baidu_std` | 1.18.0 |
| Apache Thrift, binary/framed | 0.25.0 |
| Cap'n Proto, two-party RPC | 1.5.0 |
| Benchkit | `dafaa0380eeb4d57e1142bfa722af0c07849ad38` |

Exact dependency commits, compiler versions and binary SHA-256 values are in
`versions.json` and `environment.json`. grpc-lite uses ReleaseFast with TLS compiled
in; this matrix uses plaintext. C++ adapters use Release builds. brpc uses
system protobuf 3.19.6; gRPC C++ uses its independent bundled protobuf installation.
The saved brpc patch accommodates OpenSSL 4 const getter return types.

Benchkit `CampaignIterateVariables` owns execution, repetitions, per-run artifact
directories, CSV and charts. Each run uses one second of warmup and five seconds
of measurement. Configuration order is shuffled with a fixed seed; the three
repetitions of a configuration run consecutively.

Each outstanding call has one native client thread and its own persistent
connection. gRPC local subchannel pools and brpc connection groups prevent
cross-worker connection sharing. Framework-internal threads still differ.
Thrift handles requests inline on nonblocking I/O threads; Cap'n Proto uses
one event loop per server thread with SO_REUSEPORT. C++ gRPC retains its default
worker policy inside the assigned CPU affinity.

All payloads are zero-filled, compression and retries are disabled, and every
response length is validated. Most clients use a 10-second RPC deadline;
Thrift applies separate socket send/receive timeouts. A 90-second outer process
timeout bounds each load run. Failed runs are rejected rather than averaged in.

## Results

Tables show the **median of three per-run metrics**. A median P99 is not the
P99 of all three rounds pooled together. `summary.tsv` includes min/median/max
for throughput, latency, payload bandwidth and both CPU metrics for all 120
groups. Scenario labels remain separate even when their physical parameters
overlap; these are independently measured observations.

### Single-outstanding latency: 1 KiB each direction, one server core

| Stack | RPC/s | P50 µs | P99 µs |
| --- | ---: | ---: | ---: |
| Thrift | 59,754 | 16.37 | 23.89 |
| Cap'n Proto | 55,443 | 17.40 | 27.19 |
| brpc | 46,353 | 21.02 | 31.65 |
| grpc-lite | 28,716 | 34.12 | 47.55 |
| gRPC-Go | 24,977 | 38.36 | 70.88 |
| C++ callback | 24,083 | 40.34 | 64.34 |
| C++ sync | 23,940 | 40.91 | 60.42 |
| Tonic | 23,877 | 40.96 | 65.14 |

Thrift's P50 is about half grpc-lite's in this case. This includes differences
in client scheduling, serialization and wire protocol, not just server code.
The 0-byte and 64-byte cases are included in the CSV and latency charts.

### Concurrency sweep: 1 KiB each direction, one server core

Throughput in thousands of RPC/s:

| Stack | C=1 | C=8 | C=32 | C=128 | P99 at C=128, µs |
| --- | ---: | ---: | ---: | ---: | ---: |
| grpc-lite | 28.6 | 119.3 | 130.4 | 134.1 | 1,136 |
| gRPC-Go | 25.6 | 57.4 | 55.4 | 49.7 | 4,122 |
| Tonic | 23.3 | 70.0 | 69.8 | 68.4 | 1,995 |
| C++ sync | 20.3 | 50.8 | 58.7 | 55.9 | 4,591 |
| C++ callback | 24.2 | 65.2 | 69.5 | 69.1 | 11,047 |
| brpc | 46.0 | 126.7 | 127.8 | 128.2 | 1,005 |
| Thrift | 63.8 | 129.4 | 127.8 | 129.9 | 1,071 |
| Cap'n Proto | 55.2 | 138.7 | 134.2 | 132.2 | 1,072 |

Most stacks gain little throughput beyond eight outstanding calls; latency keeps
increasing. grpc-lite gains another 12% from eight to 128. C++ callback's
throughput advantage over sync comes with a much larger high-concurrency tail.
The four faster stacks cluster near 128–139k RPC/s; this does not establish equal
intrinsic server capacity.

### Multicore: 1 KiB each direction, concurrency 128

| Stack | 1 core RPC/s | 4 cores RPC/s | 8 cores RPC/s | 8-core P99 µs | 8-core client/server CPU % |
| --- | ---: | ---: | ---: | ---: | ---: |
| grpc-lite | 130,162 | 395,464 | 381,721 | 934 | 592 / 270 |
| gRPC-Go | 49,488 | 153,272 | 266,128 | 1,906 | 481 / 579 |
| Tonic | 68,976 | 255,510 | 301,781 | 1,902 | 490 / 321 |
| C++ sync | 62,375 | 170,048 | 290,797 | 1,004 | 504 / 500 |
| C++ callback | 69,323 | 237,487 | 323,705 | 1,501 | 584 / 516 |
| brpc | 127,942 | 449,008 | 638,058 | 252 | 547 / 393 |
| Thrift | 126,520 | 484,417 | 866,125 | 330 | 435 / 473 |
| Cap'n Proto | 132,560 | 507,173 | 718,996 | 781 | 561 / 359 |

Thrift gains 6.85× from one to eight server cores; Cap'n Proto gains 5.42×;
brpc gains 4.99×. grpc-lite gains 3.04× at four cores but no further throughput
at eight. Its client consumes more process CPU than its server. The shared
driver, client runtime, loopback processing and connection distribution remain
possible constraints; these measurements do not isolate the plateau's cause.

C++ callback gains 40% over sync at four cores and 11% at eight, while its P99
is respectively 74% and 50% higher.

CPU is process CPU, where 100% is one core and the eight-core budget is 800%.
The sampling interval includes setup, warmup, draining and aggregation, not only
the five-second measurement. It does not represent all CPU work attributable to
the workload (for example, independently accounted kernel/softirq work).
Do not infer saturation or unused capacity solely from these percentages.

### Large symmetric messages: concurrency 32, one server core

| Stack | 64 KiB RPC/s | 1 MiB RPC/s | 1 MiB payload MiB/s | 1 MiB P99 ms |
| --- | ---: | ---: | ---: | ---: |
| grpc-lite | 21,045 | 935 | 1,870 | 44.18 |
| gRPC-Go | 11,812 | 1,426 | 2,851 | 137.92 |
| Tonic | 28,963 | 2,195 | 4,390 | 23.36 |
| C++ sync | 21,354 | 1,614 | 3,228 | 91.17 |
| C++ callback | 24,308 | 1,649 | 3,298 | 112.52 |
| brpc | 36,054 | 1,988 | 3,976 | 44.82 |
| Thrift | 43,025 | 2,254 | 4,509 | 15.12 |
| Cap'n Proto | 36,244 | 1,821 | 3,641 | 19.38 |

Payload bandwidth counts request plus response bytes, excluding wire overhead;
it is not physical NIC bandwidth. Thrift leads both sizes. Tonic is close to
Thrift in 1 MiB throughput; their round ranges overlap, so a small median gap
is not evidence of a stable ranking. grpc-lite's 1 MiB throughput is 42% of
Thrift's, despite its small-message strengths.

### Asymmetric messages: concurrency 32, one server core

| Stack | 64 B request / 64 KiB response, RPC/s | 64 KiB request / 64 B response, RPC/s |
| --- | ---: | ---: |
| grpc-lite | 41,596 | 39,077 |
| gRPC-Go | 14,189 | 20,870 |
| Tonic | 39,686 | 45,702 |
| C++ sync | 38,112 | 35,099 |
| C++ callback | 39,270 | 36,765 |
| brpc | 51,450 | 66,149 |
| Thrift | 54,435 | 77,572 |
| Cap'n Proto | 53,928 | 55,042 |

Direction matters: brpc and Thrift handle the large-request case faster than
the large-response case; Cap'n Proto is nearly symmetric. These differences
include response construction, allocation and transport behavior.

## Relationship to TLS, streaming and prior gRPC measurements

The separate report `../2026-11-16-extended/README.md` contains 420 successful
measured runs covering five gRPC server configurations, two load-generator
implementations, TLS, streaming and multicore unary.

- Its Go / Tonic clients achieve **412,883 / 387,513 RPC/s** with grpc-lite for
  one-core plaintext 1 KiB unary, versus **48,653 / 42,717 RPC/s** with TLS.
  The 88%–89% drop is an end-to-end TLS gap, not a proven encryption bottleneck.
- At one-core 64 KiB unary, grpc-lite TLS achieves **2,834 / 2,644 RPC/s**,
  below all four other gRPC server configurations in that experiment.
- grpc-lite leads plaintext client-streaming (**66,133 / 61,892 RPC/s**) and
  server-streaming (**46,477 / 48,723 RPC/s**) there, but not TLS streaming.
  Each streaming RPC includes 16 messages/exchanges and final status.
- Bidirectional ping-pong ordering changes with the client implementation.
  Ping-pong is not independently saturated full-duplex traffic.
- C++ callback/reactor is the asynchronous API tested. CompletionQueue was not
  implemented. Streaming tests are single-core because the grpc-lite interop
  handler uses loop-confined shared stream state.

The previous experiment uses 512 unary outstanding calls across 32 connections,
two Go or Tonic client processes and different warmup/CPU accounting. This native
matrix uses one C++ process with a connection per outstanding RPC. The different
grpc-lite rates are therefore not a version regression or interchangeable
capacity estimate. Do not merge their latency or throughput samples.

## Variability and interpretation limits

Three five-second rounds are a local comparison, not a long-duration capacity
study. The largest throughput spread, `(max-min)/median`, is **19.7%** for brpc
at 1 MiB; Cap'n Proto eight-core throughput spans **660,294–758,107 RPC/s**
(13.6% spread). grpc-lite at 1 MiB spans **856–975 RPC/s** (12.7%).
Identical physical settings repeated under different scenario labels also vary:
C++ sync 1 KiB concurrency-one medians are 23,940 RPC/s in `latency` and
20,340 RPC/s in `concurrency`. This is a useful warning against small-gap claims.

Latency includes each measured synchronous call and merges all client-thread
samples within a run. This closed-loop workload does not correct coordinated
omission. Throughput includes draining calls started before the deadline.
Connection setup and warmup are excluded from RPC latency samples.

CPU governor, thermals, interrupts and background services were not controlled.
SO_REUSEPORT distribution may be uneven. Request handlers are simple payload
services, not application workloads. No remote-host network, open-loop arrival
process, multi-core streaming, mTLS, fixed-cipher TLS, or TLS/streaming for the
three non-gRPC frameworks was measured. No profiling isolates copying,
allocation, scheduler, protocol or cryptographic costs.

For this workload, Thrift is a strong low-latency/throughput candidate when its
protocol fits; Cap'n Proto and brpc also outperform the gRPC stacks in several
plaintext cases. grpc-lite remains strong for small-message plaintext gRPC,
but large messages and TLS need attention. Select using the required protocol,
streaming and TLS behavior rather than a single aggregate score.

## Charts, artifacts and reproduction

The directory `benchmark_msa_rpc-native_20261116_105653_178998/` contains six
Benchkit charts, each in PNG and PDF:

- `benchkit-latency-p50_us-*`: single-outstanding P50 versus message size.
- `benchkit-latency-p99_us-*`: single-outstanding P99 versus message size.
- `benchkit-concurrency-ops_per_second-*`: throughput versus concurrency.
- `benchkit-concurrency-p99_us-*`: P99 versus concurrency.
- `benchkit-payload-payload_mib_s-*`: application bandwidth versus payload size.
- `benchkit-multicore-ops_per_second-*`: throughput versus server core count.

Lines are medians; shaded bands span the three observed rounds, not confidence
intervals. The same directory retains per-run commands, logs and raw JSON.

Other artifacts:

- `benchmark_msa_rpc-native_20261116_105653_178998.csv`: 360 raw records.
- `summary.tsv`: 120 groups, with count and min/median/max metrics.
- `versions.json`, `python-requirements.txt`, `environment.json`: provenance.
- `*-CMakeCache.txt`, `grpc-lite.patch`, `brpc.patch`: build inputs and patches.
- `source/`: native adapters and campaign source snapshot.
- `../../native/README.md`: dependency and adapter build instructions.

From the benchmark repository root, after building those dependencies:

```sh
MPLBACKEND=Agg \
GRPC_LITE_SERVER=/absolute/path/to/grpc-lite-interop-server \
/tmp/opencode/rpc-benchkit-venv/bin/python campaign_rpc.py \
  --output results/benchkit-native-new
```

Use a new output directory. The campaign refuses to overwrite an existing one.
The native load-driver self-check covers invalid parameters, response-length
validation, setup/RPC failures, and successful concurrent execution.
