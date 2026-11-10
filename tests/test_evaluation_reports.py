import copy
import csv
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from sqlalchemy import select, func
from pipeline.assessment_agent import run_agent, AssessmentJob, AssessedAttempt, AttemptAssessment, AgentFileOutput
from pipeline.agent_dataset import approve_dataset, export_dataset
from pipeline.calibration import starter_cases, run_calibration, validate_cases
from pipeline.evaluation_reports import (check, compare_reports, make_report, quantity_checks,
    quality_checks, run_readiness, report_markdown, issues_csv)
from pipeline.evaluation_types import Check, CheckSet, validate_checks, reward_from_checks, requirements
from pipeline.llm_assessment import OpenAIJudge, Extraction, ExtractedAttempt, strict_schema
from test_assessment_agent import setup


DATA = b'instructions,actual_answer,expected_answer,attempt_id\nSay hello,hello,hello,a\nSay hello,wrong,hello,b\n'


class MockJudge(OpenAIJudge):
    def __init__(self, root):
        super().__init__('test-key', cache_root=root)
        self.payloads = []

    def test_connection(self):
        return 'Connected to mock evaluator.'

    def call(self, instructions, payload, output_type):
        self.payloads.append(copy.deepcopy(payload))
        name = output_type.__name__
        if name == 'InstructionReview':
            return output_type.model_validate(dict(summary='Requirements reviewed without edits.', concerns=[]))
        if name == 'Extraction':
            attempts, by_task = [], {}
            for entry in payload['rows']:
                row, n = entry['data'], entry['source_row']
                task = row.get('instructions', row.get('task'))
                attempts.append(dict(task=task, candidate=row.get('actual_answer', row.get('candidate_response')), criteria=['Answer matches the task.'],
                    reference_answer=row['expected_answer'], source_rows=[n], source_attempt_id=row.get('attempt_id'),
                    source_task_id=None, category=payload['permitted_categories'][0] if payload['permitted_categories'] else None))
                by_task.setdefault(task, []).append(n)
            packages = []
            for task, rows in by_task.items():
                packages.append(dict(task=task, category=payload['permitted_categories'][0] if payload['permitted_categories'] else None, source_rows=rows,
                    checks=[dict(requirement_id=r['requirement_id'], applicable=True,
                        status='unverified' if r['requirement_id'] == 'COMP-002' else 'pass',
                        expected=r['rule'], actual='Task and reference are present; no grader execution supplied.',
                        reason='Recorded package evidence inspected.', suggested_fix='Supply reproducible grader evidence.' if r['requirement_id'] == 'COMP-002' else '')
                        for r in payload['task_package_requirements']]))
            return output_type.model_validate(dict(attempts=attempts, packages=packages, notes=[]))
        if name == 'CheckSet':
            item = payload['attempt']
            status = 'pass' if item['candidate'] == item['reference_answer'] else 'fail'
            return output_type.model_validate(dict(checks=[dict(requirement_id=r['requirement_id'], applicable=True,
                status=status, expected=item['reference_answer'] or 'Required task result', actual=item['candidate'],
                reason='Compared response with the supplied reference.', suggested_fix='' if status == 'pass' else 'Review the incorrect response; obtain a separate corrected execution.')
                for r in payload['requirements']]))
        raise AssertionError(name)


