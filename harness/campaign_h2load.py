#!/usr/bin/env python3
"""High-rate neutral third-party gRPC Unary matrix using h2load (nghttp2).

Compares all 5 gRPC server configurations (grpc-lite, Tonic, gRPC C++ callback,
gRPC C++ sync, gRPC-Go) in both Plaintext (h2c) and TLS (h2) across:
  1. Multicore 1 KiB Unary (1, 2, 4, 8 server cores):
     - 1c / 2c: client 0-11 (12 threads, 48 conns, -m 16)
     - 4c:      client 0-11 (12 threads, 96 conns, -m 16)
     - 8c:      client 0-7  ( 8 threads, 96 conns, -m 16)
  2. Large symmetric payloads on 1 server core (client 0-11):
     - 64 KiB (12 threads, 24 conns, -m 8)
     - 1 MiB  (12 threads, 24 conns, -m 2)
  3. Asymmetric payloads on 1 server core (client 0-11):
     - 64 B -> 64 KiB (12 threads, 24 conns, -m 8)
     - 64 KiB -> 64 B (12 threads, 24 conns, -m 8)

Before every run, a pre-flight `curl` HTTP/2 request verifies HTTP 200,
trailer `grpc-status: 0`, and exact Protobuf response length (with CA
verification on `localhost` for TLS). Every `h2load` run requires zero
failed/errored/timeout requests and zero non-2xx status codes.
A separate gRPC client checks each response in a matching workload after timing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import statistics
import struct
import subprocess
import time

if __package__:
    from . import unary_validation
else:
    import unary_validation

ROOT = Path(__file__).resolve().parent.parent

SERVERS = (
    "grpc-lite",
    "tonic",
    "grpc-cpp-callback",
    "grpc-cpp-sync",
    "grpc-go",
)

WORKLOADS = (
    # (scenario, server_cores, server_cpus, client_cpus, threads, conns, streams, req_bytes, resp_bytes)
    ("multicore", 1, "15", "0-11", 12, 48, 16, 1024, 1024),
    ("multicore", 2, "14-15", "0-11", 12, 48, 16, 1024, 1024),
    ("multicore", 4, "12-15", "0-11", 12, 96, 16, 1024, 1024),
    ("multicore", 8, "8-15", "0-7", 8, 96, 16, 1024, 1024),
    ("payload", 1, "15", "0-11", 12, 24, 8, 65536, 65536),
    ("payload", 1, "15", "0-11", 12, 24, 2, 1048576, 1048576),
    ("asymmetric", 1, "15", "0-11", 12, 24, 8, 64, 65536),
    ("asymmetric", 1, "15", "0-11", 12, 24, 8, 65536, 64),
)

PROXY_VARS = (
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "all_proxy",
    "ALL_PROXY",
    "grpc_proxy",
)


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
    """Build a 5-byte length-prefixed gRPC frame containing protobuf SimpleRequest."""
    parts = bytearray()
    if resp_size > 0:
        parts.extend(b"\x10" + encode_varint(resp_size))
    if req_size > 0:
        payload_msg = b"\x12" + encode_varint(req_size) + (b"\x00" * req_size)
        parts.extend(b"\x1a" + encode_varint(len(payload_msg)) + payload_msg)
    return b"\x00" + struct.pack(">I", len(parts)) + bytes(parts)


def expected_response_wire_len(resp_size: int) -> int:
    if resp_size == 0:
        return 5
    payload_len = 1 + len(encode_varint(resp_size)) + resp_size
    resp_len = 1 + len(encode_varint(payload_len)) + payload_len
    return 5 + resp_len


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for var in PROXY_VARS:
        env.pop(var, None)
    return env


def read_proc_stat_cores() -> dict[int, tuple[int, int]]:
    """Return {cpu_id: (total_jiffies, idle_jiffies)} for CPUs 0-15."""
    cores: dict[int, tuple[int, int]] = {}
    for line in Path("/proc/stat").read_text().splitlines():
        parts = line.split()
        if parts and parts[0].startswith("cpu") and parts[0] != "cpu":
            cid = int(parts[0][3:])
            if cid < 16:
                vals = [int(x) for x in parts[1:]]
                idle = vals[3] + vals[4]
                cores[cid] = (sum(vals), idle)
    return cores


def read_pid_cpu_ticks(pid: int) -> int:
    stat = Path(f"/proc/{pid}/stat").read_text()
    after = stat[stat.rfind(")") + 2 :].split()
    return int(after[11]) + int(after[12])


def parse_cpu_list(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


def core_busy_sum(
    before: dict[int, tuple[int, int]],
    after: dict[int, tuple[int, int]],
    cpus: list[int],
) -> tuple[float, list[float]]:
    per = []
    for c in cpus:
        dt = after[c][0] - before[c][0]
        di = after[c][1] - before[c][1]
        pct = ((dt - di) / dt * 100.0) if dt > 0 else 0.0
        per.append(round(pct, 1))
    return round(sum(per), 1), per


def parse_duration_us(token: str) -> float:
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
        "total": int(m_req.group(1)),
        "started": int(m_req.group(2)),
        "done": int(m_req.group(3)),
        "succeeded": int(m_req.group(4)),
        "failed": int(m_req.group(5)),
        "errored": int(m_req.group(6)),
        "timeout": int(m_req.group(7)),
        "status_2xx": int(m_code.group(1)),
        "status_3xx": int(m_code.group(2)),
        "status_4xx": int(m_code.group(3)),
        "status_5xx": int(m_code.group(4)),
        "total_bytes": int(m_traf.group(1)),
        "header_bytes": int(m_traf.group(2)),
        "data_bytes": int(m_traf.group(3)),
        "min_us": parse_duration_us(m_lat.group(1)),
        "max_us": parse_duration_us(m_lat.group(2)),
        "p50_us": parse_duration_us(m_lat.group(3)),
        "p95_us": parse_duration_us(m_lat.group(4)),
        "p99_us": parse_duration_us(m_lat.group(5)),
        "mean_us": parse_duration_us(m_lat.group(6)),
    }


def allocate_port(state: list[int]) -> int:
    for _ in range(950):
        port = state[0]
        state[0] = 26300 if port >= 26999 else port + 1
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port in 26300-26999")


def wait_for_port(port: int, proc: subprocess.Popen, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early with code {proc.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"timed out waiting for server on 127.0.0.1:{port}")


def start_server(
    server: str,
    cores: int,
    server_cpus: str,
    port: int,
    tls: bool,
    grpc_lite_bin: Path,
    log_path: Path,
) -> subprocess.Popen:
    env = clean_env()
    cert = str(ROOT / "bin/certs/server.pem")
    key = str(ROOT / "bin/certs/server.key")
    if tls:
        env["TLS_CERT"] = cert
        env["TLS_KEY"] = key
    else:
        env.pop("TLS_CERT", None)
        env.pop("TLS_KEY", None)

    if server == "grpc-lite":
        cmd = [str(grpc_lite_bin), f"--port={port}"]
        if cores > 1:
            cmd.append(f"--reactors={cores}")
        if tls:
            cmd.extend(["--use_tls=true", f"--tls_cert_file={cert}", f"--tls_key_file={key}"])
        else:
            cmd.append("--use_tls=false")
    elif server == "grpc-go":
        env["GOMAXPROCS"] = str(cores)
        cmd = [str(ROOT / "bin/go-server"), f"-addr=127.0.0.1:{port}"]
        if tls:
            cmd.extend([f"-cert={cert}", f"-key={key}"])
    elif server == "tonic":
        env["SERVER_THREADS"] = str(cores)
        env["SERVER_ADDR"] = f"127.0.0.1:{port}"
        cmd = [str(ROOT / "bin/tonic-server")]
    elif server in ("grpc-cpp-callback", "grpc-cpp-sync"):
        env["SERVER_THREADS"] = str(cores)
        env["CPP_API"] = "callback" if server == "grpc-cpp-callback" else "sync"
        cmd = [str(ROOT / "bin/cpp-server"), f"127.0.0.1:{port}"]
    else:
        raise ValueError(f"unknown server: {server}")

    full_cmd = ["taskset", "-c", server_cpus, *cmd]
    log_f = log_path.open("w")
    proc = subprocess.Popen(
        full_cmd,
        env=env,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_f.close()
    wait_for_port(port, proc)
    return proc


def stop_server(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)


def preflight_curl(port: int, tls: bool, req_file: Path, expected_body_len: int, tmp_dir: Path) -> None:
    hdr_file = tmp_dir / "preflight-hdr.txt"
    body_file = tmp_dir / "preflight-body.bin"
    env = clean_env()
    if tls:
        cmd = [
            "curl",
            "-sS",
            "--http2",
            "--cacert",
            str(ROOT / "bin/certs/ca.pem"),
            "--resolve",
            f"localhost:{port}:127.0.0.1",
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
            f"https://localhost:{port}/grpc.testing.TestService/UnaryCall",
        ]
    else:
        cmd = [
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
        ]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=15)
    if res.returncode != 0:
        raise RuntimeError(f"preflight curl failed: {res.stderr}")
    hdr = hdr_file.read_text()
    body_len = body_file.stat().st_size
    if "HTTP/2 200" not in hdr:
        raise RuntimeError(f"preflight non-200 response:\n{hdr}")
    if not re.search(r"(?im)^grpc-status:\s*0\s*$", hdr):
        raise RuntimeError(f"preflight missing grpc-status: 0:\n{hdr}")
    if body_len != expected_body_len:
        raise RuntimeError(f"preflight body length {body_len} != expected {expected_body_len}")


def seconds_to_ms(value: str) -> int:
    seconds = float(value)
    milliseconds = seconds * 1000
    if not math.isfinite(milliseconds) or seconds < 0:
        raise argparse.ArgumentTypeError(
            "expected a finite, nonnegative number of seconds"
        )
    return round(milliseconds)


def run_single_h2load(
    h2load_bin: Path,
    port: int,
    tls: bool,
    client_cpus: str,
    server_cpus: str,
    threads: int,
    conns: int,
    streams: int,
    warmup_ms: int,
    duration_ms: int,
    req_file: Path,
    server_pid: int,
    expected_body_len: int,
) -> dict:
    env = clean_env()
    cmd = [
        "taskset",
        "-c",
        client_cpus,
        str(h2load_bin),
        "-t",
        str(threads),
        "-c",
        str(conns),
        "-m",
        str(streams),
        f"--warm-up-time={warmup_ms}ms",
        "-D",
        f"{duration_ms}ms",
        "-H",
        "content-type: application/grpc",
        "-H",
        "te: trailers",
        "-d",
        str(req_file),
    ]
    if tls:
        cmd.extend(
            [
                "--alpn-list=h2",
                f"--connect-to=127.0.0.1:{port}",
                f"https://localhost:{port}/grpc.testing.TestService/UnaryCall",
            ]
        )
    else:
        cmd.extend(
            [
                "--no-tls-proto=h2c",
                f"http://127.0.0.1:{port}/grpc.testing.TestService/UnaryCall",
            ]
        )

    clk_tck = os.sysconf("SC_CLK_TCK")
    cli_cpu_ids = parse_cpu_list(client_cpus)
    srv_cpu_ids = parse_cpu_list(server_cpus)

    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    duration_s = duration_ms / 1000
    sample_start_delay = warmup_ms / 1000 + min(0.5, duration_s * 0.25)
    sample_window = duration_s - 1.0 if duration_s >= 2.0 else duration_s * 0.5
    time.sleep(sample_start_delay)

    t0 = time.monotonic()
    stat_before = read_proc_stat_cores()
    srv_ticks_0 = read_pid_cpu_ticks(server_pid)
    cli_ticks_0 = read_pid_cpu_ticks(proc.pid) if proc.poll() is None else 0

    time.sleep(sample_window)

    t1 = time.monotonic()
    stat_after = read_proc_stat_cores()
    srv_ticks_1 = read_pid_cpu_ticks(server_pid)
    cli_ticks_1 = read_pid_cpu_ticks(proc.pid) if proc.poll() is None else cli_ticks_0

    stdout, stderr = proc.communicate(timeout=45)
    if proc.returncode != 0:
        raise RuntimeError(f"h2load exited {proc.returncode}:\n{stderr}\n{stdout}")

    parsed = parse_h2load_output(stdout)
    max_inflight = conns * streams
    if (
        parsed["failed"] != 0
        or parsed["errored"] != 0
        or parsed["timeout"] != 0
        or parsed["status_3xx"] != 0
        or parsed["status_4xx"] != 0
        or parsed["status_5xx"] != 0
        or abs(parsed["status_2xx"] - parsed["succeeded"]) > max_inflight
        or parsed["succeeded"] != parsed["done"]
        or parsed["succeeded"] <= 0
    ):
        raise RuntimeError(f"h2load validation failed: {parsed}\n{stdout}")

    bytes_per_req = parsed["total_bytes"] / parsed["succeeded"]
    # A gross traffic deficit can reject a run; passing cannot establish RPC success.
    min_expected_total = max(0, parsed["succeeded"] - max_inflight) * expected_body_len
    if parsed["total_bytes"] < min_expected_total:
        raise RuntimeError(
            f"h2load total_bytes {parsed['total_bytes']} < expected {min_expected_total} "
            f"(succeeded={parsed['succeeded']}, wire_len={expected_body_len})"
        )

    dt = t1 - t0
    srv_proc_cpu = (srv_ticks_1 - srv_ticks_0) / clk_tck / dt * 100.0
    cli_proc_cpu = (cli_ticks_1 - cli_ticks_0) / clk_tck / dt * 100.0
    cli_core_sum, cli_core_per = core_busy_sum(stat_before, stat_after, cli_cpu_ids)
    srv_core_sum, srv_core_per = core_busy_sum(stat_before, stat_after, srv_cpu_ids)

    parsed["server_proc_cpu_percent"] = round(srv_proc_cpu, 2)
    parsed["client_proc_cpu_percent"] = round(cli_proc_cpu, 2)
    parsed["server_core_cpu_percent"] = srv_core_sum
    parsed["client_core_cpu_percent"] = cli_core_sum
    parsed["server_core_per_cpu"] = srv_core_per
    parsed["client_core_per_cpu"] = cli_core_per
    parsed["bytes_per_req"] = round(bytes_per_req, 1)
    return parsed


def verify_host_topology() -> None:
    required = set(range(16))
    if not required <= os.sched_getaffinity(0):
        raise SystemExit("h2load campaign requires CPUs 0-15 in affinity mask")
    topology = {
        (
            Path(f"/sys/devices/system/cpu/cpu{i}/topology/physical_package_id").read_text().strip(),
            Path(f"/sys/devices/system/cpu/cpu{i}/topology/core_id").read_text().strip(),
        )
        for i in range(16)
    }
    if len(topology) != 16:
        raise SystemExit("CPUs 0-15 must be 16 distinct physical cores")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Output directory (must not exist)")
    parser.add_argument(
        "--h2load",
        type=Path,
        default=Path(shutil.which("h2load") or "/usr/bin/h2load"),
        help="Path to h2load binary",
    )
    parser.add_argument("--smoke", action="store_true", help="Fast 1-round x 1s validation pass")
    parser.add_argument("--rounds", type=int, default=3, help="Formal rounds per case (default: 3)")
    parser.add_argument(
        "--duration",
        dest="duration_ms",
        metavar="SECONDS",
        type=seconds_to_ms,
        default="5",
        help="Measurement duration in seconds (rounded to milliseconds)",
    )
    parser.add_argument(
        "--warmup",
        dest="warmup_ms",
        metavar="SECONDS",
        type=seconds_to_ms,
        default="2",
        help="Warmup duration in seconds (rounded to milliseconds)",
    )
    args = parser.parse_args()
    duration_ms = 1000 if args.smoke else args.duration_ms
    warmup_ms = 500 if args.smoke else args.warmup_ms
    if duration_ms == 0:
        parser.error("--duration must round to at least 1 ms")

    grpc_lite_env = os.environ.get("GRPC_LITE_SERVER")
    if not grpc_lite_env:
        raise SystemExit("GRPC_LITE_SERVER environment variable is required")
    grpc_lite_bin = Path(grpc_lite_env).resolve()
    h2load_bin = args.h2load.resolve()
    if not grpc_lite_bin.is_file():
        raise SystemExit(f"GRPC_LITE_SERVER not found: {grpc_lite_bin}")
    if not h2load_bin.is_file():
        raise SystemExit(f"h2load not found: {h2load_bin}")

    verify_host_topology()
    args.output.mkdir(parents=True, exist_ok=False)
    out = args.output.resolve()
    logs_dir = out / "logs"
    logs_dir.mkdir()
    req_dir = out / "req-payloads"
    req_dir.mkdir()

    rounds = 1 if args.smoke else args.rounds

    req_files: dict[tuple[int, int], Path] = {}
    for _, _, _, _, _, _, _, req_b, resp_b in WORKLOADS:
        key = (req_b, resp_b)
        if key not in req_files:
            p = req_dir / f"grpc-req-{req_b}-{resp_b}.bin"
            p.write_bytes(build_grpc_simple_request(req_b, resp_b))
            req_files[key] = p

    h2load_ver = subprocess.check_output([str(h2load_bin), "--version"], text=True).strip()
    lscpu_out = subprocess.check_output(["lscpu", "-J"], text=True)
    governors = {
        f"cpu{i}": Path(f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_governor").read_text().strip()
        for i in range(16)
        if Path(f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_governor").is_file()
    }
    env_meta = {
        "smoke": args.smoke,
        "rounds": rounds,
        "warmup_seconds": warmup_ms / 1000,
        "duration_seconds": duration_ms / 1000,
        "h2load_version": h2load_ver,
        "workloads": [
            {
                "scenario": s,
                "server_cores": c,
                "server_cpus": sc,
                "client_cpus": cc,
                "threads": t,
                "connections": n,
                "max_concurrent_streams": m,
                "request_bytes": rb,
                "response_bytes": rpb,
            }
            for s, c, sc, cc, t, n, m, rb, rpb in WORKLOADS
        ],
        "versions": {
            "GRPC_LITE_VERSION": os.environ.get("GRPC_LITE_VERSION", "grpc-lite/unknown"),
            "GRPC_CPP_VERSION": os.environ.get("GRPC_CPP_VERSION", "grpc-cpp/unknown"),
        },
        "binaries": {
            str(h2load_bin): sha256_file(h2load_bin),
            str(unary_validation.CLIENT): sha256_file(unary_validation.CLIENT),
            str(grpc_lite_bin): sha256_file(grpc_lite_bin),
            str(ROOT / "bin/tonic-server"): sha256_file(ROOT / "bin/tonic-server"),
            str(ROOT / "bin/go-server"): sha256_file(ROOT / "bin/go-server"),
            str(ROOT / "bin/cpp-server"): sha256_file(ROOT / "bin/cpp-server"),
        },
        "governors": governors,
        "lscpu": lscpu_out,
    }
    (out / "environment.json").write_text(json.dumps(env_meta, indent=2) + "\n")

    port_state = [26300]
    records: list[dict] = []
    jsonl_path = out / "results.jsonl"

    cases = [(tls, w) for tls in (False, True) for w in WORKLOADS]
    total_runs = len(cases) * len(SERVERS) * rounds
    run_idx = 0

    with jsonl_path.open("w") as jf:
        for case_idx, (tls, wl) in enumerate(cases):
            scenario, cores, server_cpus, client_cpus, threads, conns, streams, req_b, resp_b = wl
            transport = "TLS" if tls else "Plaintext"
            req_file = req_files[(req_b, resp_b)]
            exp_body_len = expected_response_wire_len(resp_b)
            for r in range(1, rounds + 1):
                rot = (case_idx + r - 1) % len(SERVERS)
                order = SERVERS[rot:] + SERVERS[:rot]
                for srv in order:
                    run_idx += 1
                    port = allocate_port(port_state)
                    log_path = logs_dir / f"{transport.lower()}-{scenario}-c{cores}-{req_b}x{resp_b}-r{r}-{srv}.log"
                    proc = start_server(
                        srv, cores, server_cpus, port, tls, grpc_lite_bin, log_path
                    )
                    try:
                        preflight_curl(port, tls, req_file, exp_body_len, out)
                        res = run_single_h2load(
                            h2load_bin=h2load_bin,
                            port=port,
                            tls=tls,
                            client_cpus=client_cpus,
                            server_cpus=server_cpus,
                            threads=threads,
                            conns=conns,
                            streams=streams,
                            warmup_ms=warmup_ms,
                            duration_ms=duration_ms,
                            req_file=req_file,
                            server_pid=proc.pid,
                            expected_body_len=exp_body_len,
                        )
                        validation_path = log_path.with_suffix(".validation.json")
                        unary_validation.validate_unary(
                            port=port,
                            tls=tls,
                            client_cpus=client_cpus,
                            threads=threads,
                            connections=conns,
                            streams=streams,
                            request_bytes=req_b,
                            response_bytes=resp_b,
                            warmup_ms=warmup_ms,
                            duration_ms=duration_ms,
                            env=clean_env(),
                            output=validation_path,
                        )
                    finally:
                        stop_server(proc)
                    payload_mib_s = res["ops_per_second"] * (req_b + resp_b) / 1_048_576.0
                    rec = {
                        "round": r,
                        "scenario": scenario,
                        "server": srv,
                        "transport": transport,
                        "server_cores": cores,
                        "client_cores": len(parse_cpu_list(client_cpus)),
                        "client_cpus": client_cpus,
                        "server_cpus": server_cpus,
                        "threads": threads,
                        "connections": conns,
                        "max_concurrent_streams": streams,
                        "request_bytes": req_b,
                        "response_bytes": resp_b,
                        "payload_mib_s": round(payload_mib_s, 2),
                        "completion_scope": "http2",
                        "validation_file": str(validation_path.relative_to(out)),
                        **res,
                    }
                    records.append(rec)
                    jf.write(json.dumps(rec) + "\n")
                    jf.flush()
                    print(
                        f"[{run_idx:3d}/{total_runs}] {transport:9s} {scenario:10s} {cores}c {req_b}->{resp_b} "
                        f"{srv:18s} r{r}: {rec['ops_per_second']:10,.0f} req/s ({payload_mib_s:7,.0f} MiB/s) | "
                        f"P50={rec['p50_us']:6.0f}us P99={rec['p99_us']:6.0f}us | "
                        f"srv={rec['server_core_cpu_percent']:4.0f}%/{cores*100}% "
                        f"cli={rec['client_core_cpu_percent']:4.0f}%/{rec['client_cores']*100}%",
                        flush=True,
                    )

    for tmp_name in ("preflight-hdr.txt", "preflight-body.bin"):
        p = out / tmp_name
        if p.exists():
            p.unlink()

    # Write Benchkit semicolon CSV.
    csv_path = out / "benchkit-h2load.csv"
    cols = [
        "input/scenario",
        "input/server",
        "input/transport",
        "input/server_cores",
        "input/client_cores",
        "input/connections",
        "input/max_concurrent_streams",
        "input/request_bytes",
        "input/response_bytes",
        "rep",
        "output/ops_per_second",
        "output/ops_k",
        "output/payload_mib_s",
        "output/succeeded",
        "output/p50_us",
        "output/p95_us",
        "output/p99_us",
        "output/server_proc_cpu_percent",
        "output/client_proc_cpu_percent",
        "output/server_core_cpu_percent",
        "output/client_core_cpu_percent",
    ]
    with csv_path.open("w") as cf:
        cf.write(";".join(cols) + "\n")
        for rec in records:
            row = [
                rec["scenario"],
                rec["server"],
                rec["transport"],
                rec["server_cores"],
                rec["client_cores"],
                rec["connections"],
                rec["max_concurrent_streams"],
                rec["request_bytes"],
                rec["response_bytes"],
                rec["round"],
                f"{rec['ops_per_second']:.2f}",
                f"{rec['ops_per_second'] / 1000.0:.4f}",
                f"{rec['payload_mib_s']:.2f}",
                rec["succeeded"],
                f"{rec['p50_us']:.2f}",
                f"{rec['p95_us']:.2f}",
                f"{rec['p99_us']:.2f}",
                f"{rec['server_proc_cpu_percent']:.2f}",
                f"{rec['client_proc_cpu_percent']:.2f}",
                f"{rec['server_core_cpu_percent']:.1f}",
                f"{rec['client_core_cpu_percent']:.1f}",
            ]
            cf.write(";".join(map(str, row)) + "\n")

    # Write median summary.tsv.
    tsv_path = out / "summary.tsv"
    grouped: dict[tuple, list[dict]] = {}
    for rec in records:
        key = (
            rec["transport"],
            rec["scenario"],
            rec["server_cores"],
            rec["request_bytes"],
            rec["response_bytes"],
            rec["server"],
        )
        grouped.setdefault(key, []).append(rec)
    with tsv_path.open("w") as tf:
        tf.write(
            "transport\tscenario\tserver_cores\tclient_cores\treq_bytes\tresp_bytes\tserver\t"
            "median_ops_s\tmin_ops_s\tmax_ops_s\tspread_pct\tmedian_mib_s\t"
            "median_p50_us\tmedian_p99_us\tmedian_srv_core_pct\tmedian_cli_core_pct\n"
        )
        for transport in ("Plaintext", "TLS"):
            for scenario, cores, _, _, _, _, _, req_b, resp_b in WORKLOADS:
                for srv in SERVERS:
                    grp = grouped[(transport, scenario, cores, req_b, resp_b, srv)]
                    ops = [x["ops_per_second"] for x in grp]
                    med = statistics.median(ops)
                    spread = (max(ops) - min(ops)) / med * 100.0 if med > 0 else 0.0
                    tf.write(
                        f"{transport}\t{scenario}\t{cores}\t{grp[0]['client_cores']}\t"
                        f"{req_b}\t{resp_b}\t{srv}\t"
                        f"{med:.1f}\t{min(ops):.1f}\t{max(ops):.1f}\t{spread:.2f}\t"
                        f"{statistics.median(x['payload_mib_s'] for x in grp):.1f}\t"
                        f"{statistics.median(x['p50_us'] for x in grp):.1f}\t"
                        f"{statistics.median(x['p99_us'] for x in grp):.1f}\t"
                        f"{statistics.median(x['server_core_cpu_percent'] for x in grp):.1f}\t"
                        f"{statistics.median(x['client_core_cpu_percent'] for x in grp):.1f}\n"
                    )

    completions = sum(r["succeeded"] for r in records)
    print(
        f"\nCompleted {len(records)} runs, {completions:,} timed HTTP/2 completions -> {out}"
    )


if __name__ == "__main__":
    main()
