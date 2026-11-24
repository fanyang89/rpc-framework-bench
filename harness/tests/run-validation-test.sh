#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)

run_validation() {
  env -i \
    PATH="$PATH" \
    GRPC_LITE_SERVER=/not-used \
    RUN_SH_VALIDATE_ONLY=1 \
    "$@" \
    bash "$ROOT/harness/run.sh"
}

expect_success() {
  if ! run_validation "$@"; then
    printf 'expected validation success: %s\n' "$*" >&2
    exit 1
  fi
}

expect_failure() {
  local expected=$1
  local output
  shift

  if output=$(run_validation "$@" 2>&1); then
    printf 'expected validation failure: %s\n' "$*" >&2
    exit 1
  fi
  if [[ $output != *"$expected"* ]]; then
    printf 'validation error %q does not contain %q\n' "$output" "$expected" >&2
    exit 1
  fi
}

expect_success
expect_success CLIENT_PROCESSES=1 CLIENT_CPU_SETS=ignored CLIENT_CPUS=3-4
expect_success CLIENT_PROCESSES=2 CLIENT_CPU_SETS='0-3,6-7 8-10,12-14'
expect_failure 'CLIENT_PROCESSES must be positive' CLIENT_PROCESSES=0
expect_failure 'CLIENT_PROCESSES must be positive' CLIENT_PROCESSES=two
expect_failure 'CLIENT_PROCESSES must be positive' CLIENT_PROCESSES=08
expect_failure 'exactly 2' CLIENT_PROCESSES=2 CLIENT_CPU_SETS=0-3
expect_failure 'invalid CPU set' CLIENT_PROCESSES=2 CLIENT_CPU_SETS='0-3 not-a-set'
expect_failure 'CONCURRENCY must be positive' CONCURRENCY=0
expect_failure 'CONNECTIONS must be positive' CONNECTIONS=invalid

printf 'run.sh parameter validation passed\n'
