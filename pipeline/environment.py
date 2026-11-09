"""Read simple local KEY=value settings without overriding hosted environment values."""
import os
from pathlib import Path


def load_local_environment(path=None):
    source = Path(path) if path else Path(__file__).resolve().parents[1] / '.env'
    if not source.is_file():
        return
    for line in source.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if not key.isidentifier():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        os.environ.setdefault(key, value)