def test_readiness_scoring_reports_and_exports_preserve_originals(tmp_path):
    _, factory, bid = setup(tmp_path, DATA)
    judge = MockJudge(tmp_path / 'cache')
    with factory.begin() as session:
        jid = run_agent(session, bid, judge)
        job = session.get(AssessmentJob, jid)
        assert job.summary['attempts'] == 2
        assert job.summary['success'] == job.summary['failure'] == 1
        report = job.summary['report']
        assert report['rewards']['success_rate_verified'] == .5
        assert report['overall'] == 'unverified'  # Missing grader proof, not the valid reward-0 example.
        assert not any(c['status'] == 'fail' and c['scope'] == 'dataset' for c in report['checks'])
        assert any(c['requirement_id'] == 'ATTEMPTS-PER-TASK' and c['status'] == 'pass' for c in report['checks'])
        assert any(c['scope'] == 'attempt' and c['status'] == 'fail' for c in report['checks'])
        assert report['calibration'] is None
        assert run_agent(session, bid, judge) == jid
        assert session.scalar(select(func.count()).select_from(AssessmentJob)) == 2
        saved = list(session.scalars(select(AssessedAttempt).where(AssessedAttempt.job_id == jid)))
        assert [s.result['score'] for s in saved] == [1, 0]
        assert all(s.result['category'] == 'CSV' for s in saved)
        assert all(s.result['execution_id'] in ('a', 'b') for s in saved)
        assert any('completion_defaults' in p.get('client_requirements', {}) for p in judge.payloads)
        approve_dataset(session, jid, 'Reviewer', 'Reviewed package gaps and rewards.')
        csv_data = export_dataset(session, jid)
        assert b'check_fixes' in csv_data
        assert 'suggested_fix' in next(csv.reader(io.StringIO(issues_csv(report['issues']).decode('utf-8-sig'))))
        assert 'Dataset compliance' in report_markdown(report)
    assert next((tmp_path / 'files').rglob('*.csv')).read_bytes() == DATA


def test_per_task_threshold_not_hidden_by_total_attempts():
    blueprint = dict(quantities=[dict(category='CSV', minimum_tasks=2, attempts_per_task=2)])
    inventory = [dict(task_key=key, category='CSV', execution_id=str(i), source_file=key+'.csv',
                      source_rows=[i+2], candidate_hash='same-answer')
                 for i, key in enumerate(['task1', 'task2', 'task2', 'task2'])]
    results = quantity_checks(blueprint, inventory)
    assert next(c for c in results if c['task'] == 'task1' and c['requirement_id'] == 'ATTEMPTS-PER-TASK')['status'] == 'fail'
    assert next(c for c in results if c['requirement_id'] == 'QTY-001')['status'] == 'pass'


def test_inferred_attempts_cannot_establish_separate_execution_minimum():
    blueprint = dict(quantities=[dict(category='CSV', minimum_tasks=1, attempts_per_task=2)])
    inventory = [dict(task_key='t', category='CSV', execution_id=None, source_file='t.csv',
                      source_rows=[n], candidate_hash='identical') for n in (2, 3)]
    results = quantity_checks(blueprint, inventory)
    assert next(c for c in results if c['requirement_id'] == 'ATTEMPTS-PER-TASK')['status'] == 'unverified'


def test_incomplete_inventory_does_not_invent_a_threshold_failure():
    blueprint = dict(quantities=[dict(category='CSV', minimum_tasks=2, attempts_per_task=2)])
    results = quantity_checks(blueprint, [], complete=False)
    assert next(c for c in results if c['requirement_id'] == 'QTY-001')['status'] == 'unverified'


def test_unchanged_answers_with_different_execution_ids_stay_separate(tmp_path):
    raw = DATA.replace(b'wrong', b'hello')
    _, factory, bid = setup(tmp_path, raw)
    with factory.begin() as session:
        jid = run_agent(session, bid, MockJudge(tmp_path / 'cache'))
        assert session.get(AssessmentJob, jid).summary['attempts'] == 2
        assert session.get(AssessmentJob, jid).summary['success'] == 2


def test_quality_points_to_source_rows_without_modification(tmp_path):
    raw = b'definition,answer,state_json\nA temperature instrument.,,"broken"\nA temperature instrument.,,"broken"\n'
    path = tmp_path / 'input.csv'; path.write_bytes(raw)
    file = SimpleNamespace(path=str(path), filename='input.csv', sha256=hashlib.sha256(raw).hexdigest(), byte_count=len(raw))
    checks, summary = quality_checks([file])
    assert summary['data_rows'] == 2 and summary['exact_duplicate_rows'] == 1
    assert next(c for c in checks if c['requirement_id'] == 'MISSING-DATA')['source_rows'] == [2, 3]
    assert next(c for c in checks if c['requirement_id'] == 'JSON-VALID')['status'] == 'fail'
    assert path.read_bytes() == raw


