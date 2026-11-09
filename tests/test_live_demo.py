from pathlib import Path
from sqlalchemy import select, func
from streamlit.testing.v1 import AppTest
from pipeline.db import sessions
from pipeline.live_demo import load_demo
from pipeline.assessment_agent import AssessmentJob, AssessedAttempt
from pipeline.csv_uploads import TaskUploadBatch

def test_demo_results_are_real_repeatable_and_unapproved(tmp_path):
    factory = sessions(f'sqlite:///{tmp_path / "demo.db"}')
    with factory.begin() as session:
        bid, jid = load_demo(session, tmp_path / 'files')
        assert load_demo(session, tmp_path / 'files') == (bid, jid)
        job = session.get(AssessmentJob, jid)
        assert job.approval is None
        assert {k: job.summary[k] for k in ['tasks', 'attempts', 'success', 'failure', 'unverified']} == dict(tasks=3, attempts=6, success=2, failure=2, unverified=2)
        assert session.scalar(select(func.count()).select_from(TaskUploadBatch)) == 1
        attempts = list(session.scalars(select(AssessedAttempt).where(AssessedAttempt.job_id == jid)))
        assert all(a.result['reasoning'] for a in attempts)

def test_assessment_ui_has_no_synthetic_sample_or_rules_option(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "demo-ui.db"}')
    monkeypatch.setattr('pipeline.csv_uploads.ROOT', tmp_path / 'files')
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=30).run()
    app.switch_page('pages/3_Assess_tasks.py').run()
    assert not app.exception
    assert not any(b.label == 'Load and assess sample data' for b in app.button)
    assert not any(r.label == 'Assessment engine' for r in app.radio)
    assert any('Upload a dataset' in i.value for i in app.info)
