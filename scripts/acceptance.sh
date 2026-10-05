#!/usr/bin/env bash
# 本地验收：迁移 preflight + SQLite 契约 + 一次性 PostgreSQL，并统计覆盖率。
#
# 用法:
#   bash scripts/acceptance.sh [both|--sqlite-only|--pg-only] [--no-coverage]
#   - 覆盖率默认开启；--no-coverage 关闭插桩与报告。
#   - COVERAGE_THRESHOLD 默认 80 (范围 0..100)。
#   - COVERAGE_DB_URL 可选：显式给才把快照写库，绝不指向一次性容器。
#   - 覆盖率工具/报告/JSON 缺失 -> 该后端 coverage 记 SKIP，SKIP 不挡验收；
#     report 脚本非零退出(低于阈值) -> 该后端 coverage 记 FAIL。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="both"; MODE_SET=0; COVERAGE_ENABLED=1

for arg in "$@"; do
  case "$arg" in
    both|--sqlite-only|--pg-only)
      if [[ "$MODE_SET" == "1" ]]; then echo "只能指定一个后端模式。" >&2; exit 2; fi
      MODE="$arg"; MODE_SET=1 ;;
    --no-coverage) COVERAGE_ENABLED=0 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "Usage: bash scripts/acceptance.sh [both|--sqlite-only|--pg-only] [--no-coverage]" >&2; exit 2 ;;
  esac
done

PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || { echo "FAIL: Python not found: $PYTHON"; exit 2; }

"$PYTHON" - <<'PY' || { echo "FAIL: unsupported Python version"; exit 2; }
import sys
if sys.version_info < (3, 10):
    print(f"Python 3.10+ required; found {sys.version.split()[0]}", file=sys.stderr)
    raise SystemExit(1)
print(f"Python {sys.version.split()[0]}")
PY

COVERAGE_THRESHOLD="${COVERAGE_THRESHOLD:-80}"
"$PYTHON" - "$COVERAGE_THRESHOLD" <<'PY' || exit 2
import math, sys
try:
    threshold = float(sys.argv[1])
except ValueError:
    print("COVERAGE_THRESHOLD must be a number in the range 0..100", file=sys.stderr)
    raise SystemExit(1)
if not math.isfinite(threshold) or not 0 <= threshold <= 100:
    print("COVERAGE_THRESHOLD must be a number in the range 0..100", file=sys.stderr)
    raise SystemExit(1)
PY

missing_core="$("$PYTHON" - <<'PY'
import importlib.util
for module, package in (("pytest","pytest"),("pytest_asyncio","pytest-asyncio"),
                        ("aiosqlite","aiosqlite"),("asyncpg","asyncpg")):
    if importlib.util.find_spec(module) is None:
        print(package)
PY
)"
if [[ -n "$missing_core" ]]; then
  echo "Installing missing test dependencies: $missing_core"
  # shellcheck disable=SC2086
  "$PYTHON" -m pip install $missing_core || { echo "FAIL: mandatory dependency installation failed"; exit 2; }
fi

missing_coverage="$("$PYTHON" - <<'PY'
import importlib.util
for module, package in (("pytest_cov","pytest-cov"),("coverage","coverage")):
    if importlib.util.find_spec(module) is None:
        print(package)
PY
)"
if [[ -n "$missing_coverage" ]]; then
  echo "Installing missing coverage dependencies: $missing_coverage"
  # shellcheck disable=SC2086
  "$PYTHON" -m pip install $missing_coverage \
    || echo "WARNING: coverage dependencies unavailable; coverage will be SKIP."
fi

COVERAGE_TOOLS_AVAILABLE=0
if "$PYTHON" - <<'PY'
import coverage
import pytest_cov
PY
then COVERAGE_TOOLS_AVAILABLE=1
elif [[ "$COVERAGE_ENABLED" == "1" ]]; then
  echo "WARNING: pytest-cov/coverage cannot be imported; coverage will be SKIP."
fi

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT_DIR="${ACCEPTANCE_OUT_DIR:-$ROOT/acceptance-logs/$RUN_ID}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

SQLITE_STATUS="SKIP"; PG_STATUS="SKIP"
if [[ "$COVERAGE_ENABLED" == "1" ]]; then
  SQLITE_COVERAGE_STATUS="SKIP"; PG_COVERAGE_STATUS="SKIP"
else
  SQLITE_COVERAGE_STATUS="disabled"; PG_COVERAGE_STATUS="disabled"
fi
CONTAINER_NAME=""

