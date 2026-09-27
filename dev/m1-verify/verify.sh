#!/bin/sh
# Release check for the M1/M2 fork: build, engine tests, kernel policy, greedy
# reference outputs, throughput, prefix cache and SSD tier, in one run.
#
# usage: dev/m1-verify/verify.sh --out DIR (--record | --reference DIR)
#            [--root DIR] [--skip-tests] [--long] [--no-disk]
#
#   --record         save this build's greedy outputs and benchmark as a reference
#   --reference DIR  compare against a reference saved with --record
#   --root DIR       check the checkout at DIR (default: this script's own)
#   --long           also prefill one ~110K prompt (about 30 min on an M1 Max)
#
# Environment: SPLASH_VERIFY_PORT (8011), SPLASH_VERIFY_FAMILY (7),
# SPLASH_VERIFY_DISK_MEMORY (--max-memory for the SSD test, 20G),
# SPLASH_VERIFY_PROSE (prose corpus; see common.py), SPLASH_VERIFY_TOLERANCE (0.05).
# Only one Splash server may run; loaded Ollama models are stopped first.
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd -P)
PORT=${SPLASH_VERIFY_PORT:-8011}
FAMILY=${SPLASH_VERIFY_FAMILY:-7}
DISK_MEMORY=${SPLASH_VERIFY_DISK_MEMORY:-20G}
TOLERANCE=${SPLASH_VERIFY_TOLERANCE:-0.05}
MODEL=${SPLASH_VERIFY_MODEL:-incoai/Qwen3.8-27B-Splash}
OUT= REFERENCE= RECORD=0 TESTS=1 LONG=0 DISK=1
while [ $# -gt 0 ]; do
    case $1 in
        --out) OUT=$2; shift ;;
        --root) ROOT=$(CDPATH= cd -- "$2" && pwd -P); shift ;;
        --reference) REFERENCE=$2; shift ;;
        --record) RECORD=1 ;;
        --skip-tests) TESTS=0 ;;
        --long) LONG=1 ;;
        --no-disk) DISK=0 ;;
        *) sed -n '5,18p' "$0" >&2; exit 2 ;;
    esac
    shift
done
if [ -z "$OUT" ] || { [ $RECORD = 0 ] && [ -z "$REFERENCE" ]; }; then
    sed -n '5,18p' "$0" >&2; exit 2
fi
mkdir -p "$OUT"
OUT=$(CDPATH= cd -- "$OUT" && pwd -P)
[ -n "$REFERENCE" ] && REFERENCE=$(CDPATH= cd -- "$REFERENCE" && pwd -P)
PY="$ROOT/.venv/bin/python"
export SPLASH_VERIFY_URL="http://127.0.0.1:$PORT" SPLASH_VERIFY_MODEL="$MODEL"
SUMMARY="$OUT/summary.txt"
: > "$SUMMARY"
FAILED=0
SERVER=

result() {  # result NAME STATUS [DETAIL]
    printf '%-6s %s%s\n' "$2" "$1" "${3:+  ($3)}" | tee -a "$SUMMARY"
    [ "$2" = PASS ] || [ "$2" = INFO ] || FAILED=1
}

stop_server() {
    [ -n "$SERVER" ] || return 0
    kill -INT "$SERVER" 2>/dev/null
    i=0
    while kill -0 "$SERVER" 2>/dev/null && [ $i -lt 120 ]; do sleep 1; i=$((i + 1)); done
    kill -TERM "$SERVER" 2>/dev/null
    wait "$SERVER" 2>/dev/null
    SERVER=
}
trap stop_server EXIT INT TERM

start_server() {  # start_server LOG [serve options...]
    log=$1; shift
    if pgrep -f 'serve-native' > /dev/null || lsof -nP -iTCP:"$PORT" -sTCP:LISTEN > /dev/null 2>&1; then
        echo "another Splash server is listening; stop it first" >&2; exit 2
    fi
    "$ROOT/splash" serve --model "$MODEL" --port "$PORT" --no-webui "$@" > "$log" 2>&1 &
    SERVER=$!
    "$PY" -c "import sys; sys.path.insert(0, '$HERE'); import common; common.wait_ready()" || {
        tail -20 "$log" >&2; exit 1; }
}

cd "$ROOT" || exit 2
echo "== $(git log -1 --format='%h %s') ($(git describe --tags --always))" | tee -a "$SUMMARY"
if command -v ollama > /dev/null; then
    for m in $(ollama ps 2>/dev/null | awk 'NR > 1 {print $1}'); do ollama stop "$m"; done
fi

if make -j8 > "$OUT/make.log" 2>&1; then result build PASS; else result build FAIL "$OUT/make.log"; exit 1; fi
if [ $TESTS = 1 ]; then
    if make -j8 test-engine > "$OUT/test-engine.log" 2>&1; then result test-engine PASS
    else result test-engine FAIL "$OUT/test-engine.log"; fi
fi

start_server "$OUT/server.log"
policy=$(grep -o 'Kernel policy for GPU family [0-9]* with [0-9]* cores' "$OUT/server.log" | head -1)
case $policy in
    "Kernel policy for GPU family $FAMILY "*) result kernel-policy PASS "$policy" ;;
    *) result kernel-policy FAIL "${policy:-no policy line}" ;;
esac

if [ $RECORD = 1 ]; then
    "$PY" "$HERE/greedy.py" record "$OUT/greedy.json" && result greedy-record PASS || result greedy-record FAIL
else
    "$PY" "$HERE/greedy.py" compare "$REFERENCE/greedy.json" "$OUT/greedy.json" > "$OUT/greedy.txt"
    rc=$?; cat "$OUT/greedy.txt"
    [ $rc = 0 ] && result greedy-match PASS || result greedy-match FAIL "differences in $OUT/greedy.txt; explain before use"
fi

"$PY" "$HERE/bench.py" run "$OUT/bench.jsonl" prose:2000 code:2000 prose:32000 code:32000 \
    || result bench FAIL
# Decode at depth: eight continuations of one cached 32K context (see bench.py).
"$PY" "$HERE/bench.py" accept "$OUT/accept.jsonl" prose:32000 || result bench FAIL
if [ $RECORD = 0 ]; then
    "$PY" "$HERE/bench.py" compare "$REFERENCE" "$OUT" --tolerance "$TOLERANCE" > "$OUT/bench.txt"
    rc=$?; cat "$OUT/bench.txt"
    [ $rc = 0 ] && result bench PASS "within $TOLERANCE of reference" || result bench FAIL "$OUT/bench.txt"
fi

"$PY" "$HERE/cachetest.py" "$OUT/cachetest.jsonl" && result prefix-cache PASS || result prefix-cache FAIL
if [ $LONG = 1 ]; then
    "$PY" "$HERE/bench.py" run "$OUT/long.jsonl" prose:110000 \
        && result long-context PASS "$(tail -1 "$OUT/long.jsonl")" || result long-context FAIL
fi
stop_server

if [ $DISK = 0 ]; then
    :
elif ! "$ROOT/splash" serve --help 2>&1 | grep -q max-cache-disk; then
    result disk-cache INFO "this build has no --max-cache-disk"
else
    start_server "$OUT/server-disk.log" --max-cache-disk 20G --max-memory "$DISK_MEMORY"
    "$PY" "$HERE/disktest.py" "$OUT/disktest.jsonl" && result disk-cache PASS || result disk-cache FAIL
    stop_server
fi

[ $FAILED = 0 ] && echo "ALL PASSED" | tee -a "$SUMMARY" || echo "FAILURES: see $SUMMARY" | tee -a "$SUMMARY"
exit $FAILED