def test_reassessment_resolved_removed_and_new_are_distinct():
    blueprint = {'task_id': 'same-requirements'}
    old = [check('EMPTY', 'Data quality', 'fail', 'value', '', 'Missing', filename='a.csv', rows=[2]),
           check('EMPTY', 'Data quality', 'fail', 'value', '', 'Missing', filename='removed.csv', rows=[2])]
    now = [check('EMPTY', 'Data quality', 'pass', 'value', 'fixed', 'Present', filename='a.csv'),
           check('EMPTY', 'Data quality', 'fail', 'value', '', 'Missing', filename='new.csv', rows=[2])]
    changes = compare_reports(make_report('final', now, requirements=blueprint), make_report('final', old, requirements=blueprint))
    assert {c['source_file']: c['reassessment_status'] for c in changes} == {
        'a.csv': 'Resolved', 'removed.csv': 'Not reassessed', 'new.csv': 'New'}
    with pytest.raises(ValueError, match='same requirements'):
        compare_reports({'requirements': {}}, {'requirements': {'changed': True}})


def test_check_aggregation_and_missing_check_protection():
    def item(id, status):
        return Check(requirement_id=id, applicable=True, status=status, expected='required', actual='evidence', reason='Observed.', suggested_fix='Review.')
    assert reward_from_checks([item('a', 'pass'), item('b', 'unverified')]) is None
    assert reward_from_checks([item('a', 'fail'), item('b', 'unverified')]) == 0
    assert reward_from_checks([item('a', 'pass'), item('b', 'pass')]) == 1
    with pytest.raises(ValueError, match='exactly one'):
        validate_checks(CheckSet(checks=[item('a', 'pass')]), [dict(requirement_id='a'), dict(requirement_id='b')])


def test_unknown_category_scoped_rules_force_unverified():
    snapshot = dict(completion_requirements=[dict(scope='CSV', requirement='Compare contents.')])
    reqs = requirements(snapshot, criteria=['Correct output'], category=None)
    result = CheckSet(checks=[dict(requirement_id=r['requirement_id'], applicable=True, status='pass', expected='yes', actual='yes', reason='Model says pass.', suggested_fix='') for r in reqs])
    validate_checks(result, reqs)
    assert reward_from_checks(result.checks) is None


def test_structural_readiness_needs_no_api_and_is_saved(tmp_path):
    _, factory, bid = setup(tmp_path, DATA)
    with factory.begin() as session:
        id = run_readiness(session, bid)
        report = session.get(AssessmentJob, id).summary['report']
        assert report['llm_reviewed'] is False and report['overall'] == 'unverified'
        assert run_readiness(session, bid) == id


def test_calibration_requires_human_confirmation_and_blinds_labels(tmp_path):
    _, factory, bid = setup(tmp_path, DATA)
    cases = starter_cases()[:2]
    seen = []
    class CalibrationJudge:
        name = 'openai-calibration-test'
        def assess_file(self, task, blueprint):
            content = Path(task.path).read_text()
            assert 'expected_reward' not in content and 'reviewer_reason' not in content
            seen.append(content)
            # Intentionally fail the negative reference to exercise false-pass reporting.
            return AgentFileOutput(attempts=[AttemptAssessment(task_id=task.id, attempt_id=task.id,
                source_attempt_id='case', source_file=task.filename, score=1, status='success',
                reasoning='Mock evaluator pass.', evidence=[])], mapping={}, task_review_notes=[])
    with factory.begin() as session:
        with pytest.raises(ValueError, match='confirm'):
            run_calibration(session, bid, CalibrationJudge(), cases, 'Reviewer', confirmed=False)
        id = run_calibration(session, bid, CalibrationJudge(), cases, 'Reviewer', confirmed=True)
        report = session.get(AssessmentJob, id).summary['report']
        assert report['agreement'] == .5 and report['false_passes'] == 1
        assert report['false_failures'] == 0
        assert len(seen) == 2
        assert run_calibration(session, bid, CalibrationJudge(), cases, 'Reviewer', confirmed=True) == id
        assert len(seen) == 2


