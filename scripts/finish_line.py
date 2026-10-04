"""Who finished: the Gecko capstone link and the final, one row per student.

    python3 scripts/finish_line.py            # a table
    python3 scripts/finish_line.py --csv      # the same, for a spreadsheet

Run by the instructor from a checkout of this repository, with the `gh` CLI
signed in. Read-only: it reads `submissions/*/gecko/`, `finals/*/result.json`,
and, for each capstone link, the student's PUBLIC repository at the handed-in
commit through the GitHub API.

Nothing here grades. The evidence columns say what the judges will find at that
commit: how many receipts, whether the smoke report is there, and whether
`docs/DEFENCE.md` still carries the template's placeholder lines.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import subprocess
import sys

from render_track import read_finish_line

#: Lines the template's DEFENCE.md ships with; one still present means unfilled.
DEFENCE_PLACEHOLDERS = ("<your", "TODO", "TBD")


def _gh(path: str) -> object | None:
    ran = subprocess.run(
        ["gh", "api", path], capture_output=True, text=True, check=False, timeout=30
    )
    if ran.returncode != 0:
        return None
    return json.loads(ran.stdout)


def evidence(repo: str, commit: str) -> dict[str, object]:
    """What the handed-in commit holds. `None` values mean GitHub would not say."""
    owner_name = repo.removeprefix("https://github.com/")
    tree = _gh(f"repos/{owner_name}/git/trees/{commit}?recursive=1")
    if not isinstance(tree, dict):
        return {"reachable": False, "receipts": None, "smoke": None, "defence": None}
    paths = [entry["path"] for entry in tree.get("tree", []) if entry.get("type") == "blob"]
    receipts = sum(1 for p in paths if p.startswith("receipts/") and p.endswith(".md"))
    defence = None
    if "docs/DEFENCE.md" in paths:
        blob = _gh(f"repos/{owner_name}/contents/docs/DEFENCE.md?ref={commit}")
        if isinstance(blob, dict) and blob.get("content"):
            text = base64.b64decode(blob["content"]).decode("utf-8", "replace")
            defence = not any(marker in text for marker in DEFENCE_PLACEHOLDERS)
    return {
        "reachable": True,
        "receipts": receipts,
        "smoke": "smoke-report.json" in paths,
        "defence": defence,
    }


def rows() -> list[dict[str, object]]:
    out = []
    for row in read_finish_line():
        gecko, final = row["gecko"], row["final"]
        seen = evidence(gecko["repo"], gecko["commit"]) if gecko else {}
        out.append(
            {
                "github": row["github"],
                "final_percent": final["percent"] if final else None,
                "final_passed": final["passed"] if final else None,
                "eligible": final["certificate_eligible"] if final else None,
                "capstone": gecko["url"] if gecko else None,
                "reachable": seen.get("reachable"),
                "receipts": seen.get("receipts"),
                "smoke": seen.get("smoke"),
                "defence_filled": seen.get("defence"),
                "both": bool(gecko and final),
            }
        )
    return out


def _cell(value: object) -> str:
    if value is None:
        return "-"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", action="store_true", help="CSV to stdout")
    args = parser.parse_args(argv)
    table = rows()
    if args.csv:
        if table:
            writer = csv.DictWriter(sys.stdout, fieldnames=list(table[0]))
            writer.writeheader()
            writer.writerows(table)
        return 0
    heads = ("student", "final", "eligible", "receipts", "smoke", "defence", "capstone")
    print(" | ".join(heads))
    for row in table:
        final = f"{row['final_percent']}%" if row["final_percent"] is not None else "-"
        capstone = row["capstone"] or "-"
        if row["capstone"] and row["reachable"] is False:
            capstone += "  (commit not readable)"
        print(
            " | ".join(
                (
                    str(row["github"]),
                    final,
                    _cell(row["eligible"]),
                    _cell(row["receipts"]),
                    _cell(row["smoke"]),
                    _cell(row["defence_filled"]),
                    capstone,
                )
            )
        )
    both = sum(1 for row in table if row["both"])
    print(f"\n{len(table)} student(s); {both} handed in both.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
