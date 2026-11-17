#!/usr/bin/env python3
"""Both load generators must reject an untrusted CA and the wrong hostname."""

import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

root = Path(__file__).resolve().parent.parent
certs = root / "bin/certs"


def openssl(*args):
    subprocess.run(["openssl", *map(str, args)], check=True, capture_output=True)


with tempfile.TemporaryDirectory(
    dir=os.environ.get("TMPDIR", "/tmp/opencode")
) as directory:
    temp = Path(directory)
    openssl(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-subj",
        "/CN=untrusted",
        "-keyout",
        temp / "key.pem",
        "-out",
        temp / "ca.pem",
    )
    openssl(
        "req",
        "-new",
        "-key",
        certs / "server.key",
        "-subj",
        "/CN=wrong.invalid",
        "-addext",
        "subjectAltName=DNS:wrong.invalid",
        "-addext",
        "basicConstraints=critical,CA:FALSE",
        "-out",
        temp / "wrong.csr",
    )
    openssl(
        "x509",
        "-req",
        "-in",
        temp / "wrong.csr",
        "-CA",
        certs / "ca.pem",
        "-CAkey",
        certs / "ca.key",
        "-set_serial",
        "99",
        "-days",
        "1",
        "-copy_extensions",
        "copy",
        "-out",
        temp / "wrong.pem",
    )
    for certificate, authority, success in (
        (certs / "server.pem", certs / "ca.pem", True),
        (certs / "server.pem", temp / "ca.pem", False),
        (temp / "wrong.pem", certs / "ca.pem", False),
    ):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        server = subprocess.Popen(
            [
                str(root / "bin/go-server"),
                f"-addr=127.0.0.1:{port}",
                f"-cert={certificate}",
                f"-key={certs / 'server.key'}",
            ]
        )
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    if server.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("TLS server failed to start")
                    time.sleep(0.01)
            for client in ("bench-client", "tonic-client"):
                result = subprocess.run(
                    [
                        str(root / "bin" / client),
                        f"-addr=127.0.0.1:{port}",
                        f"-tls_ca={authority}",
                        "-warmup=0s",
                        "-duration=10ms",
                        "-concurrency=1",
                        "-connections=1",
                        "-compact",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                assert (result.returncode == 0) == success, (
                    client,
                    certificate,
                    authority,
                    result.stderr,
                )
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
print("TLS trust and hostname validation passed for both clients")
