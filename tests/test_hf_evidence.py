import csv
import io
import json
from sqlalchemy import select
from pipeline.assessment_agent import run_agent, AssessedAttempt
from test_assessment_agent import setup


def records(rows):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()


def row(id, candidate, label, score, principle='accuracy'):
    return dict(row_id=id, state_json=json.dumps(dict(conversation=[dict(role='user', content='Translate hello')],
        candidate_response=candidate, evaluation_principle=principle)), gold_label=label, gold_score=score,
        jev_prediction='0.99')


def test_candidate_attempts_and_multiple_principles(tmp_path):
    data = [row('a', 'bonjour', 'true', 1), row('b', 'bonjour', 'true', 1, 'clarity'),
            row('c', 'wrong', 'false', 0)]
    _, factory, bid = setup(tmp_path, records(data))
    with factory.begin() as session:
        jid = run_agent(session, bid)
        results = [a.result for a in session.scalars(select(AssessedAttempt).where(AssessedAttempt.job_id == jid).order_by(AssessedAttempt.id))]
        assert [r['score'] for r in results] == [1, 0]
        assert results[0]['source_attempt_id'] == 'a;b'
        assert results[0]['candidate_response'] == 'bonjour'
        assert len(results[0]['evidence']) == 2
        assert results[1]['evidence'][0]['source_rows'] == [4]
        assert results[0]['source_task'][0]['content'] == 'Translate hello'


def test_conflicting_gold_is_unverified(tmp_path):
    _, factory, bid = setup(tmp_path, records([row('a', 'bonjour', 'true', 0)]))
    with factory.begin() as session:
        jid = run_agent(session, bid)
        assert session.scalar(select(AssessedAttempt).where(AssessedAttempt.job_id == jid)).result['score'] is None
