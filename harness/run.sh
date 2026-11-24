#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

: "${GRPC_LITE_SERVER:?GRPC_LITE_SERVER must point to the grpc-lite interop server}"

GRPC_LITE_VERSION=${GRPC_LITE_VERSION:-grpc-lite/unknown}
GRPC_LITE_PORT=${GRPC_LITE_PORT:-25051}
GRPC_GO_PORT=${GRPC_GO_PORT:-25052}
TONIC_PORT=${TONIC_PORT:-25053}
GRPC_CPP_PORT=${GRPC_CPP_PORT:-25054}
SERVER_CPU=${SERVER_CPU:-15}
CLIENT_CPUS=${CLIENT_CPUS:-8-14}
CLIENT_PROCESSES=${CLIENT_PROCESSES:-1}
CLIENT_CPU_SETS=${CLIENT_CPU_SETS:-}
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
MODE=${MODE:-unary}
MESSAGES=${MESSAGES:-16}
SERVER_THREADS=${SERVER_THREADS:-1}
TLS=${TLS:-0}
TLS_CERT=${TLS_CERT:-}
TLS_KEY=${TLS_KEY:-}
TLS_CA=${TLS_CA:-}
CPP_API=${CPP_API:-sync}
FRAMEWORKS=${FRAMEWORKS:-"grpc-lite grpc-go tonic grpc-cpp"}
for framework in $FRAMEWORKS; do
  case "$framework" in grpc-lite | grpc-go | tonic | grpc-cpp) ;; *) exit 1 ;; esac
done

case "$MODE" in unary | client-stream | server-stream | bidi) ;; *) exit 1 ;; esac
case "$CPP_API" in sync | callback) ;; *) exit 1 ;; esac
case "$TLS" in 0 | 1) ;; *) exit 1 ;; esac
if [[ ! $SERVER_THREADS =~ ^[1-9][0-9]*$ || ! $MESSAGES =~ ^[1-9][0-9]*$ ]] || ((MESSAGES > 1024)); then
  printf 'invalid SERVER_THREADS or MESSAGES\n' >&2
  exit 1
fi
if [[ $MODE != unary && $SERVER_THREADS != 1 ]]; then
  printf 'grpc-lite interop streaming requires one reactor\n' >&2
  exit 1
fi
if [[ $TLS == 1 ]]; then
  for file in "$TLS_CERT" "$TLS_KEY" "$TLS_CA"; do
    [[ -r $file ]] || { printf 'unreadable TLS file: %s\n' "$file" >&2; exit 1; }
  done
fi

if [[ ! $CLIENT_PROCESSES =~ ^[1-9][0-9]*$ ]]; then
  printf 'CLIENT_PROCESSES must be positive\n' >&2
  exit 1
fi
if [[ ! $ROUNDS =~ ^[1-9][0-9]*$ ]]; then
  printf 'ROUNDS must be positive\n' >&2
  exit 1
fi
if [[ ! $CONCURRENCY =~ ^[1-9][0-9]*$ ]]; then
  printf 'CONCURRENCY must be positive\n' >&2
  exit 1
fi
if [[ ! $CONNECTIONS =~ ^[1-9][0-9]*$ ]]; then
  printf 'CONNECTIONS must be positive\n' >&2
  exit 1
fi

declare -a CONFIGURED_CLIENT_CPU_SETS
if ((CLIENT_PROCESSES == 1)); then
  CONFIGURED_CLIENT_CPU_SETS=("$CLIENT_CPUS")
