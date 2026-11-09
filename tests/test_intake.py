import pytest
from pathlib import Path
from pydantic import ValidationError
from sqlalchemy import select, func
from pipeline.db import sessions
from pipeline.intake import IntakeService
from pipeline.rubric import RequirementsInput, COMPLETION_DEFAULTS, SCORING_DEFAULTS
from pipeline.models import Client, ClientTask, ClientTaskRevision, ClientTaskRubric
from streamlit.testing.v1 import AppTest


def sample():
    return {'client_name': 'Example Lab', 'task_name': 'File-processing dataset',
        'required_capabilities': 'Filter CSVs, transform JSON, extract text',
        'outside_scope': 'No browser interaction or external network',
        'quantities': [dict(category='CSV processing', minimum_tasks=10, attempts_per_task=4),
                       dict(category='JSON processing', minimum_tasks=10, attempts_per_task=4),
                       dict(category='Text extraction', minimum_tasks=5, attempts_per_task=4)]}


def test_saved_requirements_reopen_edit_and_reuse_client(tmp_path):
    url = f'sqlite:///{tmp_path / "intake.db"}'
    factory = sessions(url)
    with factory.begin() as s:
        tid = IntakeService(s).save(sample())
    with sessions(url).begin() as s:
        task = s.get(ClientTask, tid)
        cid = task.client_id
        assert tid.startswith('TASK-') and cid.startswith('CL-')
        stored = s.get(ClientTaskRubric, tid).payload
        assert stored['totals'] == dict(minimum_distinct_tasks=25, minimum_attempts=100)
        assert stored['completion_defaults'] == COMPLETION_DEFAULTS
        assert stored['scoring_defaults'] == SCORING_DEFAULTS
        assert stored['completion_requirements'] == []
        changed = {**sample(), 'task_name': 'Updated request', 'required_capabilities': 'Updated capabilities',
            'completion_requirements': [{'scope': 'CSV processing', 'requirement': 'Compare actual contents'}]}
        assert IntakeService(s).save(changed, cid, tid, 1) == tid
    with factory.begin() as s:
        service = IntakeService(s)
        with pytest.raises(ValueError, match='changed since'):
            service.save(sample(), cid, tid, 1)
        other = service.save({**sample(), 'task_name': 'Another request'}, cid)
        assert other != tid
        assert s.scalar(select(func.count()).select_from(Client)) == 1
        history = list(s.scalars(select(ClientTaskRevision).where(ClientTaskRevision.task_id == tid).order_by(ClientTaskRevision.revision)))
        assert [h.revision for h in history] == [1, 2]
        assert history[0].snapshot['required_capabilities'] == sample()['required_capabilities']
        assert history[1].snapshot['required_capabilities'] == 'Updated capabilities'


@pytest.mark.parametrize('change', [
    {'required_capabilities': '   '}, {'outside_scope': ' '}, {'quantities': []},
    {'quantities': [dict(category='CSV', minimum_tasks=0, attempts_per_task=2)]},
    {'quantities': [dict(category='CSV', minimum_tasks=2.5, attempts_per_task=2)]},
    {'quantities': [dict(category='CSV', minimum_tasks=True, attempts_per_task=2)]},
    {'quantities': [dict(category='CSV', minimum_tasks=2, attempts_per_task=1), dict(category=' csv ', minimum_tasks=3, attempts_per_task=2)]},
    {'completion_requirements': [{'scope': 'Unknown category', 'requirement': 'Compare results'}]},
    {'scoring_requirements': [{'scope': 'All tasks', 'requirement': ''}]},
])
def test_invalid_rubric_leaves_no_partial_client(tmp_path, change):
    with sessions(f'sqlite:///{tmp_path / "empty.db"}').begin() as s:
        with pytest.raises(ValidationError):
            IntakeService(s).save({**sample(), **change})
        assert s.scalar(select(func.count()).select_from(Client)) == 0


def test_requirements_form(tmp_path, monkeypatch):
    url = f'sqlite:///{tmp_path / "form.db"}'
    monkeypatch.setenv('DATABASE_URL', url)
    entrypoint = str(Path(__file__).resolve().parents[1] / 'app.py')
    app = AppTest.from_file(entrypoint, default_timeout=20).run()
    assert not app.exception
    next(b for b in app.button if b.label == 'Save requirements').click().run()
    assert app.error
    next(w for w in app.text_input if w.label == 'Client name *').set_value('Example Lab')
    next(w for w in app.text_input if w.label == 'Task name *').set_value('File processing')
    next(w for w in app.text_area if w.label == 'Required capabilities *').set_value('Filter CSV records')
    next(w for w in app.text_area if w.label == 'Activities outside scope *').set_value('No network')
    app.session_state['new_None_quantities'] = {'edited_rows': {0: {'category': 'CSV processing', 'minimum_tasks': 10, 'attempts_per_task': 4}}, 'added_rows': [], 'deleted_rows': []}
    app.run()
    assert next(m for m in app.metric if m.label == 'Minimum distinct tasks').value == '10'
    assert next(m for m in app.metric if m.label == 'Minimum attempts').value == '40'
    next(b for b in app.button if b.label == 'Save requirements').click().run()
    assert not app.exception
    assert not app.error
    assert app.success
    cid = next(t.value for t in app.text_input if t.label == 'Client ID')
    tid = next(t.value for t in app.text_input if t.label == 'Task ID')
    assert cid.startswith('CL-') and tid.startswith('TASK-')
    next(w for w in app.text_area if w.label == 'Required capabilities *').set_value('Updated capabilities')
    next(b for b in app.button if b.label == 'Save changes').click().run()
    assert not app.exception
    reopened = AppTest.from_file(entrypoint, default_timeout=20).run()
    next(w for w in reopened.selectbox if w.label == 'Open a task').select(tid).run()
    assert not reopened.exception
    assert next(w for w in reopened.text_area if w.label == 'Required capabilities *').value == 'Updated capabilities'
    with sessions(url)() as s:
        stored = s.get(ClientTaskRubric, tid).payload
        assert stored['totals']['minimum_attempts'] == 40
        assert stored['scoring_defaults'] == SCORING_DEFAULTS


def test_guide_page(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "guide.db"}')
    path = Path(__file__).resolve().parents[1] / 'app.py'
    app = AppTest.from_file(str(path), default_timeout=20).run()
    app.switch_page('pages/1_Requirements_guide.py').run()
    assert not app.exception
    assert app.title[0].value == 'Client Requirements Form: RL Post-Training Data'
    assert len(app.header) == 6
