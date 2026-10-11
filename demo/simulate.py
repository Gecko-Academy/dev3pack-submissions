"""Drive a receiver through a week of a cohort, without waiting for a cohort.

    export DEV3PACK_WEBHOOK_SECRET=...            # the shared secret
    python3 demo/simulate.py --url http://localhost:3000/ai-bootcamp/webhook
    python3 demo/simulate.py --url https://... --scenario week
    python3 demo/simulate.py --url https://... --dry-run     # print, send nothing

WHAT THIS IS FOR. A subscriber building an ingestion path needs to see the
shape of a real week before there is one: a learner hands in session 1, then
session 2 the next day, someone resubmits and replaces their earlier score,
somebody's work is withdrawn, a final is graded. Waiting for September to find
out that the second delivery of the same `(github, item)` was appended rather
than upserted is the expensive way to learn it.

EVERY DELIVERY IS REAL. Same envelope, same headers, same signature scheme as
production — signed here with whatever secret is in the environment, so a
receiver that verifies correctly accepts these and a receiver that does not,
does not. There is no "simulation mode" on the wire, because a test that takes
a shortcut the real thing does not take proves nothing about the real thing.

WHAT IT DELIBERATELY DOES: repeats a delivery, to check it is idempotent by
`x-dev3pack-delivery`; sends the same `(github, item)` twice with a different
score, to check the row is replaced and not appended; sends a removal; sends
events out of order. Each of those is a bug we would rather you found here.

THE TRACK IT POINTS AT IS THE DEMO TRACK, pinned to a commit, so `track_url`
resolves and the document is stable. It carries invented learners and scores.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "Gecko-Academy/dev3pack-submissions"
SCHEMA = "dev3pack.webhook.v1"

#: A commit whose `demo/track.json` exists and will not move. A BRANCH NAME IS
#: NOT ONE: `track_url` would resolve but keep changing under the receiver, and
#: a subscriber that validates `commit` as a SHA refuses the delivery outright
#: -- measured against the live endpoint 2026-09-13, `main` 400, this 200.
PINNED = "0ece99041178d058b2d1fa9d718af4bb424b2927"


def envelope(event: str, commit: str, **extra: object) -> dict:
    """The body, exactly as the course builds it."""
    body: dict = {
        "schema": SCHEMA,
        "event": event,
        "repository": REPO,
        "commit": commit,
        "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "track_url": f"https://raw.githubusercontent.com/{REPO}/{commit}/demo/track.json",
    }
    body.update(extra)
    return body


def send(url: str, secret: str, body: dict, *, dry: bool, note: str) -> int:
    """Sign and POST one delivery, and say plainly what came back."""
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.".encode("utf-8") + raw, hashlib.sha256
    ).hexdigest()
    delivery = hashlib.sha256(raw).hexdigest()[:32]

    if dry:
        print(f"  {note}\n    {raw.decode()}")
        return 0

    request = urllib.request.Request(
        url,
        data=raw,
        method="POST",
        headers={
            "content-type": "application/json",
            "user-agent": "dev3pack-simulate",
            "x-dev3pack-event": str(body["event"]),
            "x-dev3pack-delivery": delivery,
            "x-dev3pack-timestamp": timestamp,
            # BOTH, exactly as production sends them. A receiver that verifies
            # `-v2` refuses a delivery carrying only the older header, so a
            # simulator that sends one of the two reports a signing failure
            # against a receiver that is working correctly -- measured against
            # the live endpoint 2026-09-13: v2 alone 202, legacy alone 401.
            "x-dev3pack-signature": f"v1={signature}",
            "x-dev3pack-signature-v2": f"v1={signature}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            # The body, not only the status: the repeated-delivery case is only
            # proved by what comes back (`"duplicate": true`), and a 200 alone
            # cannot tell an idempotent receiver from one that applied it twice.
            detail = response.read().decode("utf-8", "replace").strip()[:60]
            print(f"  {note:<46} HTTP {response.status}  {detail}")
            return 0 if 200 <= response.status < 300 else 1
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace").strip()[:60]
        print(f"  {note:<46} HTTP {error.code}  {detail}")
        return 1
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        print(f"  {note:<46} unreachable: {error}")
        return 1


def week(url: str, secret: str, dry: bool) -> int:
    """Five days of a cohort, then the awkward parts.

    The order is the order a real week produces them, and the last four are the
    cases that break an ingestion written against the happy path alone.
    """
    bad = 0
    days = [
        ("ana-dev", "ch01", None, "Mon — ana hands in session 1 (unmarked)"),
        ("ana-dev", "ch02", 400, "Tue — ana, session 2, full marks"),
        ("bruno-c", "ch02", 200, "Tue — bruno, session 2, partial"),
        ("ana-dev", "ch03", 300, "Wed — ana, session 3"),
        ("bruno-c", "ch03", 100, "Wed — bruno, session 3, one check"),
    ]
    for github, item, score, note in days:
        bad += send(
            url,
            secret,
            envelope(
                "track.updated",
                PINNED,
                summary={"added": 1, "updated": 0, "removed": 0},
                changed=[{"github": github, "item": item, "score": score}],
            ),
            dry=dry,
            note=note,
        )

    print("\n  the cases that break a happy-path ingestion:\n")

    # 1. A resubmission REPLACES a row. Appending gives bruno two ch03 rows and
    #    a total nobody can explain.
    bad += send(
        url,
        secret,
        envelope(
            "track.updated",
            PINNED,
            summary={"added": 0, "updated": 1, "removed": 0},
            changed=[{"github": "bruno-c", "item": "ch03", "score": 300}],
        ),
        dry=dry,
        note="bruno resubmits ch03 — 100 becomes 300",
    )

    # 2. The same delivery twice. Delivery is at-least-once by design: a retry
    #    after a timeout that actually succeeded looks exactly like this.
    repeated = envelope(
        "track.updated",
        PINNED,
        summary={"added": 1, "updated": 0, "removed": 0},
        changed=[{"github": "carla-m", "item": "ch01", "score": None}],
    )
    bad += send(url, secret, repeated, dry=dry, note="carla hands in ch01")
    bad += send(url, secret, repeated, dry=dry, note="…the identical delivery, again")

    # 3. A withdrawal. Rows leave: a submission is unmerged, or test data is
    #    cleared. An ingestion that only ever adds will keep the ghost forever.
    bad += send(
        url,
        secret,
        envelope(
            "track.updated",
            PINNED,
            summary={"added": 0, "updated": 0, "removed": 1},
            changed=[{"github": "carla-m", "item": "ch01", "score": None, "removed": True}],
        ),
        dry=dry,
        note="carla's ch01 is withdrawn",
    )

    # 4. A final. Different surface, different owner: the score is graded
    #    against a private set and lives in the course API, not in the track.
    bad += send(
        url,
        secret,
        envelope(
            "final.scored",
            PINNED,
            cohort="2099-01",
            scored=[{"github": "ana-dev", "score": 0.82, "passed": True}],
        ),
        dry=dry,
        note="ana's final is graded",
    )
    return bad


def ping(url: str, secret: str, dry: bool) -> int:
    return send(url, secret, envelope("ping", PINNED), dry=dry, note="ping")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="the receiver to drive")
    parser.add_argument(
        "--scenario", choices=("ping", "week"), default="week", help="what to send"
    )
    parser.add_argument("--dry-run", action="store_true", help="print bodies, send nothing")
    args = parser.parse_args(argv)

    secret = os.environ.get("DEV3PACK_WEBHOOK_SECRET", "").strip()
    if not secret:
        print(
            "DEV3PACK_WEBHOOK_SECRET is not set.\n"
            "For a first run against your own code, the published demo secret works:\n"
            "  export DEV3PACK_WEBHOOK_SECRET=dev3pack-demo-secret",
            file=sys.stderr,
        )
        return 1
    if not args.dry_run and not args.url.startswith(("https://", "http://localhost", "http://127.")):
        print("--url must be https, or a local address", file=sys.stderr)
        return 1

    print(f"\n  {args.scenario} -> {args.url}\n")
    failed = ping(args.url, secret, args.dry_run) if args.scenario == "ping" else week(
        args.url, secret, args.dry_run
    )
    if args.dry_run:
        print("\n  dry run: nothing was sent.")
        return 0
    print(f"\n  {'every delivery was accepted' if not failed else f'{failed} delivery(ies) refused'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
