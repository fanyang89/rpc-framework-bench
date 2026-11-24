#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

: "${GRPC_LITE_SERVER:?GRPC_LITE_SERVER must point to the grpc-lite interop server}"

CLIENT_IMPLS=${CLIENT_IMPLS:-"go tonic"}
CONCURRENCY_LEVELS=${CONCURRENCY_LEVELS:-"4 16 64 256"}
ROUNDS=${ROUNDS:-3}
WARMUP=${WARMUP:-2s}
DURATION=${DURATION:-10s}
GRPC_LITE_VERSION=${GRPC_LITE_VERSION:-grpc-lite/unknown}
GRPC_LITE_PORT=${GRPC_LITE_PORT:-25051}
GRPC_GO_PORT=${GRPC_GO_PORT:-25052}
TONIC_PORT=${TONIC_PORT:-25053}
GRPC_CPP_PORT=${GRPC_CPP_PORT:-25054}
SERVER_CPU=${SERVER_CPU:-15}
CLIENT_CPUS=${CLIENT_CPUS:-8-14}
CLIENT_PROCESSES=${CLIENT_PROCESSES:-1}
CLIENT_CPU_SETS=${CLIENT_CPU_SETS:-}
CONNECTIONS=${CONNECTIONS:-1}
REQ_SIZE=${REQ_SIZE:-1024}
RESP_SIZE=${RESP_SIZE:-1024}
CLIENT_THREADS=${CLIENT_THREADS:-4}
RUN_ID=${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}

if [[ ! $RUN_ID =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
  printf 'RUN_ID must be a single safe path component\n' >&2
  exit 1
fi
if [[ ! $ROUNDS =~ ^[1-9][0-9]*$ ]]; then
  printf 'ROUNDS must be positive\n' >&2
  exit 1
fi
if [[ ! $CLIENT_PROCESSES =~ ^[1-9][0-9]*$ ]]; then
  printf 'CLIENT_PROCESSES must be positive\n' >&2
  exit 1
fi

read -r -a client_impls <<<"$CLIENT_IMPLS"
read -r -a concurrency_levels <<<"$CONCURRENCY_LEVELS"
if ((${#client_impls[@]} == 0 || ${#concurrency_levels[@]} == 0)); then
  printf 'CLIENT_IMPLS and CONCURRENCY_LEVELS must not be empty\n' >&2
  exit 1
fi

output_dir="$ROOT/results/sweeps/$RUN_ID"
if [[ -e $output_dir ]]; then
  printf 'sweep output already exists: %s\n' "$output_dir" >&2
  exit 1
fi

for client_impl in "${client_impls[@]}"; do
  case "$client_impl" in
    go | tonic) ;;
    *)
      printf 'CLIENT_IMPLS contains unsupported client: %s\n' "$client_impl" >&2
      exit 1
      ;;
  esac

  for concurrency in "${concurrency_levels[@]}"; do
    if [[ ! $concurrency =~ ^[1-9][0-9]*$ ]]; then
      printf 'CONCURRENCY_LEVELS contains invalid value: %s\n' "$concurrency" >&2
      exit 1
    fi

    GRPC_LITE_SERVER="$GRPC_LITE_SERVER" \
    GRPC_LITE_VERSION="$GRPC_LITE_VERSION" \
    GRPC_LITE_PORT="$GRPC_LITE_PORT" \
    GRPC_GO_PORT="$GRPC_GO_PORT" \
    TONIC_PORT="$TONIC_PORT" \
    GRPC_CPP_PORT="$GRPC_CPP_PORT" \
    SERVER_CPU="$SERVER_CPU" \
    CLIENT_CPUS="$CLIENT_CPUS" \
    CLIENT_PROCESSES="$CLIENT_PROCESSES" \
    CLIENT_CPU_SETS="$CLIENT_CPU_SETS" \
    ROUNDS="$ROUNDS" \
    WARMUP="$WARMUP" \
    DURATION="$DURATION" \
    CONCURRENCY="$concurrency" \
    CONNECTIONS="$CONNECTIONS" \
    REQ_SIZE="$REQ_SIZE" \
    RESP_SIZE="$RESP_SIZE" \
    CLIENT_IMPL="$client_impl" \
    CLIENT_THREADS="$CLIENT_THREADS" \
    RESULT_FILE="$output_dir/$client_impl-p$CLIENT_PROCESSES-conn$CONNECTIONS-c$concurrency.jsonl" \
      "$ROOT/harness/run.sh"
  done
done

printf 'wrote sweep results to %s\n' "$output_dir" >&2
