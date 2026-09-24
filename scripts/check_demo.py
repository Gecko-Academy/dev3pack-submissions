"""Check that the test data still describes the real thing.

    python3 scripts/check_demo.py

WHY THIS EXISTS. `demo/` is what a consumer builds against before the cohort
starts, and it is the one thing here that no learner ever touches, so nothing
would have noticed it going stale. The failure it guards against is quiet: the
course adds an item, `items.json` moves, `demo/track.json` does not, and a
partner's gradebook is built against a denominator we no longer use.

It also re-signs every published sample and refuses a mismatch, because a sample
whose signature does not verify teaches a consumer that their correct code is
wrong.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"

TRACK_SCHEMA = "dev3pack.track.v1"
WEBHOOK_SCHEMA = "dev3pack.webhook.v1"
EVENTS = {"track.updated", "final.scored", "ping"}
TIERS = {"verified", "claimed", "unverifiable", "handed in"}


def problems() -> list[str]:
    found: list[str] = []

    items = json.loads((ROOT / "items.json").read_text())["items"]
    expected = [
        {"id": i["id"], "title": i["title"], "scored": i["scored"],
         "verifiable": i["verifiable"], "max_score": i["max_score"]}
        for i in items
    ]
    by_id = {i["id"]: i for i in items}

    track = json.loads((DEMO / "track.json").read_text())
    if track.get("schema") != TRACK_SCHEMA:
        found.append(f"demo/track.json: schema {track.get('schema')!r}")
    if track.get("items") != expected:
        # The whole point of this check. `items[]` is a consumer's denominator.
        found.append(
            "demo/track.json: items[] no longer matches items.json. Re-generate it, "
            "or a consumer builds a gradebook against a denominator we do not use"
        )
    for entry in track.get("entries", []):
        where = f"demo/track.json: {entry.get('github')}/{entry.get('item')}"
        item = by_id.get(entry.get("item"))
        if item is None:
            found.append(f"{where}: no course item called {entry.get('item')!r}")
            continue
        if entry.get("tier") not in TIERS:
            found.append(f"{where}: tier {entry.get('tier')!r}")
        if entry.get("scored") != item["scored"]:
            found.append(f"{where}: scored {entry.get('scored')}, item says {item['scored']}")
        if not item["scored"] and entry.get("score") is not None:
            found.append(f"{where}: not marked, so score must be null")
        if entry.get("max_score") != item["max_score"]:
            found.append(f"{where}: max_score {entry.get('max_score')}, item says {item['max_score']}")

    # The traps this fixture exists to carry. If somebody tidies them away it
    # stops doing its job, and nothing else would say so.
    rows = {(e["github"], e["item"]) for e in track.get("entries", [])}
    tiers = {e["tier"] for e in track.get("entries", [])}
    if "verified" not in tiers:
        found.append("demo/track.json: no `verified` row, so a consumer never sees that state")
    if not any(e["scored"] and e["score"] == 0 for e in track["entries"]):
        found.append("demo/track.json: no real zero, so it cannot be told from a missing row")
    if ("dev3pack-demo-partial", "ch03") in rows:
        found.append("demo/track.json: ch03 must stay ABSENT for dev3pack-demo-partial")
    if not track.get("problems"):
        found.append("demo/track.json: problems[] is empty, so the field is never exercised")

    samples = json.loads((DEMO / "samples.json").read_text())
    secret = samples["demo_secret"].encode()
    for sample in samples["samples"]:
        path = ROOT / sample["file"]
        if not path.is_file():
            found.append(f"{sample['file']}: missing")
            continue
        # The trailing newline makes the file a well-formed text file and is NOT
        # part of what was signed.
        raw = path.read_bytes().rstrip(b"\n")
        want = "v1=" + hmac.new(
            secret, f"{sample['x-dev3pack-timestamp']}.".encode() + raw, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(want, sample["x-dev3pack-signature"]):
            found.append(f"{sample['file']}: the published signature does not verify")
        body = json.loads(raw)
        if body.get("schema") != WEBHOOK_SCHEMA:
            found.append(f"{sample['file']}: schema {body.get('schema')!r}")
        if body.get("event") not in EVENTS:
            found.append(f"{sample['file']}: event {body.get('event')!r}")
        if body.get("event") != sample["x-dev3pack-event"]:
            found.append(f"{sample['file']}: header says {sample['x-dev3pack-event']!r}, body says {body.get('event')!r}")

    for event in ("track.updated", "final.scored", "ping"):
        if not any(s["x-dev3pack-event"] == event for s in samples["samples"]):
            found.append(f"demo/samples.json: no {event} sample")

    return found


def main() -> int:
    found = problems()
    for problem in found:
        print(f"::error::{problem}")
    if not found:
        print("ok: the test data still describes the real thing")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
