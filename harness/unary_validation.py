"""Run the gRPC-aware companion to a timed h2load measurement."""

import json
from pathlib import Path
import subprocess


CLIENT = Path(__file__).resolve().parents[1] / "bin/bench-client"


def validate_unary(
    *,
    port,
    tls,
    client_cpus,
    threads,
    connections,
    streams,
    request_bytes,
    response_bytes,
    warmup_ms,
    duration_ms,
    env,
    output,
):
    command = [
        "taskset",
        "-c",
        client_cpus,
        str(CLIENT),
        "-mode=unary",
        "-compact",
        f"-addr=127.0.0.1:{port}",
        f"-connections={connections}",
        f"-concurrency={connections * streams}",
        f"-req_size={request_bytes}",
        f"-resp_size={response_bytes}",
        f"-warmup={warmup_ms}ms",
        f"-duration={duration_ms}ms",
    ]
    if tls:
        command.append(f"-tls_ca={CLIENT.parent / 'certs/ca.pem'}")
    with output.open("w") as log:
        subprocess.run(
            command,
            env={**env, "GOMAXPROCS": str(threads)},
            stdout=log,
            timeout=(warmup_ms + duration_ms) / 1000 + 45,
            check=True,
        )
    validation = json.loads(output.read_text())
    if validation["errors"] or validation["completed"] <= 0:
        raise RuntimeError(f"invalid unary validation result: {output}")
