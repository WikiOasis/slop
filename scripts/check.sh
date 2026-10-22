#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
ruff check src tests scripts
ruff format --check src tests scripts
python3 scripts/check_no_comments.py
npx --yes prettier@3.6.2 --check .
PYTHONPATH=src:tests python3 -m unittest discover -s tests -t tests
