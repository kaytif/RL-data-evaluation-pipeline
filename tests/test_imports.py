import csv
import io
import json
from pathlib import Path
import pytest
from sqlalchemy import select, func
from streamlit.testing.v1 import AppTest
from pipeline.db import sessions
from pipeline.intake import IntakeService
from pipeline.models import ClientTaskRevision
from pipeline.upload_models import DataSubmission, ImportedRecord
from pipeline.assessment_spec import assessment_spec, spec_csv, TEMPLATE_FIELDS
from pipeline.imports import parse, assess, save_import, ImportProblem

def snapshot():
    from pipeline.rubric import RequirementsInput
    return {**RequirementsInput(client_name='Lab', task_name='CSV dataset', required_capabilities='Filter CSV',
        outside_scope='No network', quantities=[dict(category='CSV', minimum_tasks=1, attempts_per_task=1)],
        completion_requirements=[dict(scope='CSV', requirement='Compare content')]).snapshot(), 'client_id': 'CL-demo', 'task_id': 'TASK-demo'}

def files(size=1, extension='jsonl'):
    rows = {role: [] for role in TEMPLATE_FIELDS}
    for n in range(size):
        rows['tasks'].append(dict(task_id=f't{n}', task_version='1', category='CSV', instructions='Write correct data',
            expected_result='Correct rows', grader_reference='grader-v1', special_case_behavior='Empty output fails', completion_checks=['contents']))
        rows['attempts'].append(dict(attempt_id=f'a{n}', task_id=f't{n}', task_version='1', model='fake', reward=1,
            trace=[dict(action='write', observation='saved')]))
        rows['checks'].append(dict(attempt_id=f'a{n}', check_name='contents', expected_result='correct rows',
            actual_result='correct rows', status='pass', unverified_reason=''))
    result = []
    for role, values in rows.items():
        if extension == 'jsonl':
            data = '\n'.join(json.dumps(v) for v in values).encode()
        elif extension == 'json':
            data = json.dumps(values).encode()
        else:
            out = io.StringIO(newline=''); writer = csv.DictWriter(out, TEMPLATE_FIELDS[role]); writer.writeheader()
            for v in values:
                writer.writerow({k: json.dumps(x) if isinstance(x, list) else x for k, x in v.items()})
            data = out.getvalue().encode()
        result.append((role, f'{role}.{extension}', data))
    return result

@pytest.mark.parametrize('extension', ['csv', 'json', 'jsonl'])
def test_parse_and_assess_supported_formats(extension):
    result = assess(snapshot(), parse(files(extension=extension)))
    assert result['failure_count'] == 0
    assert result['status'] == 'needs_review'
    assert any(r['requirement_id'] == 'COMP-EXTRA-001' and r['status'] == 'needs_review' for r in result['results'])

def test_large_submission_and_saved_history(tmp_path):
    factory = sessions(f'sqlite:///{tmp_path / "imports.db"}')
    values = snapshot()
    keys = ['client_name', 'task_name', 'required_capabilities', 'outside_scope', 'quantities', 'completion_requirements']
    with factory.begin() as session:
        tid = IntakeService(session).save({k: values[k] for k in keys})
        revision = session.scalar(select(ClientTaskRevision).where(ClientTaskRevision.task_id == tid))
        uploads = files(size=5000)
        sid = save_import(session, revision.id, uploads, tmp_path / 'artifacts')
        assert save_import(session, revision.id, uploads, tmp_path / 'artifacts') == sid
    with factory() as session:
        stored = session.get(DataSubmission, sid)
        assert stored.report['counts'] == dict(tasks=5000, attempts=5000, checks=5000)
        assert session.scalar(select(func.count()).select_from(ImportedRecord)) == 15000
        assert session.scalar(select(func.count()).select_from(DataSubmission)) == 1
        assert all(Path(f['path']).exists() for f in stored.files)
        assert stored.snapshot['task_id'] == tid

def test_failures_have_source_evidence_and_requirement_ids():
    records = parse(files())
    records['attempts'][0]['payload']['reward'] = 1
    records['checks'][0]['payload']['status'] = 'fail'
    records['tasks'].append(records['tasks'][0])
    result = assess(snapshot(), records)
    assert result['status'] == 'failed'
    codes = {r['requirement_id'] for r in result['results'] if r['status'] == 'failed'}
    assert {'ID-001', 'SCORE-001'} <= codes
    evidence = next(r for r in result['results'] if r['requirement_id'] == 'SCORE-001')
    assert evidence['examples'][0]['file'] == 'attempts.jsonl'
    assert evidence['examples'][0]['record'] == 1

def test_unverified_and_fail_precedence():
    records = parse(files())
    check = records['checks'][0]['payload']
    check.update(status='unverified', unverified_reason='Grader unavailable')
    records['attempts'][0]['payload']['reward'] = 'unverified'
    assert assess(snapshot(), records)['failure_count'] == 0
    records['attempts'][0]['payload']['reward'] = 1
    assert any(r['requirement_id'] == 'SCORE-001' and r['status'] == 'failed' for r in assess(snapshot(), records)['results'])
    check.update(status='fail', unverified_reason='')
    records['tasks'][0]['payload']['completion_checks'].append('missing')
    records['attempts'][0]['payload']['reward'] = 0
    result = assess(snapshot(), records)
    assert any(r['requirement_id'] == 'SCORE-002' and r['status'] == 'failed' for r in result['results'])
    assert not any(r['requirement_id'] == 'SCORE-001' and r['status'] == 'failed' for r in result['results'])

@pytest.mark.parametrize('uploads', [[], [('tasks', 'bad.json', b'{"not":"an array"}')],
    [('tasks', 'bad.csv', b'task_id,task_id\nx,x')], [('tasks', 'bad.jsonl', b'{invalid')],
    [('tasks', 'empty.json', b'[]')], [('tasks', 'code.py', b'print(1)')]])
def test_reject_bad_files(uploads):
    with pytest.raises(ImportProblem):
        parse(uploads)

def test_machine_spec_exports():
    spec = assessment_spec(snapshot())
    rows = list(csv.DictReader(io.StringIO(spec_csv(spec).decode('utf-8-sig'))))
    assert any(r['requirement_id'] == 'QTY-001' and r['minimum_tasks'] == '1' for r in rows)
    assert any(r['requirement_id'] == 'COMP-EXTRA-001' and r['assessment'] == 'human_review' for r in rows)

def test_upload_page_without_saved_requirements(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "ui.db"}')
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=20).run()
    app.switch_page('pages/2_Upload_submission.py').run()
    assert not app.exception
    assert 'Save client requirements first' in app.info[0].value

def test_size_limit_and_long_trace(monkeypatch):
    data = files()
    records = parse(data)
    records['attempts'][0]['payload']['trace'][0]['observation'] = 'x' * 200000
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, TEMPLATE_FIELDS['attempts']); writer.writeheader()
    p = records['attempts'][0]['payload']
    writer.writerow({**p, 'trace': json.dumps(p['trace'])})
    modified = [f for f in data if f[0] != 'attempts'] + [('attempts', 'long.csv', out.getvalue().encode())]
    assert len(parse(modified)['attempts']) == 1
    monkeypatch.setattr('pipeline.imports.MAX_BYTES', 10)
    with pytest.raises(ImportProblem, match='50 MiB'):
        parse(data)
