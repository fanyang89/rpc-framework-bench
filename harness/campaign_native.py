#!/usr/bin/env python3
"""Benchkit-managed closed-loop, plaintext RPC comparison."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import resource
import socket
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


def commands(framework, cores, port):
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
    if framework in ("brpc", "thrift", "capnp"):
        binary = ROOT / f"bin/native-build/{framework}-native"
        server = [str(binary), "server", str(port), str(cores)]
        client = binary
    else:
        client = ROOT / "bin/grpc-native-build/grpc-native"
        if framework == "grpc-lite":
            server = [
                os.environ["GRPC_LITE_SERVER"],
                f"--port={port}",
                f"--reactors={cores}",
                "--use_tls=false",
            ]
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
    def __init__(self, smoke=False):
        super().__init__(
            command_wrappers=(),
            command_attachments=(),
            shared_libs=(),
            pre_run_hooks=(),
            post_run_hooks=(),
        )
        self.smoke = smoke

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
            server, client, _ = commands(framework, 1, 26051)
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
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        server_command, client, env = commands(framework, server_cores, port)
        server_cpus = ",".join(map(str, range(16 - server_cores, 16)))
        server_command = ["taskset", "-c", server_cpus, *server_command]
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
                metrics = json.loads(result.stdout)
                client_after = resource.getrusage(resource.RUSAGE_CHILDREN)
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
                if metrics["errors"] != 0 or metrics["completed"] <= 0:
                    raise RuntimeError("invalid measurement")
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
    binaries = {
        Path(path)
        for framework in FRAMEWORKS
        for path in (
            commands(framework, 1, 26051)[0][0],
            commands(framework, 1, 26051)[1],
        )
    }
    (output / "environment.json").write_text(
        json.dumps(
            {
                "binaries": {
                    str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in binaries
                },
                "lscpu": subprocess.check_output(["lscpu", "-J"], text=True),
                "smoke": args.smoke,
                "client_cpus": "0-7",
                "source_diff": subprocess.check_output(
                    ["git", "diff"], cwd=ROOT, text=True
                ),
            },
            indent=2,
        )
    )
    campaign = CampaignIterateVariables(
        name="rpc-native-smoke" if args.smoke else "rpc-native",
        benchmark=RpcBenchmark(args.smoke),
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
