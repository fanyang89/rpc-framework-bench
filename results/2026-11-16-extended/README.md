# Extended gRPC comparison — 2026-11-16

All 420 measured cases completed with zero RPC/validation errors. Before
measurement, all 140 smoke cases passed, along with Go race tests, streaming
terminal-status/count tests, Rust unit tests, shell parameter validation,
and both clients' rejection of untrusted CAs and incorrect hostnames.

## Method

- Host: AMD Ryzen 9 9955HX, 16 physical cores / 32 logical CPUs.
- Servers: grpc-lite `a20631f` plus the included interop reactor CLI patch,
  gRPC-Go 1.82.1, Tonic 0.14.6, and gRPC C++ 1.84.3.
- grpc-lite: Zig 0.17.0, `ReleaseFast`, `-Dtls=true` for plaintext and TLS.
  C++: CMake Release, synchronous and callback/reactor APIs, bundled protobuf.
- Two load-generator processes, Go or Tonic, pinned to CPUs `0-3` and `4-7`.
  Each has 16 connections; total 32. Tonic uses four workers per process.
- Server affinity: CPU `15`, CPUs `12-15`, or CPUs `8-15`. These are distinct
  physical cores from clients. Go/Tonic/grpc-lite use matching scheduler workers
  or reactors; C++ keeps its own worker policy within this CPU budget.
- Unary: total concurrency 512. Streaming: total concurrency 32.
- Each case: three rounds, two-second warmup, five-second measurement.
  Values below are round medians, rounded to whole RPC/s.
- Streaming is single-core: the grpc-lite interop handler's mutable stream map
  is loop-confined. Multi-reactor mode registers only unary methods.
- TLS verifies a private local CA and the localhost SAN. Stack-default protocol
  and cipher negotiation is retained. This is not a fixed-cipher comparison,
  and does not measure handshakes per second.

Each table cell is **Go client / Tonic client**. One streaming operation includes
the entire RPC, its 16 messages/exchanges, half-close, and final status.
Do not compare streaming RPC/s directly with unary RPC/s.

The new clients include complete-RPC deadlines and streaming support, and CPU
placement/connections differ from the earlier unary run. Compare within this
matrix, not as a version regression against the previous report.

## 1 KiB unary, one core

| Server | Plaintext RPC/s | TLS RPC/s |
| --- | ---: | ---: |
| grpc-lite | 412,883 / 387,513 | 48,653 / 42,717 |
| gRPC-Go | 73,844 / 75,518 | 73,141 / 73,227 |
| Tonic | 91,917 / 89,586 | 84,176 / 80,848 |
| gRPC C++ sync | 67,476 / 67,982 | 58,953 / 59,146 |
| gRPC C++ callback | 83,669 / 83,592 | 75,210 / 73,375 |

grpc-lite leads this plaintext case but is last in the TLS case. Its TLS
throughput is approximately 88%-89% below plaintext. This identifies an
end-to-end TLS performance gap; this experiment does not isolate the cause.

## 1 KiB unary, multicore

| Server | Plaintext, 4 cores | Plaintext, 8 cores | TLS, 4 cores | TLS, 8 cores |
| --- | ---: | ---: | ---: | ---: |
| grpc-lite | 594,724 / 515,088 | 585,734 / 480,828 | 199,794 / 172,310 | 322,189 / 328,641 |
| gRPC-Go | 242,220 / 234,509 | 366,863 / 344,035 | 242,681 / 234,084 | 364,184 / 341,360 |
| Tonic | 314,779 / 312,448 | 440,856 / 425,756 | 299,665 / 281,977 | 407,111 / 383,170 |
| gRPC C++ sync | 207,773 / 200,019 | 305,914 / 313,991 | 193,554 / 184,904 | 281,632 / 287,161 |
| gRPC C++ callback | 310,938 / 316,453 | 447,512 / 425,859 | 280,994 / 279,445 | 412,061 / 395,922 |

**grpc-lite's plaintext plateau is load-generator limited in this setup.**
During its 4/8-core plaintext cases, client cores averaged 96.1%-97.8% busy;
assigned server cores averaged only 31.3%-70.2% busy. These numbers cannot
establish grpc-lite's multicore capacity or an intrinsic scaling ceiling.