else
  read -r -a CONFIGURED_CLIENT_CPU_SETS <<<"$CLIENT_CPU_SETS"
  if ((${#CONFIGURED_CLIENT_CPU_SETS[@]} != CLIENT_PROCESSES)); then
    printf 'CLIENT_CPU_SETS must contain exactly %s space-separated CPU sets\n' "$CLIENT_PROCESSES" >&2
    exit 1
  fi
  for cpu_set in "${CONFIGURED_CLIENT_CPU_SETS[@]}"; do
    if [[ ! $cpu_set =~ ^[0-9]+(-[0-9]+)?(,[0-9]+(-[0-9]+)?)*$ ]]; then
      printf 'CLIENT_CPU_SETS contains invalid CPU set: %s\n' "$cpu_set" >&2
      exit 1
    fi
  done
fi

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

if [[ $CLIENT_IMPL == tonic && ! $CLIENT_THREADS =~ ^[1-9][0-9]*$ ]]; then
  printf 'CLIENT_THREADS must be positive\n' >&2
  exit 1
fi

if [[ ${RUN_SH_VALIDATE_ONLY:-0} == 1 ]]; then
  exit 0
fi

if [[ $RESULT_FILE != /* ]]; then
  RESULT_FILE="$ROOT/$RESULT_FILE"
fi

for command_name in taskset jq getconf date; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'required command not found: %s\n' "$command_name" >&2
    exit 1
  fi
done

CLK_TCK=$(getconf CLK_TCK)
if [[ ! $CLK_TCK =~ ^[0-9]+$ ]] || ((CLK_TCK <= 0)); then
  printf 'getconf CLK_TCK returned an invalid value: %s\n' "$CLK_TCK" >&2
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

mkdir -p "$(dirname -- "$RESULT_FILE")"
: >"$RESULT_FILE"

TMP_DIR=$(mktemp -d)
CURRENT_PID=
CURRENT_STARTTIME=
declare -a CLIENT_PIDS=()
declare -a CLIENT_STARTTIMES=()

read_server_stat() {
  local pid=$1
  local expected_starttime=${2:-}
  local stat_line stat_tail
  local -a stat_fields

  if ! IFS= read -r stat_line <"/proc/$pid/stat"; then
    printf 'cannot read server process stat for PID %s\n' "$pid" >&2
    return 1
  fi
  if [[ ! $stat_line =~ ^[0-9]+\ \(.*\)\ (.*)$ ]]; then
    printf 'cannot parse server process stat for PID %s\n' "$pid" >&2
    return 1
  fi

  stat_tail=${BASH_REMATCH[1]}
  read -r -a stat_fields <<<"$stat_tail"
  # stat_fields starts at proc stat field 3 (state): indexes 11, 12, and 19
  # are fields 14 (utime), 15 (stime), and 22 (starttime), respectively.
  if ((${#stat_fields[@]} <= 19)) ||
    [[ ! ${stat_fields[11]} =~ ^[0-9]+$ ]] ||
    [[ ! ${stat_fields[12]} =~ ^[0-9]+$ ]] ||
    [[ ! ${stat_fields[19]} =~ ^[0-9]+$ ]]; then
    printf 'server process stat has invalid CPU or identity fields for PID %s\n' "$pid" >&2
    return 1
  fi
  if [[ ${stat_fields[0]} == Z || ${stat_fields[0]} == X ]]; then
    printf 'server process PID %s exited before CPU sampling completed\n' "$pid" >&2
    return 1
  fi
  if [[ -n $expected_starttime && ${stat_fields[19]} != "$expected_starttime" ]]; then
    printf 'server process PID %s identity changed\n' "$pid" >&2
    return 1
  fi

  SERVER_STAT_TICKS=$((${stat_fields[11]} + ${stat_fields[12]}))
  SERVER_STAT_STARTTIME=${stat_fields[19]}
}

server_is_current() {
  [[ -n $CURRENT_PID && -n $CURRENT_STARTTIME ]] &&
    read_server_stat "$CURRENT_PID" "$CURRENT_STARTTIME" 2>/dev/null
}

client_is_current() {
  local pid=$1
  local starttime=$2

  [[ -n $pid && -n $starttime ]] &&
    read_server_stat "$pid" "$starttime" 2>/dev/null
}

sample_server_cpu() {
  if ! read_server_stat "$CURRENT_PID" "$CURRENT_STARTTIME"; then
    return 1
  fi
  SERVER_SAMPLE_TICKS=$SERVER_STAT_TICKS
  SERVER_SAMPLE_WALL_NS=$(date +%s%N)
  if [[ ! $SERVER_SAMPLE_WALL_NS =~ ^[0-9]+$ ]]; then
    printf 'date returned an invalid nanosecond timestamp: %s\n' "$SERVER_SAMPLE_WALL_NS" >&2
    return 1
  fi
}

stop_server() {
  if server_is_current; then
    kill "$CURRENT_PID" 2>/dev/null || true
  fi
  if [[ -n $CURRENT_PID ]]; then
    wait "$CURRENT_PID" 2>/dev/null || true
  fi
  CURRENT_PID=
  CURRENT_STARTTIME=
}

cleanup_clients() {
  local index pid starttime

  for index in "${!CLIENT_PIDS[@]}"; do
    pid=${CLIENT_PIDS[index]}
    starttime=${CLIENT_STARTTIMES[index]:-}
    if client_is_current "$pid" "$starttime"; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  for pid in "${CLIENT_PIDS[@]}"; do
    wait "$pid" 2>/dev/null || true
  done
  CLIENT_PIDS=()
  CLIENT_STARTTIMES=()
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  cleanup_clients
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
    if ! server_is_current; then
      wait "$CURRENT_PID" 2>/dev/null || true
      printf '%s server exited before port %s became ready\n' "$label" "$port" >&2
      while IFS= read -r line; do
        printf '%s\n' "$line" >&2
      done <"$log_file"
      return 1
    fi
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
      if server_is_current; then
        return 0
      fi
      printf '%s server exited while port %s became reachable\n' "$label" "$port" >&2
      return 1
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

  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    printf 'refusing to start %s: port %s is already in use\n' "$label" "$port" >&2
    return 1
  fi

  : >"$log_file"
  local -a lite_tls=(--use_tls=false) go_tls=() lite_reactors=()
  if [[ $SERVER_THREADS != 1 ]]; then
    lite_reactors=("--reactors=$SERVER_THREADS")
  fi
  if [[ $TLS == 1 ]]; then
    lite_tls=(--use_tls=true "--tls_cert_file=$TLS_CERT" "--tls_key_file=$TLS_KEY")
    go_tls=("-cert=$TLS_CERT" "-key=$TLS_KEY")
  fi
  # taskset execs its command in place, so $! is the actual server PID once ready.
  case "$label" in
    grpc-lite)
      taskset -c "$SERVER_CPU" "$GRPC_LITE_SERVER" \
        --port="$port" "${lite_reactors[@]}" "${lite_tls[@]}" >"$log_file" 2>&1 &
      ;;
    grpc-go)
      GOMAXPROCS="$SERVER_THREADS" taskset -c "$SERVER_CPU" "$ROOT/bin/go-server" \
        -addr="127.0.0.1:$port" "${go_tls[@]}" >"$log_file" 2>&1 &
      ;;
    tonic)
      SERVER_THREADS="$SERVER_THREADS" SERVER_ADDR="127.0.0.1:$port" \
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
  if ! read_server_stat "$CURRENT_PID"; then
    kill "$CURRENT_PID" 2>/dev/null || true
    wait "$CURRENT_PID" 2>/dev/null || true
    CURRENT_PID=
    return 1
  fi
  CURRENT_STARTTIME=$SERVER_STAT_STARTTIME
  wait_for_port "$label" "$port" "$log_file"
}

start_client() {
  local index=$1
  local cpu_set=$2
  local output_file=$3
  local pid
  shift 3

  : >"$output_file"
  if [[ $CLIENT_IMPL == tonic ]]; then
    CLIENT_THREADS="$CLIENT_THREADS" taskset -c "$cpu_set" "$CLIENT_BIN" "$@" >"$output_file" &
  else
    taskset -c "$cpu_set" "$CLIENT_BIN" "$@" >"$output_file" &
  fi
  pid=$!
  CLIENT_PIDS[index]=$pid
  if ! read_server_stat "$pid"; then
    wait "$pid" 2>/dev/null || true
    return 1
  fi
  CLIENT_STARTTIMES[index]=$SERVER_STAT_STARTTIME
}

run_case() {
  local round=$1
  local label=$2
  local port=$3
  local server_version=$4
  local client_status=0 end_sample_status index status
  local start_ticks start_wall_ns end_ticks end_wall_ns
  local cpu_ticks wall_ns
  local json
  local -a client_args client_outputs

  printf 'round=%s label=%s port=%s\n' "$round" "$label" "$port" >&2
  start_server "$label" "$port"
  if ! sample_server_cpu; then
    stop_server
    return 1
  fi
  start_ticks=$SERVER_SAMPLE_TICKS
  start_wall_ns=$SERVER_SAMPLE_WALL_NS
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
    -mode="$MODE"
    -messages="$MESSAGES"
    -tls_ca="$([[ $TLS == 1 ]] && printf '%s' "$TLS_CA" || true)"
    -compact
  )
  CLIENT_PIDS=()
  CLIENT_STARTTIMES=()
  for ((index = 0; index < CLIENT_PROCESSES; index++)); do
    client_outputs[index]="$TMP_DIR/client-$index.json"
    if ! start_client "$index" "${CONFIGURED_CLIENT_CPU_SETS[index]}" \
      "${client_outputs[index]}" "${client_args[@]}"; then
      printf 'client %s failed to start for %s round %s\n' "$index" "$label" "$round" >&2
      cleanup_clients
      stop_server
      return 1
    fi
  done

  for index in "${!CLIENT_PIDS[@]}"; do
    if wait "${CLIENT_PIDS[index]}"; then
      status=0
    else
      status=$?
    fi
    if ((status != 0)); then
      printf 'client %s failed for %s round %s with status %s\n' \
        "$index" "$label" "$round" "$status" >&2
      if ((client_status == 0)); then
        client_status=$status
      fi
    fi
  done
  CLIENT_PIDS=()
  CLIENT_STARTTIMES=()

  end_sample_status=0
  sample_server_cpu || end_sample_status=$?
  end_ticks=${SERVER_SAMPLE_TICKS:-0}
  end_wall_ns=${SERVER_SAMPLE_WALL_NS:-0}
  stop_server

  if ((end_sample_status != 0)); then
    return "$end_sample_status"
  fi
  if ((client_status != 0)); then
    return "$client_status"
  fi

  cpu_ticks=$((end_ticks - start_ticks))
  wall_ns=$((end_wall_ns - start_wall_ns))
  if ((cpu_ticks < 0 || wall_ns <= 0)); then
    printf 'invalid server CPU sample delta for %s round %s\n' "$label" "$round" >&2
    return 1
  fi

  if ((CLIENT_PROCESSES == 1)); then
    if ! json=$(jq -cse \
      --argjson cpu_ticks "$cpu_ticks" \
      --argjson clk_tck "$CLK_TCK" \
      --argjson wall_ns "$wall_ns" \
      'if length == 1 and (.[0] | type == "object") then
         .[0] + {
           server_cpu: {
             cpu_seconds: ($cpu_ticks / $clk_tck),
             wall_seconds: ($wall_ns / 1000000000),
             utilization_percent: (($cpu_ticks / $clk_tck) / ($wall_ns / 1000000000) * 100)
           }
         }
       else
         error("client output must contain exactly one JSON object")
       end' "${client_outputs[0]}"); then
      printf 'client produced invalid JSON for %s round %s\n' "$label" "$round" >&2
      return 1
    fi
  elif ! json=$(jq -cse \
    --argjson client_processes "$CLIENT_PROCESSES" \
    --argjson concurrency_per_process "$CONCURRENCY" \
    --argjson connections_per_process "$CONNECTIONS" \
    --argjson cpu_ticks "$cpu_ticks" \
    --argjson clk_tck "$CLK_TCK" \
    --argjson wall_ns "$wall_ns" \
    '.[0] as $first |
     if length != $client_processes or any(.[]; type != "object") then
       error("client outputs must contain one JSON object per process")
     elif any(.[];
       .round != $first.round or
       .label != $first.label or
       .client_framework_version != $first.client_framework_version or
       .server_version != $first.server_version or
       .config != $first.config or
       .config.concurrency != $concurrency_per_process or
       .config.connections != $connections_per_process or
       (.completed | type) != "number" or
       (.errors | type) != "number" or
       (.ops_per_second | type) != "number") then
       error("client outputs have inconsistent metadata, config, or metrics")
     else
       {
         round: $first.round,
         label: $first.label,
         client_framework_version: $first.client_framework_version,
         server_version: $first.server_version,
         config: ($first.config + {
           client_processes: $client_processes,
           concurrency_per_process: $concurrency_per_process,
           connections_per_process: $connections_per_process,
           total_concurrency: ($client_processes * $concurrency_per_process),
           total_connections: ($client_processes * $connections_per_process)
         }),
         completed: (map(.completed) | add),
         errors: (map(.errors) | add),
         ops_per_second: (map(.ops_per_second) | add),
         p50_us: null,
         p95_us: null,
         p99_us: null,
         min_us: null,
         max_us: null,
         mean_us: null,
         process_results: (to_entries | map({
           index: .key,
           completed: .value.completed,
           errors: .value.errors,
           ops_per_second: .value.ops_per_second,
           p50_us: .value.p50_us,
           p95_us: .value.p95_us,
           p99_us: .value.p99_us,
           min_us: .value.min_us,
           max_us: .value.max_us,
           mean_us: .value.mean_us
         })),
         server_cpu: {
           cpu_seconds: ($cpu_ticks / $clk_tck),
           wall_seconds: ($wall_ns / 1000000000),
           utilization_percent: (($cpu_ticks / $clk_tck) / ($wall_ns / 1000000000) * 100)
         }
       }
     end' "${client_outputs[@]}"); then
    printf 'client produced invalid JSON for %s round %s\n' "$label" "$round" >&2
    return 1
  fi
   jq -ce --arg mode "$MODE" --argjson tls "$TLS" --arg api "$CPP_API" \
     --argjson threads "$SERVER_THREADS" --arg cpus "$SERVER_CPU" \
     --arg clients "${CONFIGURED_CLIENT_CPU_SETS[*]}" \
     --argjson start "$start_wall_ns" --argjson end "$end_wall_ns" \
     '.config += {mode: $mode, tls: ($tls == 1), cpp_api: $api,
       server_threads: $threads, server_cpus: $cpus, client_cpu_sets: $clients}
      | .measurement_window = {start_unix_ns: $start, end_unix_ns: $end}
      | if .errors != 0 or .completed <= 0 then error("failed or empty measurement") else . end' \
     <<<"$json" >>"$RESULT_FILE"
}

labels=(grpc-lite grpc-go tonic grpc-cpp)
export CPP_API
if [[ $TLS == 1 ]]; then
  export TLS_CERT TLS_KEY
else
  unset TLS_CERT TLS_KEY
fi
ports=("$GRPC_LITE_PORT" "$GRPC_GO_PORT" "$TONIC_PORT" "$GRPC_CPP_PORT")
versions=("$GRPC_LITE_VERSION" grpc-go/1.82.1 tonic/0.14.6 "${GRPC_CPP_VERSION:-grpc-cpp/1.48.4}")

for ((round = 1; round <= ROUNDS; round++)); do
  for ((offset = 0; offset < ${#labels[@]}; offset++)); do
    index=$(((round - 1 + offset) % ${#labels[@]}))
    [[ " $FRAMEWORKS " == *" ${labels[index]} "* ]] || continue
    run_case "$round" "${labels[index]}" "${ports[index]}" "${versions[index]}"
  done
done

printf 'wrote %s\n' "$RESULT_FILE" >&2