def test_calibration_set_needs_both_binary_classes():
    with pytest.raises(ValueError, match='reward-1'):
        validate_cases([starter_cases()[0], {**starter_cases()[0], 'id': 'other'}])


def test_new_calibration_creates_new_unapproved_report_without_editing_approved_job(tmp_path):
    _, factory, bid = setup(tmp_path, DATA)
    judge = MockJudge(tmp_path / 'cache')
    with factory.begin() as session:
        first = run_agent(session, bid, judge)
        approve_dataset(session, first, 'Reviewer', 'Original reviewed result.')
        calibration = run_calibration(session, bid, judge, starter_cases()[:2], 'Reviewer', confirmed=True)
        second = run_agent(session, bid, judge)
        assert first != second
        assert session.get(AssessmentJob, first).approval['actor'] == 'Reviewer'
        assert session.get(AssessmentJob, second).approval is None
        assert session.get(AssessmentJob, second).summary['report']['calibration']['agreement'] == 1
        assert session.get(AssessmentJob, calibration).summary['report']['reference_cases'][0]['id'] == 'correct'


def test_corrected_upload_resolves_matching_candidate_issue_and_preserves_original(tmp_path):
    from pipeline.csv_uploads import TaskUploadBatch, save_csv_batch
    from test_assessment_agent import File
    _, factory, bid = setup(tmp_path, DATA)
    judge = MockJudge(tmp_path / 'cache')
    with factory.begin() as session:
        first = run_agent(session, bid, judge)
        revision = session.get(TaskUploadBatch, bid).requirement_revision_id
        corrected = save_csv_batch(session, revision, [File(DATA.replace(b'wrong', b'hello'))], 'corrected-upload', tmp_path / 'corrected')
        second = run_agent(session, corrected, judge)
        changes = compare_reports(session.get(AssessmentJob, second).summary['report'], session.get(AssessmentJob, first).summary['report'])
        assert any(c['phase'] == 'Candidate rewards' and c['attempt'] == 'b' and c['reassessment_status'] == 'Resolved' for c in changes)
        original = list(session.scalars(select(AssessedAttempt).where(AssessedAttempt.job_id == first)))
        assert [a.result['score'] for a in original] == [1, 0]


def test_new_panels_and_keyless_quality_check_render(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    url, factory, bid = setup(tmp_path, DATA)
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=30).run()
    app.switch_page('pages/3_Assess_tasks.py').run()
    assert not app.exception
    assert next(b for b in app.button if b.label == 'Run assessment agent').disabled
    next(b for b in app.button if b.label == 'Inspect data quality').click().run()
    assert not app.exception
    assert any(b.proto.label == 'Download readiness report JSON' for b in app.get('download_button'))
    assert next(b for b in app.button if b.label == 'Evaluate grader on reference cases').disabled
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AssessmentJob)) == 1


def test_llm_ui_shows_reports_and_retains_export_approval(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    url, _, bid = setup(tmp_path, DATA)
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    judge = MockJudge(tmp_path / 'cache')
    monkeypatch.setattr('pipeline.llm_assessment.OpenAIJudge', lambda **kwargs: judge)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=30).run()
    app.switch_page('pages/3_Assess_tasks.py').run()
    next(b for b in app.button if b.label == 'Run assessment agent').click().run()
    assert not app.exception
    assert not app.tabs
    assert [h.value for h in app.header] == ['Data quality', 'Task coverage']
    assert any(s.value == 'Task workspace' for s in app.subheader)
    assert any('Response' in frame.value.columns for frame in app.dataframe)
    assert not any(s.value.startswith('Attempt ') for s in app.subheader)
    assert next(m for m in app.metric if m.label == 'Reward 1').value == '1'
    assert next(m for m in app.metric if m.label == 'Reward 0').value == '1'
    assert next(b for b in app.button if b.label == 'Approve dataset export').disabled
    assert not any(b.proto.label == 'Download final report JSON' for b in app.get('download_button'))
    next(w for w in app.text_input if w.label == 'Reviewer name').set_value('Reviewer')
    next(w for w in app.text_area if w.label == 'Review rationale').set_value('Reviewed the candidate rewards and unresolved package checks.')
    next(w for w in app.checkbox if w.label.startswith('I reviewed the task grouping')).check().run()
    next(b for b in app.button if b.label == 'Approve dataset export').click().run()
    assert not app.exception
    labels = {b.proto.label for b in app.get('download_button')}
    assert {'Download final report JSON', 'Download final report Markdown', 'Download issues & fixes CSV', 'Download results CSV'} <= labels


