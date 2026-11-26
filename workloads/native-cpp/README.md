# Benchkit native RPC comparison

`../../harness/campaign_native.py` uses Benchkit's `CampaignIterateVariables` execution engine,
per-run data directories, CSV result store and `generate_graph` API. The matrix
contains 120 configurations, with three repetitions for formal measurements.
Smoke uses the same configurations with 100 ms warmup and 100 ms measurement.

## Versions used

| Component | Version / commit |
| --- | --- |
| Benchkit | `dafaa0380eeb4d57e1142bfa722af0c07849ad38` |
| brpc | 1.18.0 / `94f1bbd32845a45f0218dfe43e25791252bdcb72` |
| Thrift | 0.25.0 / `27e8a425ffb498e190df3a12e239326bf5ba9ed6` |
| Cap'n Proto | 1.5.0 / `373e61ec89e2359f1c362e9b2eadc552f4779306` |
| LevelDB | 1.23 / `99b3c03b3284f5886f9ef9a4ef703d57373e61be` |
| libevent | 2.1.12 / `5df3037d10556bfcb675bc73e516978b75fc7bc7` |

Build dependencies into a private prefix. brpc uses system protobuf 3.19.6;
gRPC C++ uses its separate 1.84.3 installation and bundled protobuf. Never mix
these protobuf installations in the same executable.

On GCC 16, configure brpc with `-DCMAKE_CXX_STANDARD=17
-DCMAKE_CXX_EXTENSIONS=OFF`. With OpenSSL 4, apply `brpc-openssl4.patch` before
building: it preserves the return types of two now-const OpenSSL getters.
All dependencies and adapters use Release builds; brpc's upstream build adds
`-O2` after the Release flags.

From the repository root:

```sh
cmake -S workloads/native-cpp -B bin/native-build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="${TMPDIR:-/tmp}"/rpc-native
cmake --build bin/native-build --parallel 8
cmake -S workloads/native-cpp -B bin/grpc-native-build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DGRPC_ONLY=ON \
  -DCMAKE_PREFIX_PATH="${TMPDIR:-/tmp}"/grpc-1.84.3-install
cmake --build bin/grpc-native-build --parallel 4
c++ -std=c++17 -pthread workloads/native-cpp/load_test.cc -o "${TMPDIR:-/tmp}"/rpc-load-test
"${TMPDIR:-/tmp}"/rpc-load-test

export GRPC_LITE_SERVER=/absolute/path/to/grpc-lite-interop-server
export MPLBACKEND=Agg
"${TMPDIR:-/tmp}"/rpc-benchkit-venv/bin/python harness/campaign_native.py \
  --smoke --output results/sweeps/benchkit-smoke
"${TMPDIR:-/tmp}"/rpc-benchkit-venv/bin/python harness/campaign_native.py \
  --output results/benchkit-native
```

Install Benchkit at the pinned commit in a venv, plus numpy, pandas, matplotlib
and seaborn. Output directories must be new. Each run records server/client
commands, logs and validated JSON metrics. Charts are exported as PNG and PDF.

## Workloads and interpretation

Eight server configurations: grpc-lite, gRPC-Go, Tonic, C++ sync, C++ callback,
brpc `baidu_std`, Thrift binary/framed, and Cap'n Proto two-party RPC.

- Latency: 0, 64, 1024-byte request and response; one outstanding RPC.
- Concurrency: 1, 8, 32, 128 outstanding RPCs; 1 KiB each way.
- Payload: 1 KiB, 64 KiB, 1 MiB each way; concurrency 32.
- Asymmetric: 64 bytes / 64 KiB and reversed; concurrency 32.
- Multicore: 1, 4, 8 server cores; 1 KiB each way; concurrency 128.

Clients use CPUs 0-7; servers use the last 1/4/8 CPUs of 0-15. The driver
requires these to be distinct physical cores. Each outstanding RPC has its own
native thread and persistent connection. brpc connection groups and gRPC local
subchannel pools prevent accidental cross-worker connection sharing.
Framework-internal threads still differ.

Thrift handles requests inline on its configured nonblocking I/O threads.
Cap'n Proto uses one event loop per server thread and SO_REUSEPORT listeners;
kernel connection distribution can be uneven. C++ gRPC retains its default
worker policy within the CPU affinity budget.

Every call validates the response size. Calls have 10-second deadlines, except
Thrift's timeout applies separately to socket send/receive operations. An outer
90-second process timeout bounds each load run. Failures reject the measurement.
No retries or compression are enabled. Payloads are zero-filled.

Formal runs use one-second warmup and five-second measurement. Throughput
includes draining RPCs already started before the deadline. Percentiles combine
all measured samples from the single client process, not per-thread percentiles.
This is closed-loop latency and does not correct coordinated omission.
Server process CPU includes client setup, warmup, draining and aggregation;
it is diagnostic, not a precise measurement-window CPU metric.

These results compare end-to-end C++ client/server stacks across different wire
protocols. They do not establish standalone server capacity. The existing
Go/Tonic TLS and streaming results remain a separate experiment.