cleanup() {
  if [[ -n "$CONTAINER_NAME" ]] && command -v docker >/dev/null 2>&1; then
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

show_tail() { [[ -f "$1" ]] && { echo "--- last 40 lines: $1 ---"; tail -n 40 "$1"; }; }

run_coverage_report() {
  local backend="$1" coverage_json="$2" summary_json="$3" report_log="$4" status_var="$5"
  local -a report_args

  if [[ "$COVERAGE_ENABLED" != "1" ]]; then printf -v "$status_var" '%s' "disabled"; return; fi
  if [[ "$COVERAGE_TOOLS_AVAILABLE" != "1" ]]; then
    echo "WARNING: coverage tooling unavailable; $backend coverage marked SKIP."
    printf -v "$status_var" '%s' "SKIP"; return
  fi
  if [[ ! -f "$coverage_json" || ! -s "$coverage_json" ]]; then
    echo "WARNING: $backend coverage JSON was not produced; coverage marked SKIP."
    printf -v "$status_var" '%s' "SKIP"; return
  fi
  if [[ ! -f scripts/coverage_report.py ]]; then
    echo "WARNING: scripts/coverage_report.py is missing; coverage marked SKIP."
    printf -v "$status_var" '%s' "SKIP"; return
  fi

  report_args=(scripts/coverage_report.py --coverage "$coverage_json"
    --backend "$backend" --out "$summary_json" --threshold "$COVERAGE_THRESHOLD")
  if [[ -n "${COVERAGE_DB_URL:-}" ]]; then report_args+=(--db "$COVERAGE_DB_URL"); fi

  echo "== $backend coverage report =="
  if "$PYTHON" "${report_args[@]}" 2>&1 | tee "$report_log"; then
    printf -v "$status_var" '%s' "PASS"
  else
    printf -v "$status_var" '%s' "FAIL"; show_tail "$report_log"
  fi
}

run_sqlite() {
  local preflight_log="$OUT_DIR/sqlite-preflight.log"
  local pytest_log="$OUT_DIR/sqlite.log"
  local coverage_json="$OUT_DIR/sqlite-coverage.json"
  local -a pytest_args

  echo "== SQLite migration/boot preflight =="
  if ! env -u DATABASE_URL -u RUN_PG_CONTRACTS -u TEST_DATABASE_URL \
      "$PYTHON" scripts/acceptance_preflight.py sqlite 2>&1 | tee "$preflight_log"; then
    SQLITE_STATUS="FAIL (preflight)"; show_tail "$preflight_log"
    echo "SQLite pytest and coverage skipped: preflight failed."; return
  fi

  echo "== SQLite contracts =="
  pytest_args=(tests/contracts -q --junitxml=report.xml)
  if [[ "$COVERAGE_ENABLED" == "1" && "$COVERAGE_TOOLS_AVAILABLE" == "1" ]]; then
    rm -f "$coverage_json"
    pytest_args+=(--cov=body --cov=migrations --cov=panel --cov=skills
      --cov-report=term-missing "--cov-report=json:$coverage_json")
  elif [[ "$COVERAGE_ENABLED" == "1" ]]; then
    echo "WARNING: running SQLite contracts without coverage instrumentation."
  fi

  if env -u DATABASE_URL -u RUN_PG_CONTRACTS -u TEST_DATABASE_URL \
      "$PYTHON" -m pytest "${pytest_args[@]}" 2>&1 | tee "$pytest_log"; then
    SQLITE_STATUS="PASS"
  else
    SQLITE_STATUS="FAIL (pytest)"; show_tail "$pytest_log"
  fi
  [[ -f report.xml ]] && cp report.xml "$OUT_DIR/sqlite-report.xml"

  if [[ "$COVERAGE_ENABLED" == "1" ]]; then
    run_coverage_report sqlite "$coverage_json" \
      "$OUT_DIR/sqlite-coverage-summary.json" "$OUT_DIR/sqlite-coverage.log" \
      SQLITE_COVERAGE_STATUS
  fi
}

run_postgres() {
  local port db_name dsn ready password
  local preflight_log="$OUT_DIR/postgres-preflight.log"
  local pytest_log="$OUT_DIR/postgres.log"
  local coverage_json="$OUT_DIR/postgres-coverage.json"
  local -a pytest_args

  echo "== PostgreSQL 16 startup =="
  if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
    PG_STATUS="FAIL (Docker unavailable)"; echo "FAIL: Docker CLI/daemon unavailable."; return
  fi

  port="$("$PYTHON" - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"
  db_name="frame_evidence_acceptance_$$"
  CONTAINER_NAME="frame-evidence-pg-$$"
  password="${ACCEPTANCE_PG_PASSWORD:-acceptance_local_only}"   # 仅本地一次性容器; 可用环境变量覆盖

  if ! docker run --detach --rm --name "$CONTAINER_NAME" \
      --publish "127.0.0.1:${port}:5432" \
      --env POSTGRES_DB=postgres --env POSTGRES_USER=postgres \
      --env "POSTGRES_PASSWORD=$password" \
      --health-cmd "pg_isready -U postgres -d postgres" \
      --health-interval 2s --health-timeout 3s --health-retries 30 \
      postgres:16 >"$OUT_DIR/docker-start.log" 2>&1; then
    PG_STATUS="FAIL (container start)"; cat "$OUT_DIR/docker-start.log"; return
  fi

  ready=0
  for _ in $(seq 1 60); do
    docker exec "$CONTAINER_NAME" pg_isready -U postgres -d postgres >/dev/null 2>&1 \
      && { ready=1; break; }; sleep 1
  done
  if [[ "$ready" != "1" ]]; then
    PG_STATUS="FAIL (database not ready)"
    docker logs "$CONTAINER_NAME" >"$OUT_DIR/postgres-container.log" 2>&1 || true
    show_tail "$OUT_DIR/postgres-container.log"; return
  fi

  docker exec "$CONTAINER_NAME" createdb -U postgres "$db_name" \
    || { PG_STATUS="FAIL (database creation)"; return; }

  # asyncpg DSN 刻意不带 query(如 sslmode)
  dsn="postgresql://postgres:${password}@127.0.0.1:${port}/${db_name}"

  echo "== PostgreSQL migration/boot preflight =="
  if ! env -u DATABASE_URL RUN_PG_CONTRACTS=1 TEST_DATABASE_URL="$dsn" \
      "$PYTHON" scripts/acceptance_preflight.py postgres 2>&1 | tee "$preflight_log"; then
    PG_STATUS="FAIL (preflight)"; show_tail "$preflight_log"
    echo "PostgreSQL pytest and coverage skipped: preflight failed."; return
  fi

  echo "== PostgreSQL contracts =="
  pytest_args=(tests/contracts -q --junitxml=report.xml)
  if [[ "$COVERAGE_ENABLED" == "1" && "$COVERAGE_TOOLS_AVAILABLE" == "1" ]]; then
    rm -f "$coverage_json"
    pytest_args+=(--cov=body --cov=migrations --cov=panel --cov=skills
      --cov-report=term-missing "--cov-report=json:$coverage_json")
  elif [[ "$COVERAGE_ENABLED" == "1" ]]; then
    echo "WARNING: running PostgreSQL contracts without coverage instrumentation."
  fi

  if env -u DATABASE_URL RUN_PG_CONTRACTS=1 TEST_DATABASE_URL="$dsn" \
      "$PYTHON" -m pytest "${pytest_args[@]}" 2>&1 | tee "$pytest_log"; then
    PG_STATUS="PASS"
  else
    PG_STATUS="FAIL (pytest)"; show_tail "$pytest_log"
  fi
  [[ -f report.xml ]] && cp report.xml "$OUT_DIR/postgres-report.xml"

  if [[ "$COVERAGE_ENABLED" == "1" ]]; then
    run_coverage_report postgres "$coverage_json" \
      "$OUT_DIR/postgres-coverage-summary.json" "$OUT_DIR/postgres-coverage.log" \
      PG_COVERAGE_STATUS
  fi
}

[[ "$MODE" != "--pg-only" ]] && run_sqlite
[[ "$MODE" != "--sqlite-only" ]] && run_postgres

if [[ "$COVERAGE_ENABLED" != "1" ]]; then
  COVERAGE_SUMMARY="disabled"
else
  COVERAGE_SUMMARY="PASS"
  if { [[ "$MODE" != "--pg-only" && "$SQLITE_COVERAGE_STATUS" == "FAIL" ]] ||
       [[ "$MODE" != "--sqlite-only" && "$PG_COVERAGE_STATUS" == "FAIL" ]]; }; then
    COVERAGE_SUMMARY="FAIL"
  elif { [[ "$MODE" != "--pg-only" && "$SQLITE_COVERAGE_STATUS" == "SKIP" ]] ||
         [[ "$MODE" != "--sqlite-only" && "$PG_COVERAGE_STATUS" == "SKIP" ]]; }; then
    COVERAGE_SUMMARY="SKIP"
  fi
fi

echo
echo "========== ACCEPTANCE =========="
printf "SQLite:              %s\n" "$SQLITE_STATUS"
printf "SQLite coverage:     %s\n" "$SQLITE_COVERAGE_STATUS"
printf "PostgreSQL:          %s\n" "$PG_STATUS"
printf "PostgreSQL coverage: %s\n" "$PG_COVERAGE_STATUS"
printf "Coverage:            %s\n" "$COVERAGE_SUMMARY"
echo "Logs:                $OUT_DIR"

SUITES_OK=0
case "$MODE" in
  both)         [[ "$SQLITE_STATUS" == "PASS" && "$PG_STATUS" == "PASS" ]] && SUITES_OK=1 ;;
  --sqlite-only) [[ "$SQLITE_STATUS" == "PASS" ]] && SUITES_OK=1 ;;
  --pg-only)     [[ "$PG_STATUS" == "PASS" ]] && SUITES_OK=1 ;;
esac

COVERAGE_OK=1
if [[ "$COVERAGE_ENABLED" == "1" && "$COVERAGE_SUMMARY" == "FAIL" ]]; then COVERAGE_OK=0; fi

if [[ "$SUITES_OK" == "1" && "$COVERAGE_OK" == "1" ]]; then
  echo "VERDICT: PASS"; exit 0
fi
echo "VERDICT: FAIL"; exit 1
