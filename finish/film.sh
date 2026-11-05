#!/usr/bin/env bash
# The finish stage, in order, for one video (FINISH_SLUG, default four-slots). Run after `doit` has built the
# fit clips and voice. The doit route is `doit film:<slug>`; this is the manual one.
# Usage: finish/film.sh [--score] [act numbers…]   (no acts = every act)
#   --score   regenerate the score for the named acts first (GPU, ~1 min per act); otherwise reuse paths.SCORE
set -euo pipefail
cd "$(dirname "$0")"
VP=$(cd .. && pwd); PY="$VP/.venv/bin/python"
SA_PY="${STABLE_AUDIO_PY:-${STABLE_AUDIO_ROOT:-$HOME/models/stable-audio}/.venv/bin/python}"
SCORE=0; [ "${1:-}" = "--score" ] && { SCORE=1; shift; }
"$PY" acts_plan.py
ACTS=("$@"); [ ${#ACTS[@]} -eq 0 ] && mapfile -t ACTS < <("$PY" -c "import json,paths;print('\n'.join(str(a['n']) for a in json.loads(paths.ACTS_JSON.read_text())))")
if [ $SCORE = 1 ]; then
  "$SA_PY" score.py $("$PY" -c "import json,paths,sys;a=json.loads(paths.ACTS_JSON.read_text());w=set(sys.argv[1:]);print(' '.join(x['slug'] for x in a if str(x['n']) in w))" "${ACTS[@]}")
fi
echo "== picture"; "$PY" act_picture.py "${ACTS[@]}"
echo "== sfx";     "$SA_PY" sfx_mix.py "${ACTS[@]}"
echo "== voice";   "$PY" act_voice.py "${ACTS[@]}"
echo "== film";    "$PY" film.py
