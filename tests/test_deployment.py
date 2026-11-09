import pytest
from pipeline.db import engine
from pipeline.storage import artifact_root
from scripts.start_server import main


@pytest.mark.parametrize('scheme', ['postgres', 'postgresql', 'postgresql+psycopg'])
def test_neon_url_uses_installed_driver_and_preserves_tls(scheme):
    url = f'{scheme}://demo:placeholder@db.example.test/neondb?sslmode=require&channel_binding=require'
    db = engine(url)
    assert db.url.drivername == 'postgresql+psycopg'
    assert db.url.query['sslmode'] == 'require'
    assert db.url.query['channel_binding'] == 'require'
    assert engine(url) is db


def test_artifact_directory_is_configurable(tmp_path, monkeypatch):
    monkeypatch.setenv('ARTIFACT_ROOT', str(tmp_path / 'persistent'))
    assert artifact_root() == tmp_path / 'persistent'


@pytest.mark.parametrize('url', ['', 'sqlite:///local.db'])
def test_hosted_launcher_never_silently_uses_local_sqlite(monkeypatch, url):
    monkeypatch.setenv('DATABASE_URL', url)
    with pytest.raises(SystemExit, match='PostgreSQL'):
        main()
