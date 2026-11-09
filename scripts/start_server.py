"""Render entrypoint: migrate Neon, then run Streamlit on Render's assigned port."""
import os
import sys
from pathlib import Path
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from pipeline.environment import load_local_environment
    load_local_environment()
    database_url = os.getenv('DATABASE_URL', '')
    if not database_url:
        raise SystemExit('Set DATABASE_URL to your Neon PostgreSQL connection URL in Render environment settings.')
    try:
        backend = make_url(database_url).get_backend_name()
    except Exception:
        raise SystemExit('DATABASE_URL must be a valid PostgreSQL connection URL.') from None
    if backend not in ('postgres', 'postgresql'):
        raise SystemExit('Hosted deployments require PostgreSQL. Set DATABASE_URL to your Neon connection URL.')
    os.chdir(ROOT)
    from pipeline.storage import artifact_root
    artifact_root().mkdir(parents=True, exist_ok=True)
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config(str(ROOT / 'alembic.ini')), 'head')
    port = str(int(os.getenv('PORT', '10000')))
    args = [sys.executable, '-m', 'streamlit', 'run', str(ROOT / 'app.py'),
            '--server.address=0.0.0.0', '--server.port=' + port,
            '--server.headless=true', '--server.fileWatcherType=none',
            '--browser.gatherUsageStats=false', '--client.showErrorDetails=none']
    os.execv(sys.executable, args)


if __name__ == '__main__':
    main()
