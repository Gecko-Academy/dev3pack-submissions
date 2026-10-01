"""The weekly challenge adds to its session's score, and only from the notebook.

    python3 -m unittest discover -s tests

Founder ruling 2026-09-22: week 1's challenge adds to ch05, week 2's to ch10.
Same item, same row, same webhook shape; only the number grows.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import render_track  # noqa: E402

STUDENT = "octocat"


def notebook(*streams: object) -> dict:
    """A notebook whose code cells print `streams`, one output each, in order."""
    return {
        "cells": [
            {"cell_type": "code", "source": "", "outputs": [{"output_type": "stream", "text": s}]}
            for s in streams
        ]
    }


def claim(item: str, passed: int, **extra: object) -> dict:
    return {
        "schema": render_track.SUBMISSION_SCHEMA,
        "chapter": item,
        "student": {"github": STUDENT},
        "result": {"ran": True, "scored": True, "passed": [f"ex{i}" for i in range(passed)]},
        **extra,
    }


@contextmanager
def tree(item: str, nb: object, passed: int = 2, raw: bytes | None = None, **extra: object):
    """One bundle in a throwaway `submissions/`, and `read_tree` pointed at it."""
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "submissions" / STUDENT / item
        bundle.mkdir(parents=True)
        (bundle / "submission.json").write_text(json.dumps(claim(item, passed, **extra)))
        body = raw if raw is not None else json.dumps(nb).encode()
        (bundle / "notebook.ipynb").write_bytes(body)
        with mock.patch.object(render_track, "SUBMISSIONS", Path(tmp) / "submissions"), \
                mock.patch.object(render_track, "ROOT", Path(tmp)):
            yield bundle


def score(item: str, nb: object = None, **kwargs: object) -> int | None:
    with tree(item, nb, **kwargs):
        entries, problems = render_track.read_tree()
    assert problems == [], problems
    (entry,) = entries
    return entry["score"]


LINE1 = "   week 1 challenge: 400/500\n"


class ChallengeAddsToItsSession(unittest.TestCase):
    def test_ch05_adds_the_printed_points(self) -> None:
        self.assertEqual(score("ch05", notebook("hello\n", LINE1)), 200 + 400)

    def test_ch10_adds_week_2(self) -> None:
        self.assertEqual(score("ch10", notebook("   week 2 challenge: 120/500\n")), 320)

    def test_text_as_a_list_of_lines(self) -> None:
        self.assertEqual(score("ch05", notebook(["a\n", LINE1, "b\n"])), 600)

    def test_the_week_must_match_the_item(self) -> None:
        self.assertEqual(score("ch05", notebook("   week 2 challenge: 400/500\n")), 200)
        self.assertEqual(score("ch10", notebook(LINE1)), 200)

    def test_a_wrong_week_line_does_not_mask_the_right_one(self) -> None:
        nb = notebook(LINE1, "   week 2 challenge: 10/500\n")
        self.assertEqual(score("ch05", nb), 600)

    def test_the_last_line_wins(self) -> None:
        nb = notebook("   week 1 challenge: 100/500\n", "x\n   week 1 challenge: 300/500\n")
        self.assertEqual(score("ch05", nb), 500)
        nb = notebook("   week 1 challenge: 300/500\n", "   week 1 challenge: 0/500\n")
        self.assertEqual(score("ch05", nb), 200)

    def test_three_digits_over_500_are_capped(self) -> None:
        self.assertEqual(score("ch05", notebook("   week 1 challenge: 501/500\n")), 700)
        self.assertEqual(score("ch05", notebook("   week 1 challenge: 999/500\n")), 700)

    def test_four_digits_are_not_a_challenge_line_at_all(self) -> None:
        self.assertEqual(score("ch05", notebook("   week 1 challenge: 9999/500\n")), 200)
        # Not matched, so it cannot override the real line before it either.
        self.assertEqual(score("ch05", notebook(LINE1, "   week 1 challenge: 9999/500\n")), 600)

    def test_near_misses_add_nothing(self) -> None:
        for line in (
            "week 1 challenge: 400/400",
            "week 1 challenge: 400/500 bonus",
            "print('week 1 challenge: 400/500')",
            "week 1 challenge: -400/500",
            "week 1 challenge: ٤٠٠/500",  # Arabic-Indic 400; int() accepts it
        ):
            with self.subTest(line=line):
                self.assertEqual(score("ch05", notebook(line + "\n")), 200)

    def test_absent_adds_nothing(self) -> None:
        self.assertEqual(score("ch05", notebook("all done\n")), 200)

    def test_only_stream_outputs_of_code_cells_count(self) -> None:
        nb = {
            "cells": [
                {"cell_type": "markdown", "source": LINE1, "outputs": [{"output_type": "stream", "text": LINE1}]},
                {"cell_type": "code", "source": LINE1, "outputs": [
                    {"output_type": "execute_result", "data": {"text/plain": LINE1}, "text": LINE1},
                    {"output_type": "error", "text": LINE1},
                ]},
            ]
        }
        self.assertEqual(score("ch05", nb), 200)

    def test_other_items_are_untouched(self) -> None:
        self.assertEqual(score("ch03", notebook(LINE1, "   week 2 challenge: 400/500\n")), 200)

    def test_help_cannot_eat_the_challenge(self) -> None:
        # Two passes, three reveals: the session floors at 0, the challenge still counts.
        with tree("ch05", notebook(LINE1), help={"hinted": 0, "revealed": 3}):
            (entry,), _ = render_track.read_tree()
        self.assertEqual(entry["score"], 400)


class TheClaimCannotAddPoints(unittest.TestCase):
    def test_a_hand_edited_claim_adds_nothing(self) -> None:
        extra = {"challenge": 500, "week 1 challenge": "500/500"}
        with tree("ch05", notebook("no line here\n"), **extra) as bundle:
            stated = json.loads((bundle / "submission.json").read_text())
            stated["result"]["score"] = 700
            stated["result"]["challenge"] = 500
            stated["result"]["output"] = LINE1
            (bundle / "submission.json").write_text(json.dumps(stated))
            (entry,), problems = render_track.read_tree()
        self.assertEqual(problems, [])
        self.assertEqual(entry["score"], 200)


class ABadNotebookIsWorthZeroAndNeverBreaksTheRender(unittest.TestCase):
    def test_unreadable_shapes(self) -> None:
        cases = {
            "not json": b"{ this is not json" + LINE1.encode(),
            "not utf-8": b"\xff\xfe" + LINE1.encode(),
            "a list": json.dumps([LINE1]).encode(),
            "cells not a list": json.dumps({"cells": LINE1}).encode(),
            "cell not a dict": json.dumps({"cells": [LINE1]}).encode(),
            "outputs not a list": json.dumps({"cells": [{"cell_type": "code", "outputs": LINE1}]}).encode(),
            "text mixed list": json.dumps(notebook([LINE1, 3])).encode(),
            "deep nesting": b"[" * 100_000 + b"]" * 100_000,
        }
        for name, raw in cases.items():
            with self.subTest(name=name), tree("ch05", None, raw=raw):
                (entry,), problems = render_track.read_tree()
                self.assertEqual(entry["score"], 200)
                self.assertEqual(problems, [])
                # And the whole render still produces a document.
                items = render_track.load_items()
                self.assertIn("octocat", render_track.render_markdown([entry], problems, items))

    def test_over_the_size_cap(self) -> None:
        # Valid JSON whose first cap+1 bytes are ALSO valid JSON (trailing
        # whitespace), so only the cap itself can refuse it.
        body = json.dumps(notebook(LINE1)).encode()
        cap = len(body) + 10
        with mock.patch.object(render_track, "MAX_NOTEBOOK_BYTES", cap):
            self.assertEqual(score("ch05", raw=body + b" " * 10), 600)
            self.assertEqual(score("ch05", raw=body + b" " * 11), 200)
            self.assertEqual(score("ch05", raw=body + b" " * 50), 200)

    def test_a_symlinked_notebook_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as elsewhere:
            target = Path(elsewhere) / "real.ipynb"
            target.write_text(json.dumps(notebook(LINE1)))
            with tree("ch05", notebook()) as bundle:
                (bundle / "notebook.ipynb").unlink()
                os.symlink(target, bundle / "notebook.ipynb")
                (entry,), _ = render_track.read_tree()
        self.assertEqual(entry["score"], 200)

    def test_a_symlinked_bundle_folder_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as elsewhere:
            with tree("ch05", notebook(LINE1)) as bundle:
                real = Path(elsewhere) / "ch05"
                bundle.rename(real)
                os.symlink(real, bundle)
                (entry,), _ = render_track.read_tree()
        self.assertEqual(entry["score"], 200)

    def test_missing_notebook(self) -> None:
        with tree("ch05", notebook(LINE1)) as bundle:
            (bundle / "notebook.ipynb").unlink()
            (entry,), _ = render_track.read_tree()
        self.assertEqual(entry["score"], 200)


class TheChallengeNotebookCarriesTheScore(unittest.TestCase):
    """`challenge.ipynb`, the demo notebook `bootcamp submit` attaches to ch05/ch10."""

    def score_with(self, item: str, homework: object, challenge: object | bytes) -> int:
        with tree(item, homework) as bundle:
            body = challenge if isinstance(challenge, bytes) else json.dumps(challenge).encode()
            (bundle / "challenge.ipynb").write_bytes(body)
            (entry,), problems = render_track.read_tree()
        self.assertEqual(problems, [])
        return entry["score"]

    def test_the_demo_notebook_alone_carries_the_points(self) -> None:
        self.assertEqual(self.score_with("ch05", notebook("no line\n"), notebook(LINE1)), 600)

    def test_the_larger_of_the_two_wins(self) -> None:
        low, high = "   week 1 challenge: 100/500\n", "   week 1 challenge: 450/500\n"
        self.assertEqual(self.score_with("ch05", notebook(low), notebook(high)), 650)
        self.assertEqual(self.score_with("ch05", notebook(high), notebook(low)), 650)
        # Never the sum: carrying both cannot double a score.
        self.assertEqual(self.score_with("ch05", notebook(LINE1), notebook(LINE1)), 600)

    def test_ch10_reads_week_2_from_it(self) -> None:
        line = "   week 2 challenge: 120/500\n"
        self.assertEqual(self.score_with("ch10", notebook(), notebook(line)), 320)

    def test_a_wrong_week_line_in_it_adds_nothing(self) -> None:
        line = "   week 2 challenge: 400/500\n"
        self.assertEqual(self.score_with("ch05", notebook(), notebook(line)), 200)

    def test_it_is_capped_like_the_homework_notebook(self) -> None:
        line = "   week 1 challenge: 999/500\n"
        self.assertEqual(self.score_with("ch05", notebook(), notebook(line)), 700)

    def test_unreadable_is_worth_zero(self) -> None:
        self.assertEqual(self.score_with("ch05", notebook(), b"{ not json" + LINE1.encode()), 200)

    def test_other_items_ignore_it(self) -> None:
        self.assertEqual(self.score_with("ch06", notebook(), notebook(LINE1)), 200)

    def test_a_symlinked_challenge_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as elsewhere:
            target = Path(elsewhere) / "real.ipynb"
            target.write_text(json.dumps(notebook(LINE1)))
            with tree("ch05", notebook()) as bundle:
                os.symlink(target, bundle / "challenge.ipynb")
                (entry,), _ = render_track.read_tree()
        self.assertEqual(entry["score"], 200)

    def test_the_claim_still_cannot_add_points(self) -> None:
        extra = {"evidence": {"challenge_sha256": "0" * 64}, "challenge": 500}
        with tree("ch05", notebook(), **extra) as bundle:
            (bundle / "challenge.ipynb").write_text(json.dumps(notebook("nothing\n")))
            stated = json.loads((bundle / "submission.json").read_text())
            stated["result"]["score"] = 700
            stated["result"]["output"] = LINE1
            (bundle / "submission.json").write_text(json.dumps(stated))
            (entry,), _ = render_track.read_tree()
        self.assertEqual(entry["score"], 200)


class TheRealTreeOnlyMovesWhereANotebookPrintedALine(unittest.TestCase):
    """Render the repository as it is, with and without challenge points.

    Every line of TRACK.md must be byte-identical except the rows of a learner
    whose ch05/ch10 notebook carries a line, and in those rows only that cell
    and the total may move.
    """

    def test_only_rows_with_a_challenge_line_change(self) -> None:
        items = render_track.load_items()
        with mock.patch.object(render_track, "challenge_points", return_value=0):
            before_entries, problems = render_track.read_tree()
        after_entries, after_problems = render_track.read_tree()
        self.assertEqual(problems, after_problems)
        before = render_track.render_markdown(before_entries, problems, items).splitlines()
        after = render_track.render_markdown(after_entries, problems, items).splitlines()
        self.assertEqual(len(before), len(after))

        carriers = {
            (e["github"], e["item"])
            for e in after_entries
            if e["item"] in render_track.CHALLENGE_WEEK
            and render_track.session_challenge(
                e["item"], render_track.SUBMISSIONS / e["github"] / e["item"]
            )
        }
        header = next(line for line in before if line.startswith("| Student | "))
        columns = [c.strip() for c in header.strip("|").split("|")]
        for old, new in zip(before, after):
            if old == new:
                continue
            old_cells = [c.strip() for c in old.strip("|").split("|")]
            new_cells = [c.strip() for c in new.strip("|").split("|")]
            student = old_cells[0]
            moved = {columns[i] for i, (a, b) in enumerate(zip(old_cells, new_cells)) if a != b}
            allowed = {item for (who, item) in carriers if who == student} | {"Total"}
            self.assertTrue(moved <= allowed, f"{student}: {moved} moved, only {allowed} may")
            self.assertTrue(moved - {"Total"}, f"{student}: the total moved on its own")

        # And track.json: only the carriers' scores differ.
        key = lambda e: (e["github"], e["item"])  # noqa: E731
        changed = {
            key(a) for a, b in zip(sorted(before_entries, key=key), sorted(after_entries, key=key))
            if a != b
        }
        self.assertTrue(changed <= carriers)


if __name__ == "__main__":
    unittest.main()
