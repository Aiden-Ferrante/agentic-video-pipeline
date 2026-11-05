#!/usr/bin/env bash
# Run `doit` with a retry around the one failure that is not ours.
#
# doit keeps task state in `.doit.db`, a gdbm file opened exclusively. Anything
# else that opens it — the canvas polling `doit list --status` for freshness
# badges, a second shell — makes our open fail with EAGAIN ("Resource
# temporarily unavailable") BEFORE any task runs. That is a clean failure, so a
# retry is safe; and once we hold the lock a long build keeps it, which only
# costs the poller a stale badge. Usage: ./doit_retry.sh <doit args...>
set -uo pipefail
cd "$(dirname "$0")"
tries=${DOIT_RETRIES:-300}   # the canvas's `doit list --status` poll holds the lock ~14 s on a 100-beat video
for i in $(seq 1 "$tries"); do
  out=$(.venv/bin/python -m doit "$@" 2>&1); rc=$?
  if [ $rc -ne 0 ] && grep -q "Resource temporarily unavailable: '.doit.db'" <<<"$out"; then
    sleep "$(awk -v i="$i" 'BEGIN{printf "%.1f", 0.5 + (i % 5) * 0.7}')"
    continue
  fi
  printf '%s\n' "$out"
  exit $rc
done
echo "doit_retry: .doit.db stayed locked after $tries tries" >&2
exit 75
