"""Compare two TLS-enabled interop servers using a built benchmark checkout."""

import hashlib
import json
import os
from pathlib import Path
from statistics import median
import subprocess
import sys

root, openssl, mbedtls = (Path(arg).resolve() for arg in sys.argv[1:])
sys.path.insert(0, str(root))
from extended import cases

out = Path(__file__).resolve().parent
servers = {"openssl": openssl, "mbedtls": mbedtls}
files = [*servers.values(), root / "bin/bench-client", root / "bin/tonic-client"]
(out / "sha256.json").write_text(
    json.dumps(
        {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}, indent=2
    )
    + "\n"
)

for smoke in (True, False):
    directory = out / ("smoke" if smoke else "measured")
    directory.mkdir()
    rows = []
    for mode, tls, cores, size in cases():
        if not tls:
            continue
        for client in ("go", "tonic"):
            rates = {}
            for backend in list(servers)[:: 1 if len(rows) % 2 == 0 else -1]:
                path = directory / f"{mode}-{cores}-{size}-{client}-{backend}.jsonl"
                rounds = 1 if smoke else 3
                certs = root / "bin/certs"
                env = dict(
                    os.environ,
                    FRAMEWORKS="grpc-lite",
                    TLS="1",
                    MODE=mode,
                    GRPC_LITE_SERVER=str(servers[backend]),
                    GRPC_LITE_VERSION=f"af19148/{backend}",
                    CLIENT_IMPL=client,
                    SERVER_THREADS=str(cores),
                    SERVER_CPU="15" if cores == 1 else f"{16 - cores}-15",
                    REQ_SIZE=str(size),
                    RESP_SIZE=str(size),
                    MESSAGES="16",
                    CLIENT_PROCESSES="2",
                    CLIENT_CPU_SETS="0-3 4-7",
                    CLIENT_THREADS="4",
                    CONNECTIONS="16",
                    CONCURRENCY="2" if smoke else ("256" if mode == "unary" else "16"),
                    TLS_CERT=str(certs / "server.pem"),
                    TLS_KEY=str(certs / "server.key"),
                    TLS_CA=str(certs / "ca.pem"),
                    ROUNDS=str(rounds),
                    WARMUP="0s" if smoke else "2s",
                    DURATION="200ms" if smoke else "5s",
                    RESULT_FILE=str(path),
                )
                subprocess.run(
                    ["bash", "run.sh"], cwd=root, env=env, check=True, timeout=180
                )
                records = [json.loads(line) for line in path.read_text().splitlines()]
                assert len(records) == rounds and all(
                    r["errors"] == 0 and r["completed"] > 0 and r["config"]["tls"]
                    for r in records
                )
                rates[backend] = median(r["ops_per_second"] for r in records)
            a, b = rates["openssl"], rates["mbedtls"]
            rows.append(
                f"{mode}\t{cores}\t{size}\t{client}\t{a:.1f}\t{b:.1f}\t{b / a:.3f}"
            )
    assert len(rows) == 14
    (directory / "comparison.tsv").write_text(
        "mode\tcores\tbytes\tclient\topenssl_rpc_s\tmbedtls_rpc_s\tmbedtls_over_openssl\n"
        + "\n".join(rows)
        + "\n"
    )