def test_task_table_shows_all_tasks_and_filters_results(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    data = DATA + b'Say hi,hi,hi,c\nSay hi,hi,hi,d\n'
    url, factory, bid = setup(tmp_path, data)
    with factory.begin() as session:
        run_agent(session, bid, MockJudge(tmp_path / 'cache'))
    monkeypatch.setenv('DATABASE_URL', url)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=30).run()
    app.switch_page('pages/3_Assess_tasks.py').run()
    assert not app.exception
    table = next(f.value for f in app.dataframe if 'Response' in f.value.columns)
    assert len(table) == 4
    assert set(table['Task']) == {'Say hello', 'Say hi'}
    assert 'Compare a corrected upload' not in {e.label for e in app.expander}
    assert any('JSON reference examples' in e.label for e in app.expander)
    next(w for w in app.selectbox if w.label == 'Filter results').select('Incorrect').run()
    assert not app.exception
    table = next(f.value for f in app.dataframe if 'Response' in f.value.columns)
    assert len(table) == 1 and table.iloc[0]['Response'] == 'wrong'
    next(w for w in app.selectbox if w.label == 'Filter results').select('Unverified').run()
    assert not app.exception
    assert any('No attempts match' in i.value for i in app.info)


def test_task_table_keeps_all_executions(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    data = b'instructions,actual_answer,expected_answer,attempt_id\n' + b''.join(
        f'Say hello,hello,hello,execution-{i}\n'.encode() for i in range(22))
    url, factory, bid = setup(tmp_path, data)
    with factory.begin() as session:
        run_agent(session, bid, MockJudge(tmp_path / 'cache'))
    monkeypatch.setenv('DATABASE_URL', url)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=30).run()
    app.switch_page('pages/3_Assess_tasks.py').run()
    assert not app.exception
    table = next(f.value for f in app.dataframe if 'Response' in f.value.columns)
    assert len(table) == 22


def test_file_coverage_never_combines_tasks_or_attempts():
    from pipeline.evaluation_reports import file_quantity_checks
    from pipeline.evaluation_ui import coverage_rows
    rubric = {'quantities': [{'category': 'Words', 'minimum_tasks': 2, 'attempts_per_task': 2}]}
    inventory = [dict(task_key='same', category='Words', execution_id=f'run-{i}',
        source_file=f'{i}.csv', source_file_id=f'file-{i}', source_rows=[2], candidate_hash='a') for i in (1, 2)]
    assert next(c for c in quantity_checks(rubric, inventory) if c['requirement_id'] == 'ATTEMPTS-PER-TASK')['status'] == 'pass'
    for i in (1, 2):
        checks = file_quantity_checks(rubric, inventory, f'{i}.csv', f'file-{i}')
        assert next(c for c in checks if c['requirement_id'] == 'ATTEMPTS-PER-TASK')['status'] == 'fail'
        assert next(c for c in checks if c['requirement_id'] == 'QTY-001')['status'] == 'fail'
    rows = coverage_rows(rubric, inventory[:1], True)
    assert rows[0]['Coverage'] == '50.0%'
    assert rows[0]['Tasks meeting attempt minimum'] == 0
    # Even identical filenames cannot combine modern inventories with separate IDs.
    for record in inventory:
        record['source_file'] = 'same.csv'
    checks = file_quantity_checks(rubric, inventory, 'same.csv', 'file-1', ambiguous_filename=True)
    assert next(c for c in checks if c['requirement_id'] == 'ATTEMPTS-PER-TASK')['status'] == 'fail'
    checks = file_quantity_checks(rubric, [], 'same.csv', 'file-3', complete=False, ambiguous_filename=True)
    assert next(c for c in checks if c['requirement_id'] == 'QTY-001')['status'] == 'unverified'


