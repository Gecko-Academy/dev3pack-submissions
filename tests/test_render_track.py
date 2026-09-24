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


if __name__ == "__main__":
    unittest.main()
