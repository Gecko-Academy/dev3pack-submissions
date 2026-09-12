"""Tell a subscriber that the track moved, so nobody has to poll for it.

    python3 scripts/notify.py --previous <sha> --commit <sha>   # after a push
    python3 scripts/notify.py --finals <list> --commit <sha>    # after a final
    python3 scripts/notify.py --ping --commit <sha>             # prove the wiring

TWO EVENTS, BECAUSE THERE ARE TWO SCORE SURFACES AND THEY HAVE DIFFERENT
OWNERS. `track.updated` carries per-session scores, whose record is this
repository. `final.scored` carries final-assessment scores, whose record is the
course API, because they are graded against a question set that cannot ship to
anybody. A subscriber wants telling either way, so both come down one pipe with
one envelope and one secret.

WHAT THIS IS FOR. `track.json` is public and pollable, and that stays true: the
raw URL is still the whole interface and needs no key. But a consumer that keys
its own rows by its own user ids would rather be told than ask, so this posts a
short, signed notification whenever a merge actually changed somebody's score.

WHAT IT SENDS, AND WHAT IT DOES NOT. A pointer and a summary. The body names the
commit and a raw URL PINNED TO THAT COMMIT, and the summary says which
(github, item) rows moved. It is deliberately not the whole track: a redelivery,
a retry or a notification that arrives out of order all resolve the same way,
by reading the pinned URL, which cannot have changed since. Treat the body as a
hint and the URL as the truth and there is no state to reconcile.

WHY IT MAY HOLD A SECRET. It runs from `collect.yml`, on a schedule, from `main`,
on reviewed code, and only after the commit it announces is already pushed. It
never checks out a fork and never executes a submitted notebook. THE SECRET MUST
NEVER REACH `verify.yml`, which is the workflow that sees unmerged content from a
fork. That is the same rule this repository already enforces against
`pull_request_target`, and it is the same reason.

WHY IT ANNOUNCES ONLY AFTER THE PUSH. A notification names a commit and a raw
URL built from it. Sending before the push would name a commit that is not
public yet, and the subscriber's first read would 404.

DELIVERY IS AT-LEAST-ONCE AND UNORDERED. Three attempts with backoff, then the
step goes red and the run stays green, because a subscriber being down is not a
reason to make a cohort's merges look broken. Nothing re-sends automatically;
`notify.yml` replays a delivery by hand, and a subscriber that missed one is
never wrong for long because the next change carries a newer commit.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: This repository, named once, and the same string `collect.py` uses.
REPO = "Gecko-Academy/dev3pack-submissions"
#: The document this is all about.
TRACK = "track.json"
#: The envelope version. Bump it only for a change a receiver must notice.
SCHEMA = "dev3pack.webhook.v1"
#: What `track.json` must say it is before anything here reads it.
TRACK_SCHEMA = "dev3pack.track.v1"

#: A ceiling on the summary, not on the change. The first run of a cohort can
#: move hundreds of rows, and a subscriber does not need them inline when the
#: pinned URL holds every one of them.
MAX_CHANGED = 500

#: Attempt, then wait, then attempt. A subscriber redeploying is the common case
#: and it is usually back within a minute.
BACKOFF = (2, 6, 18)
TIMEOUT = 20


class NotifyError(Exception):
    """Nothing was delivered, and the reason is in the message."""


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise NotifyError(f"git {' '.join(args)}\n{result.stderr.strip()}")
    return result.stdout


def track_at(ref: str) -> dict:
    """The track as of a commit, or an empty one when there was no track yet."""
    if ref in {"", "none"}:
        return {"entries": []}
    try:
        raw = git("show", f"{ref}:{TRACK}")
    except NotifyError:
        # A commit from before this file existed is not an error: everything in
        # the new track is then genuinely new, which is what an empty one says.
        return {"entries": []}
    payload = json.loads(raw)
    if payload.get("schema") != TRACK_SCHEMA:
        raise NotifyError(f"{ref}:{TRACK} says schema {payload.get('schema')!r}")
    return payload


def by_key(track: dict) -> dict:
    """One entry per (github, item), which is what the track guarantees."""
    return {(entry["github"], entry["item"]): entry for entry in track.get("entries", [])}


def summarise(entry: dict, change: str) -> dict:
    """The fields a subscriber needs to decide whether to re-read, and no more.

    `tier` travels with every row on purpose. A score without it reads as a
    mark, and `claimed` means self-reported and shape-checked: nothing re-runs a
    notebook yet.
    """
    return {
        "change": change,
        "github": entry["github"],
        "item": entry["item"],
        "scored": entry["scored"],
        "score": entry["score"],
        "max_score": entry["max_score"],
        "tier": entry["tier"],
        "submitted_at": entry.get("submitted_at"),
    }


def changes(previous: dict, current: dict) -> list[dict]:
    """What moved between two tracks, ordered so a person can read the list.

    A removal is reported rather than skipped. It is rare and it is never a
    zero: it means a submission left the tree, and a subscriber that only ever
    upserts would otherwise keep showing a score the repository no longer holds.
    """
    before, after = by_key(previous), by_key(current)
    moved: list[dict] = []
    for key in sorted(after):
        if key not in before:
            moved.append(summarise(after[key], "added"))
        elif before[key] != after[key]:
            moved.append(summarise(after[key], "updated"))
    for key in sorted(before):
        if key not in after:
            moved.append(summarise(before[key], "removed"))
    return moved


#: Where a scored final lands in this repository, and the API that is its record.
FINALS_API = "https://app.geckovision.tech/api/dev3pack/finals"


def finals_from(paths: list[str]) -> list[dict]:
    """What was scored, from the result files this push wrote.

    WHAT IS DELIBERATELY DROPPED: `results`, the per-case verdicts. The course
    tells partners it never sends per-question results, and a notification is
    not the place to start. The repository keeps them; this carries the score,
    both gates, and whether a certificate may be issued.
    """
    scored: list[dict] = []
    for path in paths:
        if not path:
            continue
        document = json.loads((ROOT / path).read_text(encoding="utf-8"))
        scored.append(
            {
                "github": document["github"],
                "question_set_id": document.get("question_set_id"),
                "score": document.get("score"),
                "gates": document.get("gates"),
                "passed": document.get("passed"),
                "certificate_eligible": document.get("certificate_eligible"),
            }
        )
    return sorted(scored, key=lambda entry: entry["github"])


def finals_envelope(commit: str, scored: list[dict], cohort: str) -> dict:
    body = {
        "schema": SCHEMA,
        "event": "final.scored",
        "repository": REPO,
        "commit": commit,
        "cohort": cohort,
        # The API is the record for a final, not this repository, so the pointer
        # goes there. Reading it needs the partner's read key; the summary here
        # does not.
        "finals_url": f"{FINALS_API}?cohort={cohort}",
        "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "counts": {"scored": len(scored)},
        "truncated": len(scored) > MAX_CHANGED,
        "scored": scored[:MAX_CHANGED],
    }
    return body


def envelope(event: str, commit: str, moved: list[dict] | None) -> dict:
    body = {
        "schema": SCHEMA,
        "event": event,
        "repository": REPO,
        "commit": commit,
        "track_url": f"https://raw.githubusercontent.com/{REPO}/{commit}/{TRACK}",
        "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if moved is None:
        return body
    body["counts"] = {
        state: sum(1 for change in moved if change["change"] == state)
        for state in ("added", "updated", "removed")
    }
    body["truncated"] = len(moved) > MAX_CHANGED
    body["changed"] = moved[:MAX_CHANGED]
    return body


def hand_to_gateway(api: str, key: str, body: dict) -> None:
    """Ask the course API to tell the subscriber, and let it decide how.

    WHY THIS REPOSITORY NO LONGER TALKS TO THE SUBSCRIBER. It is public. Its
    Actions secrets are reachable by every workflow in it and by everyone with
    write access — and the subscriber's check today accepts a FIXED string
    rather than a per-request signature, so anything that learned that string
    could forge a delivery of any content: invented scores, invented finals,
    invented withdrawals. A credential like that does not belong here.

    So we say what happened, with the server key this repository ALREADY holds
    for `/submit`, and the API holds the address and the credential. It is the
    same shape `finals.yml` has used since the 10th, one route further.

    RETRIES STAY HERE, because the thing worth retrying is reaching our own API.
    Whether the subscriber was reachable is the API's problem and it reports it.
    """
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    delivery = hashlib.sha256(raw).hexdigest()[:32]
    request = urllib.request.Request(
        f"{api.rstrip('/')}/api/dev3pack/notify",
        data=raw,
        method="POST",
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {key}",
            "user-agent": f"dev3pack-notify ({REPO})",
        },
    )

    last = ""
    for attempt, pause in enumerate(BACKOFF, start=1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                answer = json.loads(response.read().decode("utf-8") or "{}")
                if answer.get("delivered"):
                    print(f"delivered {delivery} — subscriber returned {answer.get('status')}")
                else:
                    # Reaching us worked; reaching the subscriber did not. Say
                    # so and stop: repeating it cannot change their answer.
                    print(
                        f"the API accepted {delivery} but did not deliver it: "
                        f"{answer.get('reason') or answer.get('status')}"
                    )
                return
        except urllib.error.HTTPError as error:
            last = f"HTTP {error.code}"
            if error.code == 502:
                # Our API is up and the subscriber refused. Its body names why.
                detail = error.read().decode("utf-8", "replace")[:200]
                raise NotifyError(f"the subscriber refused {delivery}: {detail}") from None
            if error.code != 429 and 400 <= error.code < 500:
                raise NotifyError(f"the course API refused {delivery}: {last}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = str(error)
        if attempt < len(BACKOFF):
            print(f"attempt {attempt} failed ({last}); retrying in {pause}s", file=sys.stderr)
            time.sleep(pause)
    raise NotifyError(f"could not reach the course API for {delivery}: {last}")


def deliver(url: str, secret: str, body: dict) -> None:
    """POST it, signed, retrying only what retrying can fix.

    The signature covers the timestamp AND the exact bytes sent, so a receiver
    that verifies against a re-serialised body will disagree with us. It has to
    verify against the raw request body, which is why this signs `raw` and sends
    that same object.
    """
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.".encode("utf-8") + raw, hashlib.sha256
    ).hexdigest()
    delivery = hashlib.sha256(raw).hexdigest()[:32]

    request = urllib.request.Request(
        url,
        data=raw,
        method="POST",
        headers={
            "content-type": "application/json",
            "user-agent": f"dev3pack-notify ({REPO})",
            "x-dev3pack-event": body["event"],
            "x-dev3pack-delivery": delivery,
            "x-dev3pack-timestamp": timestamp,
            "x-dev3pack-signature": f"v1={signature}",
        },
    )

    last = ""
    for attempt, pause in enumerate(BACKOFF, start=1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                print(f"delivered {delivery} — HTTP {response.status}")
                return
        except urllib.error.HTTPError as error:
            last = f"HTTP {error.code}"
            # A 4xx that is not 429 is the receiver refusing this body. Sending
            # the identical bytes again cannot change that answer.
            if error.code != 429 and 400 <= error.code < 500:
                raise NotifyError(f"the subscriber refused delivery {delivery}: {last}")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = str(error)
        if attempt < len(BACKOFF):
            print(f"attempt {attempt} failed ({last}); retrying in {pause}s", file=sys.stderr)
            time.sleep(pause)
    raise NotifyError(f"delivery {delivery} failed after {len(BACKOFF)} attempts: {last}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True, help="the commit being announced")
    parser.add_argument("--previous", default="none", help="the commit it replaced")
    parser.add_argument("--finals", help="a file listing the result.json paths just written")
    parser.add_argument("--cohort", default="2026-09", help="the cohort a final belongs to")
    parser.add_argument("--ping", action="store_true", help="send a ping, diff nothing")
    parser.add_argument("--dry-run", action="store_true", help="print the body, send nothing")
    parser.add_argument(
        "--fingerprint",
        action="store_true",
        help="print a hash of the configured secret, so two sides can be compared",
    )
    args = parser.parse_args(argv)

    url = os.environ.get("DEV3PACK_WEBHOOK_URL", "").strip()
    secret = os.environ.get("DEV3PACK_WEBHOOK_SECRET", "").strip()
    # THE GATEWAY IS PREFERRED, and once the two values above are deleted from
    # this repository it is the only path left. See `hand_to_gateway`.
    api = os.environ.get("DEV3PACK_API_BASE", "").strip()
    server_key = os.environ.get("DEV3PACK_SERVER_KEY", "").strip()
    via_gateway = bool(api and server_key) and not (url and secret)
    if args.fingerprint:
        # WHY A HASH AND NOT THE VALUE. Two deployments disagreeing about a
        # shared secret is the commonest cause of a 401, and the obvious way to
        # check — read both and compare — means the secret travels through a
        # chat window, a terminal history and a CI log. SHA-256 of a 64-character
        # random value is not reversible, so the fingerprint proves sameness and
        # discloses nothing. Truncated because a full digest invites someone to
        # try a dictionary against it.
        if not secret:
            print("DEV3PACK_WEBHOOK_SECRET is not set here")
            return 1
        digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]
        print(f"length {len(secret)} · sha256[:16] {digest}")
        if secret != secret.strip():
            print("WARNING: the value has surrounding whitespace; the other side may not strip it")
        return 0

    if url and not url.startswith("https://"):
        # The body is signed, not encrypted, and the signature proves only that
        # we sent it. Over `http://` anyone on the path reads which students
        # scored what and can replay the delivery; `file://` would make
        # `urlopen` read a local path and deliver nothing at all. Neither is a
        # subscriber, so neither is worth guessing at. Refuse loudly, because a
        # typo here is silent in every other direction.
        raise NotifyError(
            f"DEV3PACK_WEBHOOK_URL must be https://, not {url.split(':', 1)[0]}://"
        )
    if not args.dry_run and not via_gateway and not (url and secret):
        # NOT AN ERROR. The course has to keep running before a subscriber
        # exists, and a red build every half hour would teach everyone to
        # ignore this workflow.
        print("no subscriber configured; nothing to deliver")
        return 0

    if args.ping:
        body = envelope("ping", args.commit, None)
    elif args.finals:
        paths = [line.strip() for line in Path(args.finals).read_text().splitlines()]
        scored = finals_from([p for p in paths if p])
        if not scored:
            print("no final was scored in this push; nothing to deliver")
            return 0
        body = finals_envelope(args.commit, scored, args.cohort)
        passed = sum(1 for entry in scored if entry.get("passed"))
        print(f"{len(scored)} final(s) scored, {passed} passed")
    else:
        moved = changes(track_at(args.previous), track_at(args.commit))
        if not moved:
            # The track can change without a score changing: a new course item
            # rewrites the document and moves nobody's row.
            print("the track moved but no score did; nothing to deliver")
            return 0
        body = envelope("track.updated", args.commit, moved)
        counts = body["counts"]
        print(
            f"{counts['added']} added, {counts['updated']} updated, "
            f"{counts['removed']} removed"
        )

    if args.dry_run:
        print(json.dumps(body, indent=2, sort_keys=True))
        return 0
    if via_gateway:
        hand_to_gateway(api, server_key, body)
    else:
        deliver(url, secret, body)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except NotifyError as error:
        print(f"\n{error}", file=sys.stderr)
        raise SystemExit(1) from error
