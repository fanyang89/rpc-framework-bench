#!/usr/bin/env bash
set -eu -o pipefail
out=$(cd "$(dirname "$0")" && pwd)
base=/home/fanmi/.local/share/opencode/worktree/ab40b7
root=$base/rpc-framework-bench
old=$base/rpc-framework-comparison/zig-out/bin/grpc-lite-interop-server
mbed=/tmp/opencode/grpc-lite-bench-af19148-mbedtls/bin/grpc-lite-interop-server
export GRPC_LITE_SERVER=/tmp/opencode/grpc-lite-bench-af19148/zig-out/bin/grpc-lite-interop-server
export GRPC_LITE_VERSION=af19148-openssl GRPC_CPP_VERSION=grpc-cpp/1.84.3
export TMPDIR=${TMPDIR:-/tmp/opencode} MPLBACKEND=Agg
cd "$root"
sha256sum --quiet --check <(jq -r '.binaries | to_entries[] | "\(.value)  \(.key)"' results/2026-11-16-benchkit-native/environment.json)
sha256sum --quiet --check "$out/../2026-11-21-openssl-spot-af19148/sha256.txt"
python3 "$out/tls.py" "$root" "$old" "$GRPC_LITE_SERVER" "$mbed" > "$out/tls.log" 2>&1
for stage in smoke measured; do
    flags=()
    if [[ $stage == smoke ]]; then flags=(--smoke --no-graphs); fi
    /tmp/opencode/rpc-benchkit-venv/bin/python campaign_rpc.py "${flags[@]}" \
        --output "$out/native-$stage" > "$out/native-$stage.log" 2>&1
done
for stage in smoke measured; do
    flags=()
    if [[ $stage == smoke ]]; then flags=(--smoke); fi
    python3 extended.py "${flags[@]}" --output "$out/extended-$stage" > "$out/extended-$stage.log" 2>&1
done
