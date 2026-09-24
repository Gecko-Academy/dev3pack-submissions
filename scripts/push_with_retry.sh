#!/usr/bin/env bash
# Push the current commit to main, rebasing onto whatever landed first.
#
#     bash scripts/push_with_retry.sh
#
# WHY. `finals.yml` and `collect.yml` both push to main, and nothing orders
# them, so one is sometimes refused as non-fast-forward. Before this, the
# refused one failed its run: scores that were already on the API went
# unrecorded here, or a track went unpublished until the next collect.
#
# WHY NOT ONE CONCURRENCY GROUP FOR BOTH. GitHub keeps one PENDING run per group,
# and a newer pending run cancels the older one. A finals dispatch waiting behind
# a collect run could be dropped by the next collect, and that final would never
# be scored. Retrying the push loses nothing.
#
# WHY A REBASE CANNOT CONFLICT. finals only writes `finals/<learner>/`, collect
# only writes `TRACK.md` and `track.json`, and neither job touches the other's
# files. If a rebase does conflict, something else is writing here, and this
# stops rather than guess.
#
# PUSH_ATTEMPTS and PUSH_BACKOFF (seconds, multiplied by the attempt) exist so
# the tests can run this without sleeping.

set -euo pipefail

attempts="${PUSH_ATTEMPTS:-5}"
backoff="${PUSH_BACKOFF:-3}"
branch="${PUSH_BRANCH:-main}"

for attempt in $(seq 1 "$attempts"); do
  if git push origin "HEAD:$branch"; then
    echo "pushed on attempt $attempt"
    exit 0
  fi
  if [ "$attempt" -eq "$attempts" ]; then
    break
  fi
  echo "push refused on attempt $attempt; rebasing onto origin/$branch" >&2
  sleep $((attempt * backoff))
  if ! git pull --rebase origin "$branch"; then
    git rebase --abort 2>/dev/null || true
    echo "::error::rebasing onto origin/$branch conflicted; another job is writing the same files"
    exit 1
  fi
done
echo "::error::the push was refused $attempts times; nothing was pushed"
exit 1