def test_explicit_vocabulary_file_counts_50_definitions_and_50_opposites(tmp_path):
    from pipeline.evaluation_reports import explicit_file_inventory
    from pipeline.evaluation_ui import coverage_rows
    raw = b'task_id,task_type,prompt,answer\n' + b''.join(
        f'{category}-{i},{category},Question {i},Response\n'.encode()
        for category in ('definitions', 'opposites') for i in range(50))
    path = tmp_path / 'vocabulary.csv'
    path.write_bytes(raw)
    file = SimpleNamespace(id='selected', filename=path.name, path=str(path), sha256=hashlib.sha256(raw).hexdigest())
    rubric = {'quantities': [dict(category=c, minimum_tasks=50, attempts_per_task=1)
                             for c in ('definitions', 'opposites')]}
    inventory, complete = explicit_file_inventory(file, rubric)
    rows = coverage_rows(rubric, inventory, complete)
    assert complete
    assert [r['Distinct tasks'] for r in rows] == [50, 50]
    assert [r['Coverage'] for r in rows] == ['100.0%', '100.0%']
    assert all(r['Tasks meeting attempt minimum'] == 0 for r in rows)
    # The user's actual rubric is an ideal target, not the file's grouping.
    target = {'quantities': [dict(category=c, minimum_tasks=1, attempts_per_task=50)
                             for c in ('Definition', 'Opposites')]}
    actual, complete = explicit_file_inventory(file, target)
    assert actual == inventory
    assert complete
    rows = coverage_rows(target, actual, complete)
    assert [r['Distinct tasks'] for r in rows] == [50, 50]
    assert [r['Required tasks'] for r in rows] == [1, 1]
    assert [r['Observed attempts per task'] for r in rows] == ['1', '1']
    assert [r['Required attempts per task'] for r in rows] == [50, 50]
    checks = quantity_checks(target, actual)
    assert all(c['status'] == 'fail' for c in checks if c['requirement_id'] == 'ATTEMPTS-PER-TASK')
    assert {r['category'] for r in actual} == {'definitions', 'opposites'}


def test_categories_outside_rubric_remain_visible_and_do_not_satisfy_its_targets(tmp_path):
    from pipeline.evaluation_reports import explicit_file_inventory
    from pipeline.evaluation_ui import coverage_rows
    raw = b'task_id,task_type,prompt,answer\nT1,translation,Translate hello,hola\n'
    path = tmp_path / 'translation.csv'
    path.write_bytes(raw)
    file = SimpleNamespace(id='selected', filename=path.name, path=str(path), sha256=hashlib.sha256(raw).hexdigest())
    target = {'quantities': [dict(category='definitions', minimum_tasks=50, attempts_per_task=1)]}
    inventory, complete = explicit_file_inventory(file, target)
    assert complete and inventory[0]['category'] == 'translation'
    rows = coverage_rows(target, inventory, complete)
    assert rows[0]['Distinct tasks'] == 0
    assert rows[1]['Category'] == 'translation' and rows[1]['Distinct tasks'] == 1
    assert rows[1]['Coverage'] == 'No rubric target'
    checks = quantity_checks(target, inventory)
    assert next(c for c in checks if c['requirement_id'] == 'QTY-001')['status'] == 'fail'
    assert next(c for c in checks if c['requirement_id'] == 'CAT-001')['status'] == 'fail'


