# Test data

Everything here is fake except where it says otherwise, and none of it is
reachable from the real track. `render_track.py` writes `track.json` at the root
and never touches this directory, so nothing you find here can leak into a
cohort's gradebook, and nothing a cohort does can change these files.

It exists so you can build a receiver and a gradebook view before the cohort
starts, without asking us for anything.

| File | What it is |
|---|---|
| `track.json` | A demo track, same schema as the real one, five invented learners |
| `webhook-track-updated.json` | A `track.updated` body, five rows added |
| `webhook-track-removed.json` | A `track.updated` body, five rows removed |
| `webhook-ping.json` | A `ping` body |
| `samples.json` | The headers for each sample, and the demo secret |

## The webhook samples

**The bodies are real.** They are the exact bytes the course delivered on
2026-09-11 while the webhook was being tested. `track_url` in each one still
resolves, so you can follow the pointer and see the document it describes.

**The signatures and timestamps are not.** The real ones were made with a live
secret. These are recomputed with a secret published in `samples.json`:

```
dev3pack-demo-secret
```

That secret is public and worth nothing. It exists so you can run your
verification code and get a `true` back without holding anything private. The
real secret is sent separately and never appears in this repository.

### Verify one

The files are the raw bytes as sent, with a trailing newline added so the file
is a well-formed text file. **That newline is not signed.** Strip it, or read
the body from a real request instead.

```python
import hashlib, hmac, json, pathlib

samples = json.loads(pathlib.Path("demo/samples.json").read_text())
secret = samples["demo_secret"].encode()

for sample in samples["samples"]:
    raw = pathlib.Path(sample["file"]).read_bytes().rstrip(b"\n")
    timestamp = sample["x-dev3pack-timestamp"]
    want = "v1=" + hmac.new(
        secret, f"{timestamp}.".encode() + raw, hashlib.sha256
    ).hexdigest()
    assert hmac.compare_digest(want, sample["x-dev3pack-signature"])
```

Change one byte of `raw` and it must stop matching. If it does not, your
comparison is wrong, and the usual cause is verifying against a re-serialised
copy of the parsed JSON rather than the bytes that arrived.

### Send one to your own endpoint

```bash
SAMPLE=demo/webhook-track-updated.json
curl -X POST http://localhost:3000/your/endpoint \
  -H "content-type: application/json" \
  -H "x-dev3pack-event: track.updated" \
  -H "x-dev3pack-delivery: $(jq -r '.samples[0]["x-dev3pack-delivery"]' demo/samples.json)" \
  -H "x-dev3pack-timestamp: $(jq -r '.samples[0]["x-dev3pack-timestamp"]' demo/samples.json)" \
  -H "x-dev3pack-signature: $(jq -r '.samples[0]["x-dev3pack-signature"]' demo/samples.json)" \
  --data-binary @"$SAMPLE"
```

**Expect your freshness check to reject it.** The timestamp is fixed so the
sample is reproducible, which means it is permanently stale. That is the check
working. Point it at the demo secret and disable the age test while you develop,
then turn the age test back on before you go live.

## The demo track

`track.json` here is shaped exactly like the real one and is chosen to break the
assumptions a gradebook usually makes, rather than to look tidy. Build your view
against it and the real thing will not surprise you.

| Learner | What it is there to catch |
|---|---|
| `dev3pack-demo-full` | The ordinary case. Full marks, nothing odd. |
| `dev3pack-demo-partial` | **`ch03` is absent.** Not submitted, which is not a zero. It also holds a real zero in `ch05`, so you can tell the two apart. |
| `dev3pack-demo-hinted` | `270` out of `300`. A hint was taken, so a score is not exercises times one hundred. |
| `dev3pack-demo-verified` | One row is `verified` beside `claimed` ones. Nothing is verified today; this is here so the state already exists in your UI when the verifier lands. |
| `dev3pack-demo-units` | Week 0 only, every item unscored. This learner's total is **blank**, not nought per cent. |

`problems` is deliberately non-empty. It carries strings when a bundle could not
be read, and you should decide to render or ignore it rather than discover it in
production.

### What your view has to get right

- A missing `(github, item)` row means **not submitted**. An empty cell and a
  zero are different facts about a person, and `dev3pack-demo-partial` has both.
- `scored: false` means **no arithmetic**. Render the word, keep the row out of
  totals. `dev3pack-demo-units` has nothing else, so a percentage for them is
  wrong however you compute it.
- Show `tier` beside every score. `claimed` is self-reported and shape-checked.
  Nothing re-runs a notebook yet.
- `items[]` is your denominator, not the rows you happened to receive.

## When you are ready for real deliveries

Send us an HTTPS endpoint and we will send you the real secret. Ask for a `ping`
first: it is the same envelope with no `changed` list, it can be sent at any
time, and it proves signing and routing on a cohort that is still empty.
