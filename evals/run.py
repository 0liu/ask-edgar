"""Run the evals from the repository root: ``uv run python evals/run.py [--live]``.

The same as ``ask-edgar evals``; the logic lives in ``ask_edgar.evals``.
"""

import sys

from ask_edgar.cli import app

if __name__ == "__main__":
    app(["evals", *sys.argv[1:]])
