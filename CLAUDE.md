# Claude Code instructions

@AGENTS.md

The line above is an IMPORT, not a link: Claude Code reads `CLAUDE.md` and
follows `@path` references, so the policy is actually loaded rather than merely
pointed at. A Markdown link is a link — Claude does not open it. `AGENTS.md`
stays canonical; only Claude-specific differences belong below.

Claude-specific notes:

- Prefer targeted `uv run pytest tests/test_<module>.py` runs over full sweeps
  while iterating; run the full suite before claiming done.
- When asked to add a feature, present the plan and wait for approval before
  editing (this repo teaches the inspect → plan → implement → test → review loop —
  model it).
- For notebook edits, verify with
  `uv run python scripts/check_notebooks.py <path>` afterwards.
