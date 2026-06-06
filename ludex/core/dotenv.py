"""Minimal dependency-free .env loader.

Lets BYO-key users place ANTHROPIC_API_KEY / OPENAI_API_KEY in a git-ignored
.env file instead of exporting them. Existing environment variables win
(setdefault), so a real export always overrides the file.
"""

import os


def load_dotenv(path: str = "") -> None:
    """Load KEY=VALUE lines from `.env` (repo root by default) into os.environ."""
    if not path:
        path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
