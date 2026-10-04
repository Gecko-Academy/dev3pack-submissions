"""`challenge.ipynb`: the optional third file in a ch05/ch10 bundle.

    python3 -m unittest discover -s tests

`bootcamp submit` attaches the learner's saved demo notebook (demo 08 for ch05,
demo 10 for ch10) so the weekly challenge's points reach the leaderboard. It is
held to the homework notebook's rules: regular file, same size cap, a notebook,
and bound to the claim by `evidence.challenge_sha256`.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_bundle  # noqa: E402
import render_track  # noqa: E402

OWNER = "octocat"
HOMEWORK = json.dumps({"cells": [], "nbformat": 4}).encode()
CHALLENGE = json.dumps(
    {"cells": [{"cell_type": "code", "outputs": [
        {"output_type": "stream", "text": "   week 1 challenge: 400/500\n"}
    ]}]}
).encode()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class ChallengeBundle(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def bundle(self, item: str = "ch05", challenge: bytes | None = CHALLENGE,
               recorded: bytes | None = None, with_id: bool = True) -> Path:
        """A bundle as `bootcamp submit` writes it. `recorded` is what the claim hashed."""
        directory = self.root / "submissions" / OWNER / item
        directory.mkdir(parents=True)
        (directory / "notebook.ipynb").write_bytes(HOMEWORK)
        evidence = {"notebook_sha256": sha(HOMEWORK)}
        hashed = recorded if recorded is not None else challenge
        if hashed is not None:
            evidence["challenge_sha256"] = sha(hashed)
        if challenge is not None:
            (directory / "challenge.ipynb").write_bytes(challenge)
        claim: dict = {
            "schema": check_bundle.SCHEMA,
            "chapter": item,
            "student": {"github": OWNER},
            "result": {"ran": True, "scored": True, "passed": ["a", "b"], "score": 200},
            "evidence": evidence,
        }
        if with_id:
            claim["submission_id"] = check_bundle.submission_id_for(claim)
        (directory / "submission.json").write_text(json.dumps(claim))
        return directory

    def assertRefused(self, directory: Path, fragment: str) -> None:
        found = "\n".join(check_bundle.problems_with(directory))
        self.assertIn(fragment, found)

    # -- accepted ---------------------------------------------------------------

    def test_accepted_in_ch05(self) -> None:
        self.assertEqual(check_bundle.problems_with(self.bundle("ch05")), [])

    def test_accepted_in_ch10(self) -> None:
        self.assertEqual(check_bundle.problems_with(self.bundle("ch10")), [])

    def test_absent_is_valid(self) -> None:
        self.assertEqual(check_bundle.problems_with(self.bundle(challenge=None)), [])

    def test_crlf_conversion_is_tolerated_like_the_homework_notebook(self) -> None:
        crlf = CHALLENGE.replace(b"\n", b"\r\n")
        directory = self.bundle(challenge=CHALLENGE, recorded=crlf)
        self.assertEqual(check_bundle.problems_with(directory), [])

    # -- refused ----------------------------------------------------------------

    def test_refused_in_any_other_item(self) -> None:
        for item in ("ch06", "ch04", "w01"):
            with self.subTest(item=item):
                self.assertRefused(
                    self.bundle(item), "challenge.ipynb is only accepted in ch05 and ch10 bundles"
                )

    def test_any_other_extra_file_is_still_refused(self) -> None:
        directory = self.bundle()
        (directory / "challenge2.ipynb").write_bytes(CHALLENGE)
        self.assertRefused(directory, "unexpected file in the bundle: challenge2.ipynb")

    def test_oversize_refused(self) -> None:
        directory = self.bundle()
        size = len(CHALLENGE)
        with mock.patch.object(check_bundle, "MAX_NOTEBOOK_BYTES", size):
            self.assertEqual(check_bundle.problems_with(directory), [])
        with mock.patch.object(check_bundle, "MAX_NOTEBOOK_BYTES", size - 1):
            self.assertRefused(directory, f"challenge.ipynb is {size} bytes, over the")

    def test_symlink_refused(self) -> None:
        directory = self.bundle()
        target = self.root / "elsewhere.ipynb"
        target.write_bytes(CHALLENGE)
        (directory / "challenge.ipynb").unlink()
        os.symlink(target, directory / "challenge.ipynb")
        self.assertRefused(directory, "challenge.ipynb must be a regular file")

    def test_a_directory_is_refused(self) -> None:
        directory = self.bundle(challenge=None)
        (directory / "challenge.ipynb").mkdir()
        self.assertRefused(directory, "challenge.ipynb must be a regular file")

    def test_not_json_refused(self) -> None:
        for name, raw in {
            "not json": b"{ nope",
            "not utf-8": b"\xff\xfe{}",
            "no cells": b'{"nbformat": 4}',
            "cells not a list": b'{"cells": "x"}',
            "a list": b"[]",
            "deep nesting": b"[" * 100_000 + b"]" * 100_000,
        }.items():
            with self.subTest(name=name):
                directory = self.bundle(item="ch05", challenge=raw)
                self.assertRefused(directory, "challenge.ipynb: not a notebook")
                for path in sorted(directory.iterdir()):
                    path.unlink()
                directory.rmdir()

    def test_edited_after_submit_refused(self) -> None:
        # The attack: hand in a demo notebook, then edit its printed line up.
        honest = CHALLENGE.replace(b"400/500", b"100/500")
        directory = self.bundle(challenge=CHALLENGE, recorded=honest)
        self.assertRefused(directory, "challenge.ipynb is not the one this bundle was submitted")

    def test_attached_with_no_digest_refused(self) -> None:
        directory = self.bundle(challenge=None)
        (directory / "challenge.ipynb").write_bytes(CHALLENGE)
        self.assertRefused(directory, "challenge.ipynb is not the one this bundle was submitted")

    def test_a_recorded_digest_with_no_file_refused(self) -> None:
        directory = self.bundle(challenge=None, recorded=CHALLENGE)
        self.assertRefused(directory, "the claim records a challenge.ipynb but the bundle has none")

    def test_rewriting_the_digest_to_match_breaks_the_id(self) -> None:
        # Edit the notebook AND the digest: the claim's own fingerprint catches it.
        directory = self.bundle()
        forged = CHALLENGE.replace(b"400/500", b"500/500")
        (directory / "challenge.ipynb").write_bytes(forged)
        claim = json.loads((directory / "submission.json").read_text())
        claim["evidence"]["challenge_sha256"] = sha(forged)
        (directory / "submission.json").write_text(json.dumps(claim))
        self.assertRefused(directory, "submission_id is")

    # -- one source for the two lists -----------------------------------------

    def test_the_checker_and_the_renderer_agree_on_the_items(self) -> None:
        self.assertEqual(set(render_track.CHALLENGE_WEEK), check_bundle.CHALLENGE_ITEMS)


if __name__ == "__main__":
    unittest.main()
