import io
import json
from pathlib import Path
import pytest
from sqlalchemy import select, func
from streamlit.testing.v1 import AppTest
from pipeline.db import sessions
from pipeline.intake import IntakeService
from pipeline.models import ClientTaskRevision
from pipeline.csv_uploads import save_csv_batch
from pipeline.assessment_agent import run_agent, AssessmentJob, AssessedAttempt, AssessmentToolCall
from pipeline.agent_dataset import approve_dataset, export_dataset

class File(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.name = 'task.csv'

def setup(tmp_path, content, extras=None):
    url = f'sqlite:///{tmp_path / "agent.db"}'
    factory = sessions(url)
    with factory.begin() as s:
        tid = IntakeService(s).save(dict(client_name='Lab', task_name='Tasks', required_capabilities='CSV filtering',
            outside_scope='No network', quantities=[dict(category='CSV', minimum_tasks=1, attempts_per_task=2)],
            completion_requirements=extras or []))
        rid = s.scalar(select(ClientTaskRevision.id).where(ClientTaskRevision.task_id == tid))
        bid = save_csv_batch(s, rid, [File(content)], 'test-upload', tmp_path / 'files')
    return url, factory, bid

EXAMPLE = b'attempt_id,check_name,expected_result,actual_result\na,contents,hello,hello\nb,contents,hello,wrong\n'

def test_group_score_reason_and_approved_export(tmp_path):
    url, factory, bid = setup(tmp_path, EXAMPLE)
    with factory.begin() as s:
        jid = run_agent(s, bid)
        assert run_agent(s, bid) == jid
        assert s.get(AssessmentJob, jid).summary['attempts'] == 2
        rows = list(s.scalars(select(AssessedAttempt).order_by(AssessedAttempt.id)))
        assert [r.result['score'] for r in rows] == [1, 0]
        assert rows[1].result['evidence'][0]['source_rows'] == [3]
        assert rows[0].result['task_id'] == rows[1].result['task_id']
        assert rows[0].result['attempt_id'] != rows[1].result['attempt_id']
        with pytest.raises(ValueError, match='Human approval'):
            export_dataset(s, jid)
        with pytest.raises(ValueError):
            approve_dataset(s, jid, '', 'yes')
        approve_dataset(s, jid, 'Test reviewer', 'Reviewed exact comparisons and task notes')
    with sessions(url)() as s:
        data = export_dataset(s, jid, 'jsonl').decode().splitlines()
        assert [json.loads(line)['score'] for line in data] == [1, 0]
        assert all(json.loads(line)['approval']['actor'] == 'Test reviewer' for line in data)
        assert b'reasoning' in export_dataset(s, jid)
        assert s.scalar(select(func.count()).select_from(AssessmentJob)) == 1

@pytest.mark.parametrize('content', [b'arbitrary,columns\n1,2\n', b'expected_result,actual_result\n,\n',
    b'attempt_id,expected_result,actual_result,status\na,hello,hello,unverified\n',
    b'attempt_id,expected_result,actual_result\na,hello,hello\na,hello,hello\n'])
def test_insufficient_evidence_is_unverified(tmp_path, content):
    _, factory, bid = setup(tmp_path, content)
    with factory.begin() as s:
        jid = run_agent(s, bid)
        row = s.scalar(select(AssessedAttempt).where(AssessedAttempt.job_id == jid))
        assert row.result['score'] is None
        assert row.result['reasoning']

def test_custom_section_three_requirements_remain_unverified(tmp_path):
    _, factory, bid = setup(tmp_path, EXAMPLE, [dict(scope='All tasks', requirement='Output must satisfy the policy')])
    with factory.begin() as s:
        jid = run_agent(s, bid)
        rows = list(s.scalars(select(AssessedAttempt).where(AssessedAttempt.job_id == jid).order_by(AssessedAttempt.id)))
        assert [r.result['score'] for r in rows] == [None, 0]
        assert rows[0].result['evidence'][-1]['requirement_id'] == 'COMP-EXTRA'

def test_changed_original_does_not_get_success(tmp_path):
    _, factory, bid = setup(tmp_path, EXAMPLE)
    next((tmp_path / 'files').rglob('*.csv')).write_bytes(b'changed')
    with factory.begin() as s:
        jid = run_agent(s, bid)
        assert s.get(AssessmentJob, jid).summary['file_errors'] == 1
        assert s.scalar(select(AssessedAttempt)).result['score'] is None

def test_agent_ui_and_export_gate(tmp_path, monkeypatch):
    url, factory, bid = setup(tmp_path, EXAMPLE)
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    from pipeline.assessment_agent import LocalEvidenceAgent
    class TestJudge(LocalEvidenceAgent):
        name = 'openai-ui-test'
        def test_connection(self):
            return 'Connected'
    monkeypatch.setattr('pipeline.llm_assessment.OpenAIJudge', lambda **kwargs: TestJudge())
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=30).run()
    app.switch_page('pages/3_Assess_tasks.py').run()
    assert not app.exception
    assert not any(w.label == 'Assessment engine' for w in app.radio)
    assert not any(b.label == 'Load and assess sample data' for b in app.button)
    next(b for b in app.button if b.label == 'Run assessment agent').click().run()
    assert not app.exception
    assert next(m for m in app.metric if m.label == 'Reward 1').value == '1'
    assert next(m for m in app.metric if m.label == 'Reward 0').value == '1'
    next(w for w in app.selectbox if w.label == 'Filter results').set_value('Reward 0').run()
    assert not app.exception
    assert any('1 matching attempts' in c.value for c in app.caption)
    next(w for w in app.selectbox if w.label == 'Filter results').set_value('Needs review').run()
    assert not app.exception
    assert any('No attempts match' in i.value for i in app.info)
    assert next(b for b in app.button if b.label == 'Approve dataset export').disabled
    next(w for w in app.text_input if w.label == 'Reviewer name').set_value('Synthetic reviewer')
    next(w for w in app.text_area if w.label == 'Review rationale').set_value('Reviewed evidence')
    app.checkbox[0].check().run()
    next(b for b in app.button if b.label == 'Approve dataset export').click().run()
    assert not app.exception
    assert any('Export approved' in s.value for s in app.success)
