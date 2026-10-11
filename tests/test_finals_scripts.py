"""The two shell scripts `finals.yml` runs: scoring a batch, and pushing the result.

    python3 -m unittest discover -s tests

No network: `curl` is a fake on PATH, and the remote is a bare repository in a
temporary directory.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCORE = ROOT / "scripts" / "finals_score.sh"
PUSH = ROOT / "scripts" / "push_with_retry.sh"

HAVE_TOOLS = all(shutil.which(tool) for tool in ("bash", "jq", "git"))

#: Answers 500 for the learner named in FAIL_FOR, 200 with a body that is not
#: JSON for GARBAGE_FOR, and 200 with a result otherwise.
#: The learner is read from the posted payload, exactly as the API would.
FAKE_CURL = r"""#!/usr/bin/env bash
out=""; data=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift ;;
    --data) data="$2"; shift ;;
  esac
  shift
done
who="$(printf '%s' "$data" | jq -r .github)"
if [ "$who" = "$GARBAGE_FOR" ]; then
  echo 'not a result' > "$out"
  printf 200
  exit 0
fi
if [ "$who" = "$FAIL_FOR" ]; then
  echo '{"error":"the score could not be recorded"}' > "$out"
  printf 500
  exit 0
fi
jq -n --arg g "$who" '{github: $g, question_set_id: "q", score: {percent: 90},
  gates: {}, passed: true, certificate_eligible: true, results: [], receipt: {r: 1}}' > "$out"
