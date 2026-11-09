"""Start locally with the configured database, migrating PostgreSQL before launch."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8502)
    args = parser.parse_args()
    os.chdir(ROOT)
    from pipeline.environment import load_local_environment
    load_local_environment()
    from pipeline.db import engine
    if engine().dialect.name == 'postgresql':
        from alembic import command
        from alembic.config import Config
        print('Applying PostgreSQL migrations before startup...', flush=True)
        command.upgrade(Config(str(ROOT / 'alembic.ini')), 'head')
    return subprocess.call([sys.executable, '-m', 'streamlit', 'run', str(ROOT / 'app.py'),
        '--server.address=127.0.0.1', f'--server.port={args.port}', '--browser.gatherUsageStats=false'])


if __name__ == '__main__':
    raise SystemExit(main())
