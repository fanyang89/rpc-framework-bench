#!/usr/bin/env python3
"""Benchkit-managed closed-loop, plaintext 8-framework RPC comparison.

Non-HTTP/2 frameworks (brpc, Apache Thrift, Cap'n Proto) use their native C++
clients (1 connection per worker, closed-loop). The 5 gRPC stacks default to
neutral third-party `h2load` (nghttp2) with strict `-m 1` (1 in-flight RPC per
TCP connection) so connection/concurrency topology matches the native C++
clients apples-to-apples without blocking C++ sync client bottlenecks.
Pass `--grpc-client=grpc-native` to use the legacy C++ sync gRPC client.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
import resource
import shutil
import socket
import struct
import subprocess
import time

from benchkit.benchmark import Benchmark
from benchkit.campaign import CampaignIterateVariables

ROOT = Path(__file__).resolve().parent.parent
FRAMEWORKS = (
    "grpc-lite",
    "grpc-go",
    "tonic",
    "grpc-cpp",
    "grpc-cpp-callback",
    "brpc",
    "thrift",
    "capnp",
)
NATIVE_NON_GRPC = ("brpc", "thrift", "capnp")


def encode_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def build_grpc_simple_request(req_size: int, resp_size: int) -> bytes:
    parts = bytearray()
    if resp_size > 0:
        parts.extend(b"\x10" + encode_varint(resp_size))
    if req_size > 0:
        payload_msg = b"\x12" + encode_varint(req_size) + (b"\x00" * req_size)
        parts.extend(b"\x1a" + encode_varint(len(payload_msg)) + payload_msg)
    return b"\x00" + struct.pack(">I", len(parts)) + bytes(parts)


def expected_response_wire_lens(resp_size: int) -> tuple[int, ...]:
    if resp_size == 0:
        # Empty SimpleResponse (5B) or empty Payload submessage (7B)
        return (5, 7)
    payload_len = 1 + len(encode_varint(resp_size)) + resp_size
    resp_len = 1 + len(encode_varint(payload_len)) + payload_len
    return (5 + resp_len,)


def parse_h2load_duration_us(token: str) -> float:
    m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(us|ms|s)", token.strip())
    if not m:
        raise ValueError(f"cannot parse h2load duration token: {token!r}")
    val = float(m.group(1))
    unit = m.group(2)
    if unit == "us":
        return val
    if unit == "ms":
        return val * 1000.0
    return val * 1_000_000.0


def parse_h2load_output(text: str) -> dict:
    m_fin = re.search(
        r"finished in ([0-9.]+)(s|ms),\s*([0-9.]+) req/s,\s*([0-9.]+)([KMG]?B)/s",
        text,
    )
    m_req = re.search(
        r"requests:\s*(\d+) total,\s*(\d+) started,\s*(\d+) done,\s*(\d+) succeeded,\s*(\d+) failed,\s*(\d+) errored,\s*(\d+) timeout",
        text,
    )
    m_code = re.search(
        r"status codes:\s*(\d+) 2xx,\s*(\d+) 3xx,\s*(\d+) 4xx,\s*(\d+) 5xx",
        text,
    )
    m_traf = re.search(
        r"traffic:\s*[^()]+\((\d+)\) total,\s*[^()]+\((\d+)\) headers.*?,\s*[^()]+\((\d+)\) data",
        text,
    )
    m_lat = re.search(
        r"^request\s*:\s*(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)",
        text,
        re.MULTILINE,
    )
    if not (m_fin and m_req and m_code and m_traf and m_lat):
        raise RuntimeError(f"failed to parse h2load output:\n{text}")
    return {
        "ops_per_second": float(m_fin.group(3)),
        "completed": int(m_req.group(4)),
        "done": int(m_req.group(3)),
        "failed": int(m_req.group(5)),
        "errored": int(m_req.group(6)),
        "timeout": int(m_req.group(7)),
        "status_2xx": int(m_code.group(1)),
        "status_3xx": int(m_code.group(2)),
        "status_4xx": int(m_code.group(3)),
        "status_5xx": int(m_code.group(4)),
        "total_bytes": int(m_traf.group(1)),
        "min_us": parse_h2load_duration_us(m_lat.group(1)),
        "max_us": parse_h2load_duration_us(m_lat.group(2)),
        "p50_us": parse_h2load_duration_us(m_lat.group(3)),
        "p95_us": parse_h2load_duration_us(m_lat.group(4)),
        "p99_us": parse_h2load_duration_us(m_lat.group(5)),
        "mean_us": parse_h2load_duration_us(m_lat.group(6)),
        "errors": int(m_req.group(5)) + int(m_req.group(6)) + int(m_req.group(7)),
    }


def preflight_grpc_curl(
    port: int, req_file: Path, resp_size: int, directory: Path, env: dict[str, str]
) -> None:
    hdr_file = directory / "preflight-hdr.txt"
    body_file = directory / "preflight-body.bin"
    res = subprocess.run(
        [
            "curl",
            "-sS",
            "--http2-prior-knowledge",
            "-D",
            str(hdr_file),
            "-o",
            str(body_file),
            "-H",
            "content-type: application/grpc",
            "-H",
            "te: trailers",
            "--data-binary",
            f"@{req_file}",
            f"http://127.0.0.1:{port}/grpc.testing.TestService/UnaryCall",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if res.returncode != 0:
        raise RuntimeError(f"preflight curl failed: {res.stderr}")
    hdr = hdr_file.read_text()
    body = body_file.read_bytes()
    if "HTTP/2 200" not in hdr:
        raise RuntimeError(f"preflight non-200 response:\n{hdr}")
    if not re.search(r"(?im)^grpc-status:\s*0\s*$", hdr):
        raise RuntimeError(f"preflight missing grpc-status: 0:\n{hdr}")
    valid_lens = expected_response_wire_lens(resp_size)
    if len(body) not in valid_lens:
        raise RuntimeError(
            f"preflight response length {len(body)} not in expected {valid_lens}"
        )
    declared = struct.unpack(">I", body[1:5])[0]
    if declared != len(body) - 5:
        raise RuntimeError(f"preflight gRPC frame len {declared} != {len(body) - 5}")
    hdr_file.unlink(missing_ok=True)
    body_file.unlink(missing_ok=True)


def variables(smoke=False):
    workloads = [("latency", size, size, 1, 1) for size in (0, 64, 1024)]
    workloads += [
        ("concurrency", 1024, 1024, concurrency, 1) for concurrency in (1, 8, 32, 128)
    ]
    workloads += [("payload", size, size, 32, 1) for size in (1024, 65536, 1048576)]
    workloads += [
        ("asymmetric", request, response, 32, 1)
        for request, response in ((64, 65536), (65536, 64))
    ]
    workloads += [("multicore", 1024, 1024, 128, cores) for cores in (1, 4, 8)]
    records = [
        dict(
            framework=framework,
            scenario=scenario,
            request_bytes=request,
            response_bytes=response,
            concurrency=concurrency,
            server_cores=cores,
        )
        for scenario, request, response, concurrency, cores in workloads
        for framework in FRAMEWORKS
    ]
    random.Random(20261116).shuffle(records)
    return records


def commands(framework, cores, port, grpc_client="h2load", h2load_bin=None):
    address = f"127.0.0.1:{port}"
    env = dict(
        os.environ,
        GOMAXPROCS=str(cores),
        SERVER_THREADS=str(cores),
        SERVER_ADDR=address,
        CPP_API="callback" if framework.endswith("callback") else "sync",
    )
    env.pop("TLS_CERT", None)
    env.pop("TLS_KEY", None)
    for proxy_var in (
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
        "grpc_proxy",
    ):
        env.pop(proxy_var, None)
    if framework in NATIVE_NON_GRPC:
        binary = ROOT / f"bin/native-build/{framework}-native"
        server = [str(binary), "server", str(port), str(cores)]
        client = binary
    else:
        if grpc_client == "h2load":
            client = Path(h2load_bin or shutil.which("h2load") or "/usr/bin/h2load")
        else:
            client = ROOT / "bin/grpc-native-build/grpc-native"
        if framework == "grpc-lite":
            server = [
                os.environ["GRPC_LITE_SERVER"],
                f"--port={port}",
                "--use_tls=false",
            ]
            if cores > 1:
                server.append(f"--reactors={cores}")
        elif framework == "grpc-go":
            server = [str(ROOT / "bin/go-server"), f"-addr={address}"]
        elif framework == "tonic":
            server = [str(ROOT / "bin/tonic-server")]
        else:
            server = [str(ROOT / "bin/cpp-server"), address]
    return server, client, env


def cpu_seconds(pid):
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


class RpcBenchmark(Benchmark):
    def __init__(self, smoke=False, grpc_client="h2load", h2load_bin=None, req_dir=None):
        super().__init__(
            command_wrappers=(),
            command_attachments=(),
            shared_libs=(),
            pre_run_hooks=(),
            post_run_hooks=(),
        )
        self.smoke = smoke
        self.grpc_client = grpc_client
        self.h2load_bin = Path(h2load_bin or shutil.which("h2load") or "/usr/bin/h2load")
        self.req_dir = Path(req_dir) if req_dir else None
        self._req_cache: dict[tuple[int, int], Path] = {}
        self._next_port = 26051

    def _get_req_file(self, req_bytes: int, resp_bytes: int, fallback_dir: Path) -> Path:
        key = (req_bytes, resp_bytes)
        if key not in self._req_cache:
            base = self.req_dir or fallback_dir
            base.mkdir(parents=True, exist_ok=True)
            path = base / f"grpc-req-{req_bytes}-{resp_bytes}.bin"
            path.write_bytes(build_grpc_simple_request(req_bytes, resp_bytes))
            self._req_cache[key] = path
        return self._req_cache[key]

    def _allocate_port(self):
        # Stay below Linux's default ephemeral range (32768-60999) to avoid
        # TIME_WAIT port collisions across consecutive 128-connection runs.
        for _ in range(950):
            port = self._next_port
            self._next_port = 26051 if port >= 26999 else port + 1
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    probe.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    continue
        raise RuntimeError("no free benchmark port available in 26051-26999")

    @property
    def bench_src_path(self):
        return ROOT

    @staticmethod
    def get_build_var_names():
        return []

    @staticmethod
    def get_run_var_names():
        return [
            "framework",
            "scenario",
            "request_bytes",
            "response_bytes",
            "concurrency",
            "server_cores",
        ]

    def build_bench(self, **kwargs):
        for framework in FRAMEWORKS:
            server, client, _ = commands(
                framework, 1, 26051, self.grpc_client, self.h2load_bin
            )
            for path in (server[0], client):
                if not Path(path).is_file():
                    raise FileNotFoundError(path)

    def single_run(
        self,
        framework,
        scenario,
        request_bytes,
        response_bytes,
        concurrency,
        server_cores,
        benchmark_duration_seconds,
        record_data_dir,
        **kwargs,
    ):
        directory = Path(record_data_dir)
        port = self._allocate_port()
        server_command, client, env = commands(
            framework, server_cores, port, self.grpc_client, self.h2load_bin
        )
        server_cpus = ",".join(map(str, range(16 - server_cores, 16)))
        server_command = ["taskset", "-c", server_cpus, *server_command]

        use_h2load = self.grpc_client == "h2load" and framework not in NATIVE_NON_GRPC
        req_file = None
        if use_h2load:
            req_file = self._get_req_file(request_bytes, response_bytes, directory)
            threads = min(8, concurrency)
            warmup_arg = "200ms" if self.smoke else "1s"
            dur_arg = "1s" if self.smoke else f"{int(benchmark_duration_seconds)}s"
            client_command = [
                "taskset",
                "-c",
                "0-7",
                str(client),
                "--no-tls-proto=h2c",
                "-t",
                str(threads),
                "-c",
                str(concurrency),
                "-m",
                "1",
                f"--warm-up-time={warmup_arg}",
                "-D",
                dur_arg,
                "-H",
                "content-type: application/grpc",
                "-H",
                "te: trailers",
                "-d",
                str(req_file),
                f"http://127.0.0.1:{port}/grpc.testing.TestService/UnaryCall",
            ]
        else:
            client_command = [
                "taskset",
                "-c",
                "0-7",
                str(client),
                "client",
                f"127.0.0.1:{port}",
                str(concurrency),
                str(request_bytes),
                str(response_bytes),
                "100" if self.smoke else "1000",
                "100" if self.smoke else str(benchmark_duration_seconds * 1000),
            ]

        (directory / "commands.json").write_text(
            json.dumps({"server": server_command, "client": client_command}, indent=2)
        )
        with (directory / "server.log").open("w") as log:
            server = subprocess.Popen(server_command, env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 10
                while True:
                    if server.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(
                            f"{framework} failed to start: {directory / 'server.log'}"
                        )
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.02)

                if use_h2load:
                    preflight_grpc_curl(port, req_file, response_bytes, directory, env)

                before = cpu_seconds(server.pid)
                client_before = resource.getrusage(resource.RUSAGE_CHILDREN)
                started = time.monotonic()
                with (directory / "client.log").open("w") as errors:
                    result = subprocess.run(
                        client_command,
                        env=env,
                        capture_output=False,
                        stdout=subprocess.PIPE,
                        stderr=errors,
                        text=True,
                        check=True,
                        timeout=90,
                    )
                elapsed = time.monotonic() - started
                client_after = resource.getrusage(resource.RUSAGE_CHILDREN)

                if use_h2load:
                    metrics = parse_h2load_output(result.stdout)
                    if (
                        metrics["errors"] != 0
                        or metrics["status_3xx"] != 0
                        or metrics["status_4xx"] != 0
                        or metrics["status_5xx"] != 0
                        or abs(metrics["status_2xx"] - metrics["completed"]) > concurrency
                        or metrics["completed"] != metrics["done"]
                        or metrics["completed"] <= 0
                    ):
                        raise RuntimeError(
                            f"h2load validation failed for {framework}: {metrics}\n{result.stdout}"
                        )
                    min_wire_len = min(expected_response_wire_lens(response_bytes))
                    min_expected_total = max(0, metrics["completed"] - concurrency) * min_wire_len
                    if metrics["total_bytes"] < min_expected_total:
                        raise RuntimeError(
                            f"h2load total_bytes too small for {framework}: "
                            f"{metrics['total_bytes']} < {min_expected_total} "
                            f"(completed={metrics['completed']}, wire_len={min_wire_len})"
                        )
                else:
                    metrics = json.loads(result.stdout)
                    if metrics["errors"] != 0 or metrics["completed"] <= 0:
                        raise RuntimeError("invalid measurement")

                metrics["client_cpu_percent"] = (
                    (
                        client_after.ru_utime
                        + client_after.ru_stime
                        - client_before.ru_utime
                        - client_before.ru_stime
                    )
                    / elapsed
                    * 100
                )
                metrics["server_cpu_percent"] = (
                    (cpu_seconds(server.pid) - before) / elapsed * 100
                )
                metrics["payload_mib_s"] = (
                    metrics["ops_per_second"]
                    * (request_bytes + response_bytes)
                    / 1048576
                )
                output = json.dumps(metrics)
                (directory / "raw.json").write_text(output + "\n")
                return output
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()

    def parse_output_to_results(self, command_output, **kwargs):
        return json.loads(command_output)


def graphs(campaign):
    from plot_report import configure_theme, generate_native_charts

    configure_theme()
    csv_path = Path(campaign.csv_output_abs_path())
    charts_dir = csv_path.with_suffix("") / "charts"
    generate_native_charts(csv_path, charts_dir)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--no-graphs", action="store_true")
    parser.add_argument(
        "--grpc-client",
        choices=("h2load", "grpc-native"),
        default="h2load",
        help="Client for the 5 gRPC stacks (default: h2load with -m 1).",
    )
    parser.add_argument(
        "--h2load",
        type=Path,
        default=Path(shutil.which("h2load") or "/usr/bin/h2load"),
        help="Path to h2load binary when --grpc-client=h2load.",
    )
    args = parser.parse_args()
    if not set(range(16)) <= os.sched_getaffinity(0):
        parser.error("requires CPUs 0-15")
    topology = {
        (
            Path(
                f"/sys/devices/system/cpu/cpu{i}/topology/physical_package_id"
            ).read_text(),
            Path(f"/sys/devices/system/cpu/cpu{i}/topology/core_id").read_text(),
        )
        for i in range(16)
    }
    if len(topology) != 16:
        parser.error("CPUs 0-15 must be distinct physical cores")
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output.resolve()
    h2load_bin = args.h2load.resolve()
    binaries = {
        Path(path)
        for framework in FRAMEWORKS
        for path in (
            commands(framework, 1, 26051, args.grpc_client, h2load_bin)[0][0],
            commands(framework, 1, 26051, args.grpc_client, h2load_bin)[1],
        )
    }
    env_meta = {
        "grpc_client": args.grpc_client,
        "binaries": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in binaries
        },
        "lscpu": subprocess.check_output(["lscpu", "-J"], text=True),
        "smoke": args.smoke,
        "client_cpus": "0-7",
        "versions": {
            "GRPC_LITE_VERSION": os.environ.get("GRPC_LITE_VERSION", "grpc-lite/unknown"),
            "GRPC_CPP_VERSION": os.environ.get("GRPC_CPP_VERSION", "grpc-cpp/unknown"),
        },
        "source_diff": subprocess.check_output(["git", "diff"], cwd=ROOT, text=True),
    }
    if args.grpc_client == "h2load":
        env_meta["h2load_version"] = subprocess.check_output(
            [str(h2load_bin), "--version"], text=True
        ).strip()
    (output / "environment.json").write_text(json.dumps(env_meta, indent=2) + "\n")

    req_dir = output / "req-payloads"
    campaign = CampaignIterateVariables(
        name="rpc-native-smoke" if args.smoke else "rpc-native",
        benchmark=RpcBenchmark(
            smoke=args.smoke,
            grpc_client=args.grpc_client,
            h2load_bin=h2load_bin,
            req_dir=req_dir,
        ),
        nb_runs=1 if args.smoke else 3,
        variables=variables(args.smoke),
        constants={"transport": "plaintext"},
        debug=False,
        gdb=False,
        enable_data_dir=True,
        benchmark_duration_seconds=1 if args.smoke else 5,
        results_dir=output,
        tmp_dir=Path(os.environ.get("TMPDIR", "/tmp/opencode")),
    )
    campaign.run()
    if not args.no_graphs:
        graphs(campaign)


if __name__ == "__main__":
    main()
