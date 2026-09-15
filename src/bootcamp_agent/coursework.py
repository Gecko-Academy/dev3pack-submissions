"""Run a chapter's notebook and report what its own checks said.

The notebook is the arbiter, here as in the notebook itself: this module does
not re-implement any check. It executes the notebook and reads back the lines
`check()` and `review()` printed, so the terminal shows exactly what the
participant would see in Jupyter.

Strict mode stays OFF for a participant's notebook. An unfilled `TODO(you)`
prints ❌ and the notebook keeps going, which is the whole point: the scorecard
is meant to be read while the work is unfinished.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from bootcamp_agent.checks import STRICT_ENV
from bootcamp_agent.curriculum import Chapter, exercise_ids

#: What `check()` prints. Anchored, because a notebook may legitimately print
#: a tick inside prose and that is not a verdict.
# `ch03-e1` for a session, `w05-e1` for a week-0 unit. Matching only `ch` made
# every week-0 verdict invisible, so a submitted unit reported all its exercises
# as never run when they had in fact reported.
_EXERCISE = r"(?:ch|w|cap)\d\d-e\d+"
_PASS = re.compile(rf"^✅ ({_EXERCISE}) passed\s*$")
_FAIL = re.compile(rf"^❌ ({_EXERCISE}): (.+)$")


class CourseworkError(Exception):
    """The notebook could not be run at all. Distinct from a failed check."""


@dataclass
class Scorecard:
    """One chapter's verdicts, in the notebook's own words."""

    chapter_id: str
    passed: tuple[str, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()
    #: Registered for this chapter but never reached: the cell was not run, or
    #: an earlier cell raised. Not a failure, and never reported as one.
    not_reached: tuple[str, ...] = ()
    lines: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.passed) + len(self.failed) + len(self.not_reached)

    @property
    def headline(self) -> str:
        return f"{self.chapter_id}: {len(self.passed)}/{self.total} passed"


def _cells(notebook: object) -> list:
    """A notebook's cells, whether it came from nbformat or from JSON.

    `nbformat.NotebookNode` is a dict subclass with attribute access, so
    `notebook.cells` works for it and returns `[]` for a plain dict -- silently,
    which would report that nothing ran rather than failing. A plain dict is
    what a notebook read with `json.load` is, and what Colab hands back.
    """
    if isinstance(notebook, dict):
        return list(notebook.get("cells", []))
    return list(getattr(notebook, "cells", []))


@dataclass(frozen=True)
class Evidence:
    """What a notebook can prove about itself, before anything is claimed."""

    code_cells: int
    executed_cells: int
    cells_with_output: int
    verdict_lines: int

    @property
    def never_ran(self) -> bool:
        """Nothing here was executed AND nothing printed.

        Both, not either. `execution_count` is the usual signal, but some tools
        strip it while keeping the outputs -- and the outputs are the evidence
        that actually counts, because `stored_scorecard` reads those and never
        looks at the counter. A notebook with outputs has proof of a run even
        when the counter is gone.
        """
        return self.executed_cells == 0 and self.cells_with_output == 0


def evidence_of(notebook: object) -> Evidence:
    """Count what a notebook actually holds, so a hand-in can refuse an empty one.

    A submission is a claim plus the evidence for it. On 2026-09-14 a learner
    handed in a notebook with `execution_count: None` on all seventeen cells and
    no outputs: the claim said `not_reached`, the command printed `wrote ...`,
    and it exited 0. Counting first is what turns that into a refusal.
    """
    code = [cell for cell in _cells(notebook) if cell.get("cell_type") == "code"]
    executed = [cell for cell in code if cell.get("execution_count") is not None]
    with_output = [cell for cell in code if cell.get("outputs")]
    verdicts = [
        line
        for line in outputs_of(notebook)
        if _PASS.match(line.strip()) or _FAIL.match(line.strip())
    ]
    return Evidence(
        code_cells=len(code),
        executed_cells=len(executed),
        cells_with_output=len(with_output),
        verdict_lines=len(verdicts),
    )


def outputs_of(notebook: object) -> list[str]:
    """Every text line a notebook's cells printed, in order."""
    lines: list[str] = []
    for cell in _cells(notebook):
        for output in cell.get("outputs", []) or []:
            text = output.get("text") or ""
            if not text and "data" in output:
                text = output["data"].get("text/plain", "")
            if isinstance(text, list):
                text = "".join(text)
            lines.extend(str(text).splitlines())
    return lines


