from pipeline.assessment_summary import finding_text, summary_rows, section_for
from pipeline.evaluation_reports import check


def test_only_two_sections_and_candidate_failure_is_not_quality_failure():
    phases = ['Instructions', 'Data quality', 'Task coverage', 'Attempt coverage', 'Task completion', 'Reward evidence']
    assert {section_for(check('x', p, 'unverified', '', '', '', '')) for p in phases} == {'Data quality', 'Task coverage'}
    assert section_for(check('CONTENT-001', 'Candidate rewards', 'fail', 'term', 'wrong', 'Incorrect answer.')) is None


def test_missing_field_names_and_counts_are_plain_language():
    item = check('MISSING-DATA', 'Data quality', 'unverified', '', '', 'raw internal message', filename='a.csv', rows=[2])
    item['missing_fields'] = {'2': ['definition', 'answer']}
    assert finding_text(item) == 'Missing answer and definition.'
    good = {**item, 'status': 'pass'}
    assert finding_text(good) == 'No required fields are missing.'
    qty = check('QTY-001', 'Task coverage', 'unverified', '100 distinct tasks in Definition to word', '93 identified distinct tasks', '', '')
    assert finding_text(qty) == '93 of 100 required Definition to word tasks identified.'


def test_repeated_findings_are_grouped_without_exposing_internal_ids():
    checks = [check('COMP-002', 'Task completion', 'fail', '{raw json}', 'hash '*20, 'nonsensical internal explanation',
                    'long generated text '*20, filename=f'task-{i}.csv') for i in range(100)]
    assert summary_rows(checks, 'Data quality') == []
    rows = summary_rows(checks, 'Task coverage')
    assert len(rows) == 1 and rows[0]['affected'] == '100 files'
    assert rows[0]['finding'] == 'Required grader or reference evidence is incomplete.'
    assert '\n' not in rows[0]['finding'] and len(rows[0]['next_step']) < 120
    assert len(rows[0]['records']) == 100
