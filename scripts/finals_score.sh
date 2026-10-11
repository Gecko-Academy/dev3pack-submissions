#!/usr/bin/env bash
# Score every final listed in a file, and record what came back.
#
#     API=... KEY=... bash scripts/finals_score.sh finals.txt
#
# Writes `finals/<learner>/result.json` for each learner scored, lists those
# files in `scored.txt`, and lists every learner that could not be scored in
# `failed.txt`. Exits 0 when it got through the list, whatever it found; the
# workflow reads `failed.txt` and goes red at the END, after everyone who WAS
# scored has been committed, pushed and announced.
#
# WHY ONE FAILURE NO LONGER STOPS THE BATCH. Collect merges several finals in
# one run, and learners may submit as often as they like. When the first non-200
# ended the loop, one bad bundle left everybody merged beside it unscored, and
# nothing ever retried them.
#
# WHY A FILE AND NOT A HEREDOC IN THE WORKFLOW. It holds the server key, so it
# is worth testing, and a heredoc cannot be (see `check_bundle.py`).
#
# NOTHING HERE EXECUTES LEARNER CONTENT. It reads two JSON files per learner
# with jq and posts one of them.

set -euo pipefail

list="${1:?usage: finals_score.sh <file listing answers.json paths>}"
if [ -z "${KEY:-}" ]; then
  echo "::error::DEV3PACK_SERVER_KEY is not set; nothing was scored"
  exit 1
fi
: "${API:?DEV3PACK_API_BASE is not set}"

: > scored.txt
: > failed.txt

# THE CODE BEHIND THE SCORE. `repo` and `commit` from the bundle's
# submission.json, so a leaderboard can link each score to the code that earned
# it. Re-checked here rather than trusted from the pull-request check, because a
# push by hand never ran it; a value that fails is recorded as null. Slurped, so
# a file holding two documents or none yields nulls rather than a bad --argjson.
LINK_FILTER='
  def repo_ok: type == "string"
    and test("^https://github\\.com/[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
    and (test("/\\.{1,2}$") | not);
  def commit_ok: type == "string" and test("^[0-9a-f]{40}$");
  if length == 1 and (.[0] | type) == "object" then .[0] |
    {repo: (if (.repo | repo_ok) then .repo else null end),
     commit: (if (.commit | commit_ok) then .commit else null end)}
  else {repo: null, commit: null} end'
NO_LINK='{"repo":null,"commit":null}'

# Every fallible command below carries its own `|| ... return 1`. It has to:
# this function is called as an `if` condition, and bash switches `set -e` off
# inside one, so an unchecked failure would fall straight through.
score_one() {
  local path="$1" learner="$2" payload code link submission tmp

  # Never follow a link out of the checkout: this script holds the key.
  if [ -L "$path" ] || [ ! -f "$path" ]; then
    echo "::error::$learner: $path is not a regular file; nothing was posted"
    return 1
  fi
  # The learner is the folder the file sits in, which GitHub already checked
  # when the pull request was opened. The API takes it from here rather than
  # from anything inside the file.
  payload="$(jq -c --arg g "$learner" \
    '{github: $g, set: "final", cohort: (.cohort // "2026-09"),
      course_release: (.course_release // "unknown"), answers: .answers}' "$path")" || {
    echo "::error::$learner: answers.json could not be read; nothing was posted"
    return 1
  }

  # A response left over from the previous learner must never be read as this one's.
  rm -f response.json
  code="$(curl -sS -o response.json -w '%{http_code}' \
    -X POST "$API/api/dev3pack/submit" \
    -H "authorization: Bearer $KEY" \
    -H "content-type: application/json" \
    --data "$payload")" || code="${code:-000}"
  if [ "$code" != "200" ]; then
    echo "::error::$learner scored HTTP $code; nothing was recorded for them"
    cat response.json 2>/dev/null || true
    echo
    return 1
  fi

  link="$NO_LINK"
  submission="$(dirname "$path")/submission.json"
  if [ -f "$submission" ] && [ ! -L "$submission" ]; then
    link="$(jq -c -s "$LINK_FILTER" "$submission" 2>/dev/null)" || link="$NO_LINK"
    [ -n "$link" ] || link="$NO_LINK"
  fi

  # Written aside and moved into place, so a failure here leaves NO result
  # rather than half of one. The recorded result carries no answer text: the
  # API returns per-case verdicts, and that is what lands in the repository.
  mkdir -p "finals/$learner" || return 1
  tmp="$(mktemp "finals/$learner/.result.XXXXXX")" || return 1
  if ! jq --argjson link "$link" \
    '{github, repo: $link.repo, commit: $link.commit, question_set_id,
      score, gates, passed, certificate_eligible, results}' \
    response.json > "$tmp"; then
    rm -f "$tmp"
    echo "::error::$learner: the API answered 200 with a body that is not a result"
    return 1
  fi
  mv "$tmp" "finals/$learner/result.json" || { rm -f "$tmp"; return 1; }

  # What the notification summarises. Written only here, so a learner who
  # failed cannot be announced as if they had been scored.
  echo "finals/$learner/result.json" >> scored.txt
  jq -r '"  \(.github): \(.score.percent)% — passed=\(.passed) — certificate=\(.certificate_eligible)"' \
    response.json || true
}

while read -r path; do
  [ -n "$path" ] || continue
  learner="$(echo "$path" | cut -d/ -f2)"
  echo "scoring $learner"
  if ! score_one "$path" "$learner"; then
    echo "$learner" >> failed.txt
  fi
done < "$list"

echo "scored $(wc -l < scored.txt), failed $(wc -l < failed.txt)"