def test_selected_file_ui_separates_actual_counts_from_ideal_rubric(tmp_path, monkeypatch):
    from pipeline.csv_uploads import TaskUploadBatch, UploadedTaskCSV
    from pipeline.models import ClientTaskRevision
    from streamlit.testing.v1 import AppTest
    raw = b'task_id,task_type,prompt,answer\n' + b''.join(
        f'{c}-{i},{c},Question {i},Response\n'.encode()
        for c in ('definitions', 'opposites') for i in range(50))
    url, factory, bid = setup(tmp_path, raw)
    with factory.begin() as session:
        revision = session.get(ClientTaskRevision, session.get(TaskUploadBatch, bid).requirement_revision_id)
        revision.snapshot = {**revision.snapshot, 'quantities': [
            dict(category=c, minimum_tasks=1, attempts_per_task=50) for c in ('Definition', 'Opposites')]}
    monkeypatch.setenv('DATABASE_URL', url)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=30).run()
    app.switch_page('pages/3_Assess_tasks.py').run()
    next(b for b in app.button if b.label == 'Inspect data quality').click().run()
    assert not app.exception
    table = next(f.value for f in app.dataframe if 'Distinct tasks' in f.value.columns)
    assert list(table['Distinct tasks']) == [50, 50]
    assert list(table['Required tasks']) == [1, 1]
    assert list(table['Observed attempts per task']) == ['1', '1']
    assert list(table['Required attempts per task']) == [50, 50]
    assert next(m for m in app.metric if m.label == 'Task coverage').value == '100.0%'
    assert next(m for m in app.metric if m.label == 'Tasks meeting attempt minimum').value == '0.0%'
    class IncompleteReviewer:
        name = 'openai-incomplete-review'
        def review_instructions(self, blueprint):
            return SimpleNamespace(model_dump=lambda: dict(summary='Reviewed', concerns=[]))
        def inspect_file(self, file, blueprint):
            raise ValueError('Semantic package review could not complete.')
    with factory.begin() as session:
        file_id = session.scalar(select(UploadedTaskCSV.id).where(UploadedTaskCSV.batch_id == bid))
        jid = run_readiness(session, bid, judge=IncompleteReviewer(), file_id=file_id)
        report = session.get(AssessmentJob, jid).summary['report']
        assert len(report['inventory']) == 100
        assert report['inventoried_file_ids'] == [file_id]
        assert any(c['requirement_id'] == 'EXTRACTION' and c['status'] == 'unverified' for c in report['checks'])


def test_structural_coverage_counts_only_selected_file_and_rejects_batch_fallback(tmp_path, monkeypatch):
    from pipeline.csv_uploads import TaskUploadBatch, UploadedTaskCSV, save_csv_batch
    from test_assessment_agent import File
    from streamlit.testing.v1 import AppTest
    url, factory, original = setup(tmp_path, DATA)
    # Same filename and task ID across files must still remain separate.
    one = File(b'task_id,task_type,prompt,answer\nsame,CSV,Say hello,hello\n')
    two = File(b'task_id,task_type,prompt,answer\nsame,CSV,Say hi,hi\nsecond,CSV,Say bye,bye\n')
    with factory.begin() as session:
        rid = session.get(TaskUploadBatch, original).requirement_revision_id
        bid = save_csv_batch(session, rid, [one, two], 'explicit-files', tmp_path / 'explicit')
        files = list(session.scalars(select(UploadedTaskCSV).where(UploadedTaskCSV.batch_id == bid).order_by(UploadedTaskCSV.id)))
        for file in files:
            jid = run_readiness(session, bid, file_id=file.id)
            report = session.get(AssessmentJob, jid).summary['report']
            assert {r['source_file_id'] for r in report['inventory']} == {file.id}
            assert len(report['inventory']) == file.row_count - 1
            assert report['inventoried_file_ids'] == [file.id]
            assert all(c['status'] != 'pass' for c in report['checks']
                       if c['requirement_id'] == 'ATTEMPTS-PER-TASK')
        # A legacy upload-wide semantic report cannot override file-local counts.
        legacy_id = run_readiness(session, bid)
        legacy = session.get(AssessmentJob, legacy_id)
        legacy.summary = {**legacy.summary, 'report': {**legacy.summary['report'], 'llm_reviewed': True}}
    monkeypatch.setenv('DATABASE_URL', url)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=30).run()
    app.switch_page('pages/3_Assess_tasks.py').run()
    next(w for w in app.selectbox if w.label == 'Dataset upload').select(bid).run()
    for file in files:
        next(w for w in app.selectbox if w.label == 'Assessment file').select(file.id).run()
        assert not app.exception
        table = next(f.value for f in app.dataframe if 'Distinct tasks' in f.value.columns)
        assert table.iloc[0]['Distinct tasks'] == file.row_count - 1
    # Without a file report, never substitute the legacy upload-wide report.
    with factory.begin() as session:
        reports = list(session.scalars(select(AssessmentJob).where(AssessmentJob.batch_id == bid)))
        for report in reports:
            if report.summary.get('source_file_id') == files[-1].id:
                session.delete(report)
    app.run()
    assert next(m for m in app.metric if m.label == 'Task coverage').value == 'Unverified'
    table = next(f.value for f in app.dataframe if 'Distinct tasks' in f.value.columns)
    assert table.iloc[0]['Distinct tasks'] == 0


