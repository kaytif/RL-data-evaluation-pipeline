from pipeline.environment import load_local_environment


def test_local_settings_preserve_full_database_url(tmp_path, monkeypatch):
    monkeypatch.delenv('TEST_NEON_URL', raising=False)
    path = tmp_path / '.env'
    url = 'postgresql://demo:placeholder@db.example.test/neondb?sslmode=require&channel_binding=require'
    path.write_text(f'# Local settings\nTEST_NEON_URL="{url}"\n', encoding='utf-8')
    load_local_environment(path)
    import os
    assert os.environ['TEST_NEON_URL'] == url
    monkeypatch.delenv('TEST_NEON_URL')


def test_hosted_values_always_override_local_file(tmp_path, monkeypatch):
    monkeypatch.setenv('TEST_DATABASE_SETTING', 'hosted-value')
    path = tmp_path / '.env'
    path.write_text('TEST_DATABASE_SETTING=local-value\n', encoding='utf-8')
    load_local_environment(path)
    import os
    assert os.environ['TEST_DATABASE_SETTING'] == 'hosted-value'
