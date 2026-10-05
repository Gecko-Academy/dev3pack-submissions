"""A merged final must not show up in the track as a problem.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import render_track  # noqa: E402


class FinalsStayOutOfTheTrack(unittest.TestCase):
    def test_a_final_bundle_is_neither_a_row_nor_a_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            submissions = Path(tmp) / "submissions"
            final = submissions / "octocat" / "final"
            final.mkdir(parents=True)
            (final / "submission.json").write_text(json.dumps({"kind": "final"}))
            (final / "answers.json").write_text("{}")
            with mock.patch.object(render_track, "SUBMISSIONS", submissions), mock.patch.object(
                render_track, "ROOT", Path(tmp)
            ):
                entries, problems = render_track.read_tree()
        self.assertEqual(entries, [])
        self.assertEqual(problems, [])


class FinishLine(unittest.TestCase):
    def test_a_gecko_link_and_a_final_share_one_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            submissions = Path(tmp) / "submissions"
            gecko = submissions / "octocat" / "gecko"
            gecko.mkdir(parents=True)
            commit = "0123456789abcdef0123456789abcdef01234567"
            (gecko / "submission.json").write_text(
                json.dumps(
                    {
                        "kind": "gecko",
                        "github": "octocat",
                        "repo": "https://github.com/octocat/my-gecko-buyer",
                        "commit": commit,
                    }
                )
            )
            finals = Path(tmp) / "finals" / "octocat"
            finals.mkdir(parents=True)
            (finals / "result.json").write_text(
                json.dumps(
                    {"score": {"percent": 80}, "passed": True, "certificate_eligible": True}
                )
            )
            with mock.patch.object(render_track, "SUBMISSIONS", submissions), mock.patch.object(
                render_track, "FINALS", Path(tmp) / "finals"
            ), mock.patch.object(render_track, "ROOT", Path(tmp)):
                entries, problems = render_track.read_tree()
                finish = render_track.read_finish_line()
        self.assertEqual((entries, problems), ([], []))
        (row,) = finish
        self.assertEqual(row["gecko"]["url"], f"https://github.com/octocat/my-gecko-buyer/tree/{commit}")
        self.assertTrue(row["final"]["certificate_eligible"])
        markdown = render_track.render_markdown([], [], [], finish)
        self.assertIn(f"| octocat | [0123456](https://github.com/octocat/my-gecko-buyer/tree/{commit}) | 80% | eligible |", markdown)

    def test_a_link_that_fails_its_check_is_not_shown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            submissions = Path(tmp) / "submissions"
            gecko = submissions / "octocat" / "gecko"
            gecko.mkdir(parents=True)
            (gecko / "submission.json").write_text(json.dumps({"kind": "gecko"}))
            with mock.patch.object(render_track, "SUBMISSIONS", submissions), mock.patch.object(
                render_track, "FINALS", Path(tmp) / "finals"
            ):
                self.assertEqual(render_track.read_finish_line(), [])


if __name__ == "__main__":
    unittest.main()
