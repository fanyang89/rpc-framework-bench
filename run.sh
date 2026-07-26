#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

: "${GRPC_LITE_SERVER:?GRPC_LITE_SERVER must point to the grpc-lite interop server}"

GRPC_LITE_VERSION=${GRPC_LITE_VERSION:-grpc-lite/unknown}
GRPC_LITE_PORT=${GRPC_LITE_PORT:-51051}
GRPC_GO_PORT=${GRPC_GO_PORT:-51052}
TONIC_PORT=${TONIC_PORT:-51053}
GRPC_CPP_PORT=${GRPC_CPP_PORT:-51054}
SERVER_CPU=${SERVER_CPU:-15}
CLIENT_CPUS=${CLIENT_CPUS:-8-14}
ROUNDS=${ROUNDS:-5}
WARMUP=${WARMUP:-2s}
DURATION=${DURATION:-10s}
CONCURRENCY=${CONCURRENCY:-4}
CONNECTIONS=${CONNECTIONS:-1}
REQ_SIZE=${REQ_SIZE:-1024}
RESP_SIZE=${RESP_SIZE:-1024}
CLIENT_IMPL=${CLIENT_IMPL:-tonic}
CLIENT_THREADS=${CLIENT_THREADS:-4}
RESULT_FILE=${RESULT_FILE:-results/raw-${CLIENT_IMPL}-client.jsonl}

case "$CLIENT_IMPL" in
  tonic)
    CLIENT_BIN="$ROOT/bin/tonic-client"
    ;;
  go)
    CLIENT_BIN="$ROOT/bin/bench-client"
    ;;
  *)
    printf 'CLIENT_IMPL must be tonic or go\n' >&2
    exit 1
    ;;
esac

if [[ $CLIENT_IMPL == tonic ]] && { [[ ! $CLIENT_THREADS =~ ^[0-9]+$ ]] || ((CLIENT_THREADS <= 0)); }; then
  printf 'CLIENT_THREADS must be positive\n' >&2
  exit 1
fi

if [[ $RESULT_FILE != /* ]]; then
  RESULT_FILE="$ROOT/$RESULT_FILE"
fi

if ! command -v taskset >/dev/null 2>&1; then
  printf 'required command not found: taskset\n' >&2
  exit 1
fi

for executable in \
  "$GRPC_LITE_SERVER" \
  "$CLIENT_BIN" \
  "$ROOT/bin/go-server" \
  "$ROOT/bin/tonic-server" \
  "$ROOT/bin/cpp-server"; do
  if [[ ! -x $executable ]]; then
    printf 'executable not found: %s\n' "$executable" >&2
    exit 1
  fi
done

if ((ROUNDS <= 0)); then
  printf 'ROUNDS must be positive\n' >&2
  exit 1
fi

mkdir -p "$(dirname -- "$RESULT_FILE")"
: >"$RESULT_FILE"

TMP_DIR=$(mktemp -d)
CURRENT_PID=

stop_server() {
  if [[ -n $CURRENT_PID ]] && kill -0 "$CURRENT_PID" 2>/dev/null; then
    kill "$CURRENT_PID" 2>/dev/null || true
    wait "$CURRENT_PID" 2>/dev/null || true
  fi
  CURRENT_PID=
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  stop_server
  rm -rf "$TMP_DIR"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

wait_for_port() {
  local label=$1
  local port=$2
  local log_file=$3
  local attempt

  for ((attempt = 0; attempt < 100; attempt++)); do
    if ! kill -0 "$CURRENT_PID" 2>/dev/null; then
      wait "$CURRENT_PID" 2>/dev/null || true
      printf '%s server exited before port %s became ready\n' "$label" "$port" >&2
      while IFS= read -r line; do
        printf '%s\n' "$line" >&2
      done <"$log_file"
      return 1
    fi
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
      return 0
    fi
    sleep 0.05
  done

  printf '%s server did not listen on port %s\n' "$label" "$port" >&2
  return 1
}

start_server() {
  local label=$1
  local port=$2
  local log_file="$TMP_DIR/$label.log"

  : >"$log_file"
  case "$label" in
    grpc-lite)
      taskset -c "$SERVER_CPU" "$GRPC_LITE_SERVER" \
        --port="$port" --use_tls=false >"$log_file" 2>&1 &
      ;;
    grpc-go)
      GOMAXPROCS=1 taskset -c "$SERVER_CPU" "$ROOT/bin/go-server" \
        -addr="127.0.0.1:$port" >"$log_file" 2>&1 &
      ;;
    tonic)
      SERVER_THREADS=1 SERVER_ADDR="127.0.0.1:$port" \
        taskset -c "$SERVER_CPU" "$ROOT/bin/tonic-server" >"$log_file" 2>&1 &
      ;;
    grpc-cpp)
      taskset -c "$SERVER_CPU" "$ROOT/bin/cpp-server" \
        "127.0.0.1:$port" >"$log_file" 2>&1 &
      ;;
    *)
      printf 'unknown server label: %s\n' "$label" >&2
      return 1
      ;;
  esac
  CURRENT_PID=$!
  wait_for_port "$label" "$port" "$log_file"
}

run_case() {
  local round=$1
  local label=$2
  local port=$3
  local server_version=$4
  local client_output="$TMP_DIR/client.json"
  local json

  printf 'round=%s label=%s port=%s\n' "$round" "$label" "$port" >&2
  start_server "$label" "$port"
  client_args=(
    -addr="127.0.0.1:$port"
    -warmup="$WARMUP"
    -duration="$DURATION"
    -concurrency="$CONCURRENCY"
    -connections="$CONNECTIONS"
    -req_size="$REQ_SIZE"
    -resp_size="$RESP_SIZE"
    -round="$round"
    -label="$label"
    -server_version="$server_version"
    -compact
  )
  if [[ $CLIENT_IMPL == tonic ]]; then
    CLIENT_THREADS="$CLIENT_THREADS" taskset -c "$CLIENT_CPUS" "$CLIENT_BIN" \
      "${client_args[@]}" >"$client_output"
  else
    taskset -c "$CLIENT_CPUS" "$CLIENT_BIN" "${client_args[@]}" >"$client_output"
  fi
  json=$(<"$client_output")
  if [[ -z $json ]]; then
    printf 'client produced no result for %s round %s\n' "$label" "$round" >&2
    return 1
  fi
  printf '%s\n' "$json" >>"$RESULT_FILE"
  stop_server
}

labels=(grpc-lite grpc-go tonic grpc-cpp)
ports=("$GRPC_LITE_PORT" "$GRPC_GO_PORT" "$TONIC_PORT" "$GRPC_CPP_PORT")
versions=("$GRPC_LITE_VERSION" grpc-go/1.82.1 tonic/0.14.6 grpc-cpp/1.48.4)

for ((round = 1; round <= ROUNDS; round++)); do
  for ((offset = 0; offset < ${#labels[@]}; offset++)); do
    index=$(((round - 1 + offset) % ${#labels[@]}))
    run_case "$round" "${labels[index]}" "${ports[index]}" "${versions[index]}"
  done
done

printf 'wrote %s\n' "$RESULT_FILE" >&2
