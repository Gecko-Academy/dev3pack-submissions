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
request, CI re-runs your notebook and compares what its checks actually print
against what your `submission.json` says. A claim that does not survive that is
refused by a bot, so nobody has to take anyone's word for anything.

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

The same re-run the instructors do, on your own machine, from your course
repository:

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