grpc-lite's one-core TLS server CPU was about 99.8% busy, including about
29.5%-30.9% softirq, while client cores averaged only 17.7%-21.7% busy.
At four TLS cores the assigned server cores averaged 99.3%-99.7% busy.
At eight TLS cores, client pressure becomes material again (88.5%-95.1% busy).
These are `/proc/stat` samples joined to per-case windows, excluding the first
second of each window; windows include warmup and draining, not only measurement.

C++ callback improves unary throughput over sync under both clients: roughly
23%-24% in the one-core plaintext case and 36%-46% at eight plaintext cores.
This improvement is workload-specific; streaming does not show a universal win.

## Streaming, one core, 1 KiB × 16

| Mode | Server | Plaintext RPC/s | TLS RPC/s |
| --- | --- | ---: | ---: |
| Client-stream | grpc-lite | 66,133 / 61,892 | 17,175 / 17,447 |
| Client-stream | gRPC-Go | 31,784 / 34,319 | 30,504 / 33,974 |
| Client-stream | Tonic | 43,768 / 59,706 | 35,935 / 48,513 |
| Client-stream | C++ sync | 26,531 / 28,897 | 22,516 / 25,188 |
| Client-stream | C++ callback | 26,263 / 29,827 | 22,623 / 25,590 |
| Server-stream | grpc-lite | 46,477 / 48,723 | 7,712 / 7,482 |
| Server-stream | gRPC-Go | 31,318 / 30,051 | 27,413 / 25,869 |
| Server-stream | Tonic | 7,682 / 8,993 | 7,456 / 8,377 |
| Server-stream | C++ sync | 8,458 / 8,804 | 7,543 / 7,810 |
| Server-stream | C++ callback | 8,092 / 8,733 | 7,674 / 7,740 |
| Bidi ping-pong | grpc-lite | 4,842 / 8,997 | 4,035 / 5,290 |
| Bidi ping-pong | gRPC-Go | 5,793 / 5,714 | 5,303 / 4,857 |
| Bidi ping-pong | Tonic | 4,184 / 7,190 | 4,415 / 6,403 |
| Bidi ping-pong | C++ sync | 2,694 / 4,363 | 2,391 / 3,740 |
| Bidi ping-pong | C++ callback | 3,128 / 5,773 | 2,679 / 4,740 |

grpc-lite leads plaintext client/server streaming here, but not TLS streaming.
Bidi ordering changes with the load generator; it is especially sensitive to
client scheduling and round-trip latency. It does not represent independently
saturated full-duplex traffic. Server implementations also differ in buffering
and scheduling: Tonic uses a bounded one-slot response channel, C++ callback
issues one write at a time, and grpc-lite uses transport backpressure.

## 64 KiB unary, one core

| Server | Plaintext RPC/s | TLS RPC/s |
| --- | ---: | ---: |
| grpc-lite | 14,355 / 17,971 | 2,834 / 2,644 |
| gRPC-Go | 7,770 / 9,494 | 6,808 / 7,495 |
| Tonic | 16,784 / 20,600 | 14,297 / 15,426 |
| C++ sync | 14,866 / 24,640 | 11,146 / 14,447 |
| C++ callback | 15,715 / 28,368 | 15,240 / 18,414 |

grpc-lite's small-message plaintext advantage does not carry over to this
large-message case. TLS is particularly weak here. No profiling was performed
to attribute the gap to encryption, copying, allocation, framing, or scheduling.

## Artifacts and limits

- `summary.tsv`: all 140 groups, including per-round min/max and server CPU.
- Per-scenario JSONL: all 420 records, per-client diagnostics and sample windows.
- `environment.json`: machine, compiler versions, binary hashes and tracked diff.
- `grpc-lite.patch`: exact local interop CLI/registration changes.
- `host-telemetry.jsonl.gz`: one-second CPU/network telemetry.
- Root `extended.py` and README: build/run commands and metric definitions.

No multi-core streaming, independently driven full-duplex load, remote-host
network, mTLS, fixed-cipher TLS, C++ CompletionQueue implementation, or exhaustive
load sweep was measured. C++ callback is the asynchronous API tested here.
CPU governor, thermal state, interrupts and background services were not
controlled. Aggregate latency percentiles are null rather than incorrectly
merging independent client percentiles. These are fixed-load observations,
not universal framework rankings or validated capacity ceilings.
