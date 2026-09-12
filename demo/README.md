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
| `webhook-final-scored.json` | A `final.scored` body, two learners |
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

**Verify the bytes you received, never a re-serialised body.** The signature
covers the exact body that was sent: compact JSON, keys sorted, no trailing
newline. Parsing it and re-encoding changes spacing or key order and the
signature will not match, with a correct secret and a correct algorithm. In a
JavaScript handler that means `await req.text()` — never `await req.json()`
followed by `JSON.stringify`. The sample files here are stored as those exact
bytes for the same reason.

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

### The same thing in JavaScript

Web Crypto only, so this is the same in Convex, Cloudflare Workers, Deno and any
edge runtime. `request.text()` gives you the raw body; never re-serialise the
parsed object to verify.

```js
const enc = new TextEncoder();

async function verify(secret, timestamp, signature, raw) {
  if (!timestamp || !signature) return false;
  if (Math.abs(Date.now() / 1000 - Number(timestamp)) > 300) return false;
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, enc.encode(`${timestamp}.${raw}`));
  const want = "v1=" + [...new Uint8Array(mac)]
    .map((b) => b.toString(16).padStart(2, "0")).join("");
  if (want.length !== signature.length) return false;
  // Compare every byte. Returning early on the first mismatch leaks the
  // signature one character at a time to anyone who can time the response.
  let diff = 0;
  for (let i = 0; i < want.length; i++) {
    diff |= want.charCodeAt(i) ^ signature.charCodeAt(i);
  }
  return diff === 0;
}
```

Against the samples here this returns `false` on the freshness check, because
their timestamp is fixed. Drop that line while you develop.

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

## The two events

`track.updated` carries per-session scores. Their record is this repository, and
`track_url` points at it, pinned to a commit.

`final.scored` carries final-assessment scores. Their record is **not** here: a
final is graded against a question set that cannot ship to anybody, so the
course API holds it and `finals_url` points there. Reading that URL needs your
read key; the summary in the body does not.

```json
{
  "schema": "dev3pack.webhook.v1",
  "event": "final.scored",
  "cohort": "2099-01",
  "finals_url": "https://app.geckovision.tech/api/dev3pack/finals?cohort=2099-01",
  "counts": { "scored": 2 },
  "scored": [
    { "github": "dev3pack-demo-pass", "score": { "passed": 15, "total": 15, "percent": 100 },
      "gates": { "overall_threshold": true, "critical_safety": true },
      "passed": true, "certificate_eligible": true }
  ]
}
```

**Read `certificate_eligible`, not `score.percent`.** The sample carries
`dev3pack-demo-unsafe` beside the passing one for exactly this reason: it scores
73 per cent and earns nothing, because the critical-safety gate is separate and
can veto a high score. Rank a cohort by percentage and you put them in the wrong
order.

The `final.scored` sample is invented, unlike the `track.updated` ones, because
no final has been scored yet. It uses the demo cohort `2099-01`, which holds
nobody real and is safe to call as often as you like.

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

## If a delivery is missed

It will happen. Your endpoint redeploys, or something times out, and we stop
after three attempts rather than queue.

**You do not lose anything, as long as you read `track_url` rather than treating
`changed` as a delta.** The next successful notification names a newer commit,
and that commit's pinned document holds the complete current track, including
whatever the missed one carried. Replace your rows from it and you are correct
again, with no reconciliation and nothing for us to replay.

This is the whole reason the body is a hint and the URL is the truth. A
consumer that applies `changed` incrementally, and only that consumer, can
drift.

Two smaller consequences of the same rule. A duplicate delivery is harmless,
because reading the same pinned URL twice gives the same answer. And two
notifications arriving out of order are harmless too, provided you ignore a
`commit` older than the last one you applied.

If you do want a specific delivery sent again, ask. It is a button on our side.

## When you are ready for real deliveries

Send us an HTTPS endpoint and we will send you the real secret. Ask for a `ping`
first: it is the same envelope with no `changed` list, it can be sent at any
time, and it proves signing and routing on a cohort that is still empty.