def read_scorecard(
    item: Chapter | str, lines: list[str], expected: tuple[str, ...] | None = None
) -> Scorecard:
    """Turn printed output into a scorecard. Nothing is judged here, only read."""
    passed: list[str] = []
    # Keyed by exercise id, because one exercise reports twice in a normal run:
    # `check()` prints when its cell executes, and `review()` prints the same
    # failure again in the closing scorecard. Counting both turned chapter 1's
    # honest 0/3 into 0/6, which is a tally no participant could act on. First
    # reason wins, since that is the one `check()` gave at the point of failure.
    identifier = item.chapter_id if isinstance(item, Chapter) else item
    if expected is None:
        expected = exercise_ids(identifier)
    failures: dict[str, str] = {}
    for line in lines:
        if match := _PASS.match(line.strip()):
            passed.append(match.group(1))
        elif match := _FAIL.match(line.strip()):
            failures.setdefault(match.group(1), match.group(2))
    failed = tuple(failures.items())
    seen = {*passed, *failures}
    not_reached = tuple(exercise for exercise in expected if exercise not in seen)
    return Scorecard(
        chapter_id=identifier,
        passed=tuple(dict.fromkeys(passed)),
        failed=failed,
        not_reached=not_reached,
        lines=lines,
    )


def run_chapter(chapter: Chapter, timeout: int = 180) -> Scorecard:
    """Execute a chapter's notebook and read its verdicts."""
    if chapter.notebook is None:
        raise CourseworkError(f"{chapter.chapter_id} has no notebook (it is demo day)")
    return run_notebook(
        chapter.notebook, exercise_ids(chapter.chapter_id), chapter.chapter_id, timeout
    )


def stored_scorecard(notebook_path: Path, identifier: str, expected: tuple[str, ...]) -> Scorecard:
    """What a notebook's SAVED outputs report, without executing anything.

    For the two assistant-driven sessions the course deliberately does not
    re-run the notebook — running them unattended would score zero for work that
    was genuinely done. That is a reason not to re-run it, never a reason to
    claim it never ran: the learner's own `✅ chNN-eN passed` is sitting in the
    file, and reading it is the whole of "as it stands".

    A notebook that was never executed reports nothing passed, which is correct
    and is what an unfinished hand-in should say.
    """
    try:
        import nbformat
    except ImportError as error:
        raise CourseworkError(
            f"{error.name} is missing; install the dev group: uv sync --group dev"
        ) from error
    if not notebook_path.is_file():
        raise CourseworkError(f"not found: {notebook_path}")
    notebook = nbformat.read(notebook_path, as_version=4)
    return read_scorecard(identifier, outputs_of(notebook), expected)


def run_notebook(
    notebook_path: Path,
    expected: tuple[str, ...],
    identifier: str,
    timeout: int = 180,
) -> Scorecard:
    """Execute any notebook with checks non-strict, and read its verdicts.

    Takes a path rather than a `Chapter` so a week-0 unit, which deliberately is
    not a chapter because it has no date, can be run by the same code.

    :raises CourseworkError: when the notebook is missing, when nbclient is
        absent, or when it raised before its checks could report.
    """
    if not notebook_path.is_file():
        raise CourseworkError(f"not found: {notebook_path}")
    try:
        import nbformat
        from nbclient import NotebookClient
    except ImportError as error:  # pragma: no cover - dev group is installed in CI
        raise CourseworkError(
            f"{error.name} is missing; install the dev group: uv sync --group dev"
        ) from error

    notebook = nbformat.read(notebook_path, as_version=4)
    previous = os.environ.get(STRICT_ENV)
    os.environ[STRICT_ENV] = "0"
    try:
        NotebookClient(
            notebook,
            timeout=timeout,
            kernel_name="python3",
            resources={"metadata": {"path": str(notebook_path.parent)}},
            # ipykernel announces "Kernel is running over TCP without encryption
            # … susceptible to eavesdropping" on every start. True, and about a
            # child process on the learner's own machine, so it warns them of
            # nothing they can act on — while appearing above every `check` they
            # run. Raising the kernel's log level removes the cause; hiding the
            # output would also have hidden their own errors.
            extra_arguments=["--log-level=ERROR"],
        ).execute()
    except Exception as error:  # noqa: BLE001 - any failure is one report line
        raise CourseworkError(f"{type(error).__name__}: {str(error)[:200]}") from error
    finally:
        if previous is None:
            os.environ.pop(STRICT_ENV, None)
        else:
            os.environ[STRICT_ENV] = previous
    return read_scorecard(identifier, outputs_of(notebook), expected)


def render(card: Scorecard) -> str:
    """The scorecard as the terminal shows it: the score, then the work."""
    out = [card.headline]
    for exercise, reason in card.failed:
        out.append(f"❌ {exercise}: {reason}")
    for exercise in card.not_reached:
        out.append(f"   {exercise}: not checked yet; run its check cell")
    return "\n".join(out)
