# Short names for the commands you actually run.
#
# EVERY TARGET IS A ONE-LINE WRAPPER, on purpose. `make` is a convenience on
# macOS and Linux; it is not installed by default on Windows, and nothing in
# this course requires it. The `uv run ...` line under each target is the real
# command and works everywhere, so if `make` is missing, read the line and type
# it instead.

.PHONY: help setup check progress lab submit
.DEFAULT_GOAL := help

help:  ## show this
	@echo ""
	@echo "  make setup     install everything and check it works  (start here)"
	@echo "  make lab       open the notebooks"
	@echo "  make progress  what you have done so far"
	@echo ""
	@echo "  make check CH=ch02    run one session's checks"
	@echo "  make submit CH=ch02 GH=your-github-login"
	@echo ""
	@echo "  Every one of these is a short name for a 'uv run ...' command."
	@echo "  No make on your machine? Open the Makefile and type the line."
	@echo ""

setup:  ## install everything, then say whether it worked
	uv run bootcamp start

lab:  ## open the notebooks
	uv run jupyter lab

progress:  ## the whole course at a glance
	uv run bootcamp progress

check:  ## make check CH=ch02
	@test -n "$(CH)" || (echo "which session? e.g.  make check CH=ch02"; exit 2)
	uv run bootcamp check $(CH)

submit:  ## make submit CH=ch02 GH=your-github-login
	@test -n "$(CH)" || (echo "which session? e.g.  make submit CH=ch02 GH=you"; exit 2)
	@test -n "$(GH)" || (echo "your GitHub login? e.g.  make submit CH=ch02 GH=you"; exit 2)
	uv run bootcamp submit $(CH) --github $(GH)
