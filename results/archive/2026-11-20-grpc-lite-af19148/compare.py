#!/usr/bin/env python3
"""Re-run the existing matrices against two frozen grpc-lite binaries."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path("/home/fanmi/.local/share/opencode/worktree/ab40b7/rpc-framework-bench")
OUT = Path(os.environ["RESULT_DIR"]).resolve()
BINARIES = {
    "local-de94bc0": Path(
        "/tmp/opencode/grpc-lite-bench-de94bc0-install/bin/grpc-lite-interop-server"
    ),
    "main-af19148": Path(
        "/tmp/opencode/grpc-lite-bench-af19148/zig-out/bin/grpc-lite-interop-server"
    ),
}
sys.path.insert(0, str(ROOT))
import campaign_rpc
import extended


def native(smoke):
    original = campaign_rpc.commands

    def commands(framework, cores, port):
        server, client, env = original("grpc-lite", cores, port)
        server[0] = str(BINARIES[framework])
        return server, client, env

    campaign_rpc.FRAMEWORKS = tuple(BINARIES)
    campaign_rpc.commands = commands
    sys.argv = [
        "campaign_rpc.py",
        "--output",
        str(OUT / ("native-smoke" if smoke else "native")),
    ]
    if smoke:
        sys.argv.append("--smoke")
    try:
        campaign_rpc.main()
    finally:
        campaign_rpc.commands = original


def grpc(smoke):
    output = OUT / ("extended-smoke" if smoke else "extended")
    output.mkdir()
    for mode, tls, cores, size in extended.cases():
        for client in ("go", "tonic"):
            for version, binary in BINARIES.items():
                name = f"{version}-{mode}-tls{tls}-cores{cores}-bytes{size}-{client}"
                env = dict(
                    os.environ,
                    GRPC_LITE_SERVER=str(binary),
                    GRPC_LITE_VERSION=version,
                    GRPC_CPP_VERSION="grpc-cpp/1.84.3",
                    FRAMEWORKS="grpc-lite",
                    MODE=mode,
                    TLS=str(tls),
                    SERVER_THREADS=str(cores),
                    SERVER_CPU="15" if cores == 1 else f"{16 - cores}-15",
                    REQ_SIZE=str(size),
                    RESP_SIZE=str(size),
                    CLIENT_IMPL=client,
                    CPP_API="sync",
                    CLIENT_PROCESSES="2",
                    CLIENT_CPU_SETS="0-3 4-7",
                    CLIENT_THREADS="4",
                    CONNECTIONS="16",
                    MESSAGES="16",
                    CONCURRENCY="2" if smoke else ("256" if mode == "unary" else "16"),
                    ROUNDS="1" if smoke else "3",
                    WARMUP="0s" if smoke else "2s",
                    DURATION="200ms" if smoke else "5s",
                    TLS_CERT=str(ROOT / "bin/certs/server.pem"),
                    TLS_KEY=str(ROOT / "bin/certs/server.key"),
                    TLS_CA=str(ROOT / "bin/certs/ca.pem"),
                    RESULT_FILE=str(output / f"{name}.jsonl"),
                )
                print(name, flush=True)
                subprocess.run(["./run.sh"], cwd=ROOT, env=env, check=True, timeout=180)
    paths = sorted(output.glob("*.jsonl"))
    records = [
        json.loads(line) for path in paths for line in path.read_text().splitlines()
    ]
    assert len(records) == (56 if smoke else 168), len(records)
    assert all(r["errors"] == 0 and r["completed"] > 0 for r in records)
    with (output / "summary.tsv").open("w") as stream:
        subprocess.run(
            ["go", "run", "./scripts/summarize.go", *map(str, paths)],
            cwd=ROOT,
            stdout=stream,
            check=True,
        )


if __name__ == "__main__":
    os.environ.setdefault("TMPDIR", "/tmp/opencode")
    os.environ["MPLBACKEND"] = "Agg"
    os.environ["GRPC_LITE_SERVER"] = str(BINARIES["main-af19148"])
    OUT.mkdir(exist_ok=False)
    old = json.loads(
        (ROOT / "results/2026-11-18-local-de94bc0/binary-sha256.json").read_text()
    )
    hashes = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in BINARIES.values()
    }
    baseline = str(BINARIES["local-de94bc0"])
    assert hashes[baseline] == old[baseline], "Baseline binary changed"
    for client in ["bench-client", "tonic-client", "grpc-native-build/grpc-native"]:
        p = ROOT / "bin" / client
        hashes[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    (OUT / "binary-sha256.json").write_text(json.dumps(hashes, indent=2) + "\n")
    (OUT / "compare.py").write_text(Path(__file__).read_text())
    (OUT / "grpc-lite.patch").write_bytes(
        (ROOT / "results/2026-11-16-benchkit-native/grpc-lite.patch").read_bytes()
    )
    native(True)
    grpc(True)
    native(False)
    grpc(False)
    print("All baseline/local smoke and formal runs completed.", flush=True)