printf 200
"""

ANSWERS = {
    "cohort": "2026-09",
    "course_release": "a" * 40,
    "question_set_id": "q",
    "answers": {"fa-01": {"answer": "x", "citations": [], "confidence": 1, "needs_human_review": False}},
}


def git_env() -> dict:
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="test",
        GIT_AUTHOR_EMAIL="",
        GIT_COMMITTER_NAME="test",
        GIT_COMMITTER_EMAIL="",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    return env


@unittest.skipUnless(HAVE_TOOLS, "needs bash, jq and git")
class ScoringABatch(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name)
        bin_dir = self.work / "bin"
        bin_dir.mkdir()
        (bin_dir / "curl").write_text(FAKE_CURL)
        (bin_dir / "curl").chmod(0o755)
        self.path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
        lines = []
        for learner in ("alice", "bob", "carol"):
            bundle = self.work / "submissions" / learner / "final"
            bundle.mkdir(parents=True)
            (bundle / "answers.json").write_text(json.dumps(ANSWERS))
            (bundle / "submission.json").write_text(
                json.dumps({"repo": f"https://github.com/{learner}/agent", "commit": "b" * 40})
            )
            lines.append(f"submissions/{learner}/final/answers.json")
        (self.work / "finals.txt").write_text("\n".join(lines) + "\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_score(self, fail_for: str, garbage_for: str = "") -> subprocess.CompletedProcess:
        env = dict(
            os.environ,
            PATH=self.path,
            API="https://api.invalid",
            KEY="k",
            FAIL_FOR=fail_for,
            GARBAGE_FOR=garbage_for,
        )
        return subprocess.run(
            ["bash", str(SCORE), "finals.txt"], cwd=self.work, env=env, capture_output=True, text=True
        )

    def test_one_failure_does_not_stop_the_others(self) -> None:
        result = self.run_score(fail_for="bob")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("::error::bob scored HTTP 500", result.stdout)
        # Everyone after the failure was still scored, and bob left no trace.
        self.assertTrue((self.work / "finals/alice/result.json").is_file())
        self.assertTrue((self.work / "finals/carol/result.json").is_file())
        self.assertFalse((self.work / "finals/bob").exists() and any((self.work / "finals/bob").iterdir()))
        self.assertEqual((self.work / "failed.txt").read_text().split(), ["bob"])
        self.assertEqual(
            (self.work / "scored.txt").read_text().split(),
            ["finals/alice/result.json", "finals/carol/result.json"],
        )

    def test_a_scored_result_links_to_the_code_and_drops_the_receipt(self) -> None:
        self.run_score(fail_for="nobody")
        result = json.loads((self.work / "finals/alice/result.json").read_text())
        self.assertEqual(result["repo"], "https://github.com/alice/agent")
        self.assertEqual(result["commit"], "b" * 40)
        self.assertNotIn("receipt", result)
        self.assertEqual((self.work / "failed.txt").read_text(), "")

    def test_a_200_that_is_not_a_result_leaves_no_partial_file(self) -> None:
        result = self.run_score(fail_for="nobody", garbage_for="carol")
        self.assertEqual(result.returncode, 0)
        self.assertEqual((self.work / "failed.txt").read_text().split(), ["carol"])
        self.assertEqual(list((self.work / "finals/carol").iterdir()), [])
        self.assertTrue((self.work / "finals/alice/result.json").is_file())

    def test_an_unreadable_answers_file_is_a_failure_not_an_abort(self) -> None:
        (self.work / "submissions/alice/final/answers.json").write_text("{not json")
        result = self.run_score(fail_for="nobody")
        self.assertEqual(result.returncode, 0)
        self.assertEqual((self.work / "failed.txt").read_text().split(), ["alice"])
        self.assertTrue((self.work / "finals/carol/result.json").is_file())


@unittest.skipUnless(HAVE_TOOLS, "needs bash, jq and git")
class PushingAfterSomebodyElse(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.env = git_env()
        self.git(self.base, "init", "-q", "--bare", "-b", "main", "remote.git")
        self.git(self.base, "clone", "-q", "remote.git", "seed")
        (self.base / "seed/track.json").write_text("{}\n")
        self.git(self.base / "seed", "add", ".")
        self.git(self.base / "seed", "commit", "-q", "-m", "seed")
        self.git(self.base / "seed", "push", "-q", "origin", "HEAD:main")
        self.git(self.base, "clone", "-q", "remote.git", "finals")
        self.git(self.base, "clone", "-q", "remote.git", "collect")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd, env=self.env, check=True, capture_output=True, text=True
        ).stdout

    def commit(self, clone: str, path: str, text: str) -> None:
        target = self.base / clone / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        self.git(self.base / clone, "add", ".")
        self.git(self.base / clone, "commit", "-q", "-m", f"{clone}: {path}")

    def push(self, clone: str, attempts: int = 5) -> subprocess.CompletedProcess:
        env = dict(self.env, PUSH_BACKOFF="0", PUSH_ATTEMPTS=str(attempts))
        return subprocess.run(
            ["bash", str(PUSH)], cwd=self.base / clone, env=env, capture_output=True, text=True
        )

    def test_a_refused_push_rebases_and_lands(self) -> None:
        # finals commits a result; collect pushes a track first, so the first
        # push from finals is refused as non-fast-forward.
        self.commit("finals", "finals/alice/result.json", "{}\n")
        self.commit("collect", "track.json", '{"moved": true}\n')
        self.git(self.base / "collect", "push", "-q", "origin", "HEAD:main")

        result = self.push("finals")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pushed on attempt 2", result.stdout)
        log = self.git(self.base / "remote.git", "log", "--format=%s", "main")
        self.assertIn("finals: finals/alice/result.json", log)
        self.assertIn("collect: track.json", log)

    def test_a_push_that_cannot_land_fails_loudly(self) -> None:
        self.commit("finals", "finals/alice/result.json", "{}\n")
        self.commit("collect", "track.json", '{"moved": true}\n')
        self.git(self.base / "collect", "push", "-q", "origin", "HEAD:main")

        result = self.push("finals", attempts=1)
        self.assertEqual(result.returncode, 1)
        self.assertIn("refused 1 times", result.stdout)

    def test_a_conflicting_rebase_stops_rather_than_guesses(self) -> None:
        self.commit("finals", "track.json", '{"finals": "should never write this"}\n')
        self.commit("collect", "track.json", '{"moved": true}\n')
        self.git(self.base / "collect", "push", "-q", "origin", "HEAD:main")

        result = self.push("finals")
        self.assertEqual(result.returncode, 1)
        self.assertIn("conflicted", result.stdout)


if __name__ == "__main__":
    unittest.main()