def test_assessment_runs_only_one_selected_file_and_ui_never_combines_rewards(tmp_path, monkeypatch):
    from pipeline.csv_uploads import TaskUploadBatch, UploadedTaskCSV, save_csv_batch
    from test_assessment_agent import File
    from streamlit.testing.v1 import AppTest
    url, factory, original_bid = setup(tmp_path, DATA)
    one = File(b'instructions,actual_answer,expected_answer,attempt_id\nSay hello,hello,hello,a\n'); one.name = 'correct.csv'
    two = File(b'instructions,actual_answer,expected_answer,attempt_id\nSay hello,wrong,hello,b\n'); two.name = 'incorrect.csv'
    with factory.begin() as session:
        revision = session.get(TaskUploadBatch, original_bid).requirement_revision_id
        bid = save_csv_batch(session, revision, [one, two], 'two-files', tmp_path / 'two')
        files = list(session.scalars(select(UploadedTaskCSV).where(UploadedTaskCSV.batch_id == bid).order_by(UploadedTaskCSV.filename)))
        judge = MockJudge(tmp_path / 'cache')
        with pytest.raises(ValueError, match='Choose one file'):
            run_agent(session, bid, judge)
        first = run_agent(session, bid, judge, file_id=files[0].id)
        assert session.get(AssessmentJob, first).summary['attempts'] == 1
        assert session.get(AssessmentJob, first).summary['success'] == 1
        assert session.get(AssessmentJob, first).summary['failure'] == 0
        assert session.get(AssessmentJob, first).summary['report']['quality']['data_rows'] == 1
        second = run_agent(session, bid, judge, file_id=files[1].id)
        assert session.get(AssessmentJob, second).summary['failure'] == 1
        assert first != second
        assert all(a.task_id == files[0].id for a in session.scalars(select(AssessedAttempt).where(AssessedAttempt.job_id == first)))
        with pytest.raises(ValueError, match='does not belong'):
            run_agent(session, bid, judge, file_id='wrong-file')
    monkeypatch.setenv('DATABASE_URL', url)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=30).run()
    app.switch_page('pages/3_Assess_tasks.py').run()
    next(w for w in app.selectbox if w.label == 'Dataset upload').select(bid).run()
    assert not app.exception
    assert next(m for m in app.metric if m.label == 'Attempts').value == '1'
    assert next(m for m in app.metric if m.label == 'Reward 1').value == '1'
    table = next(f.value for f in app.dataframe if 'Response' in f.value.columns)
    assert len(table) == 1 and table.iloc[0]['File'] == 'correct.csv'
    next(w for w in app.selectbox if w.label == 'Assessment file').select(files[1].id).run()
    assert not app.exception
    assert next(m for m in app.metric if m.label == 'Reward 1').value == '0'
    assert next(m for m in app.metric if m.label == 'Reward 0').value == '1'
    table = next(f.value for f in app.dataframe if 'Response' in f.value.columns)
    assert len(table) == 1 and table.iloc[0]['File'] == 'incorrect.csv'
