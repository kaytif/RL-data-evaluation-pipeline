import csv
import io
from pathlib import Path
import pytest
from sqlalchemy import select
from streamlit.testing.v1 import AppTest
from pipeline.assessment_agent import AssessmentJob, AssessedAttempt, run_agent
from pipeline.csv_uploads import UploadedTaskCSV
from pipeline.agent_dataset import approve_dataset, export_scored_source_csv
from pipeline.reward_review import reward_statistics, reward_review_payload, quick_report
from test_assessment_agent import setup
from test_evaluation_reports import DATA, MockJudge


def test_reward_rates_keep_unverified_separate_and_payload_uses_saved_results():
    results = [dict(score=s, category='Words', reasoning='Observed evidence') for s in (1, 0, None, 1)]
    stats = reward_statistics(results)
    assert stats['reward_1_rate'] == .5
    assert stats['reward_0_rate'] == .25
    assert stats['unverified_rate'] == .25
    assert stats['success_rate_verified'] == 2 / 3
    assert reward_statistics([])['reward_1_rate'] is None
    payload = reward_review_payload({'required_capabilities': 'Vocabulary'}, 'selected.csv', results)
    assert payload['source_file'] == 'selected.csv'
    assert payload['statistics'] == stats
    assert len(payload['examples']) == 4
    report = quick_report({'required_capabilities': 'Vocabulary'}, [], None, stats)
    assert len(report.splitlines()) == 4
    assert '1: 2 (50.0%)' in report and '0: 1 (25.0%)' in report
    assert 'unverified: 1 (25.0%)' in report


def test_scored_csv_preserves_original_columns_and_correctly_joins_rewards(tmp_path):
    _, factory, bid = setup(tmp_path, DATA)
    with factory.begin() as session:
        jid = run_agent(session, bid, MockJudge(tmp_path / 'cache'))
        file = session.scalar(select(UploadedTaskCSV).where(UploadedTaskCSV.batch_id == bid))
        with pytest.raises(ValueError, match='approval'):
            export_scored_source_csv(session, jid, file.id)
        approve_dataset(session, jid, 'Test reviewer', 'Synthetic test only')
        raw = export_scored_source_csv(session, jid, file.id)
        rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
        source = list(csv.DictReader(io.StringIO(DATA.decode())))
        assert len(rows) == len(source) == 2
        for n, (row, original) in enumerate(zip(rows, source), 2):
            assert {key: row[key] for key in original} == original
            assert row['assessment_source_row'] == str(n)
            assert row['assessment_reasoning'] and row['assessment_attempt_id']
        assert [r['assessment_reward'] for r in rows] == ['1', '0']
        assert Path(file.path).read_bytes() == DATA
        attempts = list(session.scalars(select(AssessedAttempt).where(AssessedAttempt.job_id == jid)))
        item = attempts[0].result
        attempts[0].result = {**item, 'score': None, 'reasoning': 'Insufficient evidence.'}
        session.flush()
        rows = list(csv.DictReader(io.StringIO(export_scored_source_csv(session, jid, file.id).decode('utf-8-sig'))))
        assert rows[0]['assessment_reward'] == ''
        assert rows[0]['assessment_reasoning'] == 'Insufficient evidence.'
        # A source row containing two assessed attempts must not lose either reward.
        session.add(AssessedAttempt(job_id=jid, task_id=file.id, result={**item,
            'attempt_id': 'additional-attempt', 'score': 0, 'reasoning': 'Second response failed.'}))
        session.flush()
        expanded = list(csv.DictReader(io.StringIO(export_scored_source_csv(session, jid, file.id).decode('utf-8-sig'))))
        assert len(expanded) == 3
        assert [r['assessment_source_row'] for r in expanded] == ['2', '2', '3']
        assert expanded[1]['assessment_reasoning'] == 'Second response failed.'
        with pytest.raises(ValueError, match='Choose a file'):
            export_scored_source_csv(session, jid, 'not-this-file')


def test_reward_ui_summary_persistence_and_downloads(tmp_path, monkeypatch):
    from pipeline import llm_assessment
    url, factory, bid = setup(tmp_path, DATA)
    with factory.begin() as session:
        jid = run_agent(session, bid, MockJudge(tmp_path / 'cache'))
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    llm_assessment._loaded_mtime = Path(llm_assessment.__file__).stat().st_mtime_ns
    captured = []
    def review(self, payload):
        captured.append(payload)
        return llm_assessment.RewardReview(summary='One success and one failure; neither establishes grader reliability.',
            strengths=['Both outcomes are represented.'], concerns=['This is a small sample.'])
    monkeypatch.setattr(llm_assessment.OpenAIJudge, 'review_rewards', review)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=30).run()
    app.switch_page('pages/3_Assess_tasks.py').run()
    assert not app.exception
    assert next(m for m in app.metric if m.label == 'Success rate (reward 1)').value == '50.0%'
    assert next(m for m in app.metric if m.label == 'Failure rate (reward 0)').value == '50.0%'
    assert any('Successful answers' in e.label for e in app.expander)
    assert any('Unsuccessful answers' in e.label for e in app.expander)
    next(b for b in app.button if b.label == 'Summarize rewards with LLM').click().run()
    assert not app.exception
    assert captured[0]['statistics']['reward_1'] == captured[0]['statistics']['reward_0'] == 1
    with factory() as session:
        job = session.get(AssessmentJob, jid)
        assert job.summary['reward_review']['summary'].startswith('One success')
        assert job.approval is None
    next(t for t in app.text_input if t.label == 'Reviewer name').set_value('Test reviewer')
    next(t for t in app.text_area if t.label == 'Review rationale').set_value('Synthetic UI test only')
    next(c for c in app.checkbox if c.label.startswith('I reviewed the task grouping')).check().run()
    next(b for b in app.button if b.label == 'Approve dataset export').click().run()
    assert not app.exception
    labels = {button.proto.label for button in app.get('download_button')}
    assert 'Download original CSV + rewards & reasoning' in labels
    assert 'Download quick report' in labels
    assert 'Download results CSV' in labels
    def unavailable(self, payload):
        raise RuntimeError('Model service unavailable.')
    monkeypatch.setattr(llm_assessment.OpenAIJudge, 'review_rewards', unavailable)
    next(b for b in app.button if b.label == 'Summarize rewards with LLM').click().run()
    assert not app.exception
    assert any('Model service unavailable' in error.value for error in app.error)
    with factory() as session:
        assert session.get(AssessmentJob, jid).summary['reward_review']['summary'].startswith('One success')
        assert [a.result['score'] for a in session.scalars(select(AssessedAttempt).where(AssessedAttempt.job_id == jid).order_by(AssessedAttempt.id))] == [1, 0]
