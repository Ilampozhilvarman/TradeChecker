"""Minimal .env loader.

The project deliberately keeps its dependency list short, and the .env format we
need is trivial, so this replaces python-dotenv.

Rules:
- KEY=VALUE per line; blank lines and # comments are ignored
- an optional `export ` prefix is accepted
- surrounding single or double quotes are stripped
- a variable already present in the real environment always wins, so a value
  exported in the shell or set by the host is never clobbered by the file
"""

import os
from pathlib import Path

ENV_PATH = Path(__file__).with_name(".env")


def load(path=ENV_PATH):
    """Load KEY=VALUE pairs into os.environ. Returns the names it set."""
    path = Path(path)
    if not path.is_file():
        return []

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    loaded = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]

        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)

    return loaded
