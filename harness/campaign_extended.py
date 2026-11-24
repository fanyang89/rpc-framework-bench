#!/usr/bin/env python3
"""Run a bounded cross-framework matrix; a streaming operation is a complete RPC."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parent.parent


def cases():
    for tls in (0, 1):
        for cores in (1, 4, 8):
            yield "unary", tls, cores, 1024
        for mode in ("client-stream", "server-stream", "bidi"):
            yield mode, tls, 1, 1024
        yield "unary", tls, 1, 65536


def monitor(path, stop):
    with path.open("x") as stream:
        while not stop.is_set():
            stream.write(
                json.dumps(
                    {
                        "time": time.time(),
                        "stat": Path("/proc/stat").read_text(),
                        "net": Path("/proc/net/dev").read_text(),
                    }
                )
                + "\n"
            )
            stream.flush()
            stop.wait(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    server = Path(os.environ["GRPC_LITE_SERVER"]).resolve()
    if not server.is_file():
        parser.error("GRPC_LITE_SERVER must exist")
    required_cpus = set(range(16))
    if not required_cpus <= os.sched_getaffinity(0):
        parser.error("matrix requires CPUs 0-15")
    # Require separate physical cores for the client and server CPU sets.
    topology = [
        Path(f"/sys/devices/system/cpu/cpu{i}/topology/core_id").read_text().strip()
        for i in range(16)
    ]
    if len(set(topology)) != 16:
        parser.error("CPUs 0-15 must be distinct physical cores")
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output.resolve()
    env = dict(
        os.environ,
        TMPDIR=os.environ.get("TMPDIR", "/tmp/opencode"),
        TLS_CERT=str(ROOT / "bin/certs/server.pem"),
        TLS_KEY=str(ROOT / "bin/certs/server.key"),
        TLS_CA=str(ROOT / "bin/certs/ca.pem"),
        GRPC_LITE_SERVER=str(server),
        GRPC_LITE_VERSION=os.environ["GRPC_LITE_VERSION"],
        GRPC_CPP_VERSION=os.environ["GRPC_CPP_VERSION"],
        ROUNDS="1" if args.smoke else "3",
        WARMUP="0s" if args.smoke else "2s",
        DURATION="200ms" if args.smoke else "5s",
        CLIENT_PROCESSES="2",
        CLIENT_CPU_SETS="0-3 4-7",
        CLIENT_THREADS="4",
        CONNECTIONS="16",
        MESSAGES="16",
    )
    binaries = [
        server,
        *(
            ROOT / "bin" / name
            for name in (
                "bench-client",
                "tonic-client",
                "go-server",
                "tonic-server",
                "cpp-server",
            )
        ),
    ]
    metadata = {
        "versions": {
            key: env[key] for key in ("GRPC_LITE_VERSION", "GRPC_CPP_VERSION")
        },
        "smoke": args.smoke,
        "binaries": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in binaries
        },
        "lscpu": subprocess.check_output(["lscpu", "-J"], text=True),
        "toolchains": {
            command[0]: subprocess.check_output(command, text=True)
            for command in (
                ["go", "version"],
                ["rustc", "--version"],
                ["c++", "--version"],
            )
        },
        "source_diff": subprocess.check_output(["git", "diff"], cwd=ROOT, text=True),
        "governors": {
            str(i): Path(f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_governor")
            .read_text()
            .strip()
            for i in range(16)
        },
    }
    (output / "environment.json").write_text(json.dumps(metadata, indent=2) + "\n")
    stop = threading.Event()
    worker = threading.Thread(
        target=monitor, args=(output / "host-telemetry.jsonl", stop)
    )
    worker.start()
    try:
        for mode, tls, cores, size in cases():
            for client in ("go", "tonic"):
                for api in ("sync", "callback"):
                    name = f"{mode}-tls{tls}-cores{cores}-bytes{size}-{client}-{api}"
                    case_env = dict(
                        env,
                        MODE=mode,
                        TLS=str(tls),
                        SERVER_THREADS=str(cores),
                        SERVER_CPU="15" if cores == 1 else f"{16 - cores}-15",
                        REQ_SIZE=str(size),
                        RESP_SIZE=str(size),
                        CLIENT_IMPL=client,
                        CPP_API=api,
                        FRAMEWORKS="grpc-cpp"
                        if api == "callback"
                        else "grpc-lite grpc-go tonic grpc-cpp",
                        CONCURRENCY="2"
                        if args.smoke
                        else ("256" if mode == "unary" else "16"),
                        RESULT_FILE=str(output / f"{name}.jsonl"),
                    )
                    print(name, flush=True)
                    subprocess.run(
                        ["./harness/run.sh"], cwd=ROOT, env=case_env, check=True, timeout=600
                    )
        paths = sorted(
            path
            for path in output.glob("*.jsonl")
            if path.name != "host-telemetry.jsonl"
        )
        records = [
            json.loads(line) for path in paths for line in path.read_text().splitlines()
        ]
        expected = 140 if args.smoke else 420
        assert len(records) == expected, (len(records), expected)
        assert all(
            record["errors"] == 0 and record["completed"] > 0 for record in records
        )
        with (output / "summary.tsv").open("w") as summary:
            subprocess.run(
                ["go", "run", "./harness/summarize/summarize.go", *map(str, paths)],
                cwd=ROOT,
                check=True,
                stdout=summary,
            )
    finally:
        stop.set()
        worker.join()


if __name__ == "__main__":
    main()
