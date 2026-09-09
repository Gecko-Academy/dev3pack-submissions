# Dev3Pack submissions

Where you hand in your work. One folder per chapter, opened as a pull request
from your own fork, and checked automatically.

## What you are handing in

Two files per chapter, and `uv run bootcamp submit` writes both:

```text
submissions/
└── your-github-username/
    └── ch03/
        ├── submission.json   your score, and what passed
        └── notebook.ipynb    your work, which is the evidence for it
```

Your score is a claim and your notebook is the proof. When you open the pull
request, CI checks that the bundle is the right shape, that it sits in your own
folder, that the notebook is the exact file the claim was written against, and
that the score follows from the passes and help the claim itself lists. It does
**not** re-run your notebook yet: that arrives in week 2, and until then every
score in this repository is marked `claimed`, which means self-reported and
shape-checked. A human merges, and the merge is what records the score.

## How to submit

Once, at the start:

1. **Fork this repository.** You never need write access here, only to your own
   fork.
2. Clone your fork.

Then for each chapter, from your course repository:

```bash
uv run bootcamp submit ch03 --github your-github-username
```

It runs the chapter's notebook, prints what passed, and writes
`submissions/your-github-username/ch03/`. Copy that folder into your fork,
commit, push, and open a pull request.

You may submit unfinished work. The file records what passed and what did not,
and re-submitting replaces the earlier attempt.

## What CI checks

- **Your pull request only touches your own folder.** GitHub cannot grant write
  access to one path, so this rule lives in CI instead. It also means nobody can
  overwrite your work.
- **The notebook is the one the score was claimed for**, matched by hash, so a
  score and a notebook cannot be submitted from different attempts. Hand-editing
  `submission.json` fails here.
- **The bundle is the right shape** and the claim names the student whose folder
  it sits in.

## Check it yourself before you open the pull request

The re-run CI cannot do yet, on your own machine, from your course repository:

```bash
uv run python scripts/verify_submission.py submissions/your-username/ch03
```

It copies your notebook into the course, runs it, and compares what its checks
actually print against what your file claims. `VERIFIED` means your submission
holds up.

## What CI does not check

Whether you took a hint. That lives in `~/.bootcamp/progress.db` on your own
machine and never leaves it. `bootcamp submit` reads it and records what it
finds, and help only ever costs marks, so there is nothing to gain by
overstating it.

## If CI refuses your submission

Read what it says, fix the exercise, and run `bootcamp submit` again. The
commonest cause is hand-editing `submission.json`, which is generated and should
be committed exactly as written.

## The final assignment

The final is handed in like anything else, with one extra file:

```
submissions/<your-github>/final/
├── answers.json     what your agent answered, and nothing else
└── notebook.ipynb   the run that produced it
```

`answers.json` holds your agent's answers to the published final questions. Your
agent runs on your machine; only the answers travel. Nothing you wrote is
executed by the course.

When a pull request carrying one is merged, the course scores the answers
against the private key set and writes the result to `finals/<your-github>/`.
That file records the score, both gates and a verdict per question. It does not
record what you answered.

Two gates decide a pass, and the second is the one that matters: 30% of
questions, and **every** question marked critical. Refusing everything reaches
the first and fails the second. Above both, the course issues a signed receipt
and you render your certificate from it.

## Consuming the track

`track.json` at the root of this repository is the machine-readable record, and
the raw URL is the API:

```
https://raw.githubusercontent.com/Gecko-Academy/dev3pack-submissions/main/track.json
```

No key, because everything in it is already public here. Poll it with
`If-None-Match`; it changes at most once per session day, when the instructors
merge and push. Pin a commit in the path instead of `main` if you need the
exact document a reading was taken from.

The rules a consumer must follow, because the document cannot enforce them:

- **`schema` is `dev3pack.track.v1`.** Refuse any other value. Additive fields
  never bump it; a break ships as a new file.
- **Render `tier` beside every score.** `claimed` is self-reported and
  shape-checked, `verified` means a re-run agreed, `unverifiable` means it can
  never be re-run, `handed in` means the item is not marked. A score without
  its tier is a misreading.
- **A missing `(github, item)` row means not submitted**, never zero. `score: 0`
  is a submission where nothing passed.
- **`scored: false` means no arithmetic.** `score` and `max_score` are `null`;
  render the word, and leave the row out of totals.
- **One row per `(github, item)`.** A resubmission replaces the row and drops
  its `verified` tier, on purpose. Upsert on that key; never append.
- **`items[]` is the denominator.** It lists every chapter that can be handed
  in, with `max_score` and whether it is scored, so a blank cell has a shape.
- **`submitted_at` is the student's clock.** For "changed since", use the
  commit, not that field.
- **Non-empty `problems[]` means the document is partial**, and each entry
  names the file that could not be read.

Grades, certificates and the final assignment are not in this file and never
will be. They are decided at demo day and delivered separately.
