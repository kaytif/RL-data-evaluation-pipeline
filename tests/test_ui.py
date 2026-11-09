"""Exercise the real migration and UI with synthetic reviewer inputs."""
from pathlib import Path
import subprocess
import sys

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine
from streamlit.testing.v1 import AppTest
from pipeline.db import Base
from pipeline import models


def test_migrated_streamlit_review_cycle(tmp_path, monkeypatch):
    url = f'sqlite:///{tmp_path / "ui.db"}'
    monkeypatch.setenv('DATABASE_URL', url)
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'],
                   check=True, capture_output=True, text=True, timeout=30)
    with create_engine(url).connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []

    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'legacy_demo.py'), default_timeout=30).run()

    def click(label):
        next(b for b in app.button if b.label == label).click().run()
        assert not app.exception

    assert not app.exception
    click('Draft proposed specification')
    app.text_input[0].set_value('Synthetic test reviewer')
    app.text_input[1].set_value('Test-only approval of bundled criterion')
    click('Approve specification as human reviewer')
    click('Load bundled flawed task v1')
    click('Run executable checks')
    assert 'findings_awaiting_review' in app.info[0].value
    click('Confirm findings and generate local revision request')
    click('Load corrected bundled task v2')
    click('Run executable checks')
    assert 'awaiting_release_approval' in app.info[0].value
    click('Approve release as human reviewer')
    assert 'approved_export' in app.info[0].value
