import io
import json
import os
import zipfile
import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from pipeline.db import Base
from pipeline.models import Run, Finding, Revision, Approval, Release
from pipeline.service import Workflow, GateError
from pipeline.validation import grade, correct_output, execute, reference

@pytest.fixture
def session(tmp_path):
    db = create_engine(f'sqlite:///{tmp_path / "test.db"}')
    Base.metadata.create_all(db)
    with Session(db, expire_on_commit=False) as session:
        yield session

def setup(w):
    pid = w.create('Export settled transactions; reject empty outputs.')
    w.approve_spec(pid, 'Reviewer', 'Confirmed bundled task and criteria match brief')
    w.load(pid, 1)
    return pid

def revised(w):
    pid = setup(w)
    w.validate(pid)
    w.request_revision(pid, 'Reviewer', 'Confirmed executable false positive')
    w.load(pid, 2)
    w.validate(pid)
    return pid

def test_grader_probes():
    assert reference() == [{'id': 't1', 'status': 'settled', 'amount': '12.50'},
                           {'id': 't3', 'status': 'settled', 'amount': '7.25'}]
    assert grade(1, '') is True
    assert grade(2, '') is False
    for version in (1, 2):
        evidence = execute(version)
        assert len(evidence['cases']) == 7
        assert all(c['actual'] == c['expected'] for c in evidence['cases']) == (version == 2)

def test_approval_gates(session):
    w = Workflow(session)
    pid = w.create('CSV task')
    with pytest.raises(GateError): w.load(pid, 1)
    with pytest.raises(GateError): w.approve_spec(pid, '', 'yes')
    w.approve_spec(pid, 'Reviewer', 'Confirmed')
    w.load(pid, 1)
    with pytest.raises(GateError): w.load(pid, 2)
    with pytest.raises(GateError): w.release(pid, 'Reviewer', 'yes')
    with pytest.raises(GateError): w.export(pid)
    w.validate(pid)
    with pytest.raises(GateError): w.release(pid, 'Reviewer', 'yes')

def test_end_to_end_history_and_export(session):
    w = Workflow(session)
    pid = revised(w)
    with pytest.raises(GateError): w.export(pid)
    report = w.report(pid)
    assert report['submission_version'] == 2
    assert report['finding_and_revision_history'][0]['revision']['status'] == 'resolved'
    assert {a['kind'] for a in report['prior_human_decisions']} == {'specification', 'finding_review'}
    f = session.scalar(select(Finding))
    assert f.requirement_id == report['requirements'][0]['id']
    assert f.details['failed_probes'][0]['name'] == 'empty'
    revision = session.scalar(select(Revision))
    assert revision.status == 'resolved'
    assert revision.recheck_run_id == report['requirements'][0]['run_id']
    rid = w.release(pid, 'Human reviewer', 'All executable probes passed')
    assert w.release(pid, 'Human reviewer', 'Repeat click') == rid
    package = zipfile.ZipFile(io.BytesIO(w.export(pid)))
    manifest = json.loads(package.read('manifest.json'))
    import hashlib
    for name, checksum in manifest['sha256'].items():
        assert hashlib.sha256(package.read(name)).hexdigest() == checksum
    assert manifest['report']['approval']['actor'] == 'Human reviewer'
    assert session.scalar(select(func.count()).select_from(Release)) == 1
    session.commit()
    session.expire_all()
    assert Workflow(session).export(pid)

def test_export_executes_outside_repository(session, tmp_path):
    import subprocess, sys
    w = Workflow(session)
    pid = revised(w)
    w.release(pid, 'Reviewer', 'Passed')
    with zipfile.ZipFile(io.BytesIO(w.export(pid))) as package:
        package.extractall(tmp_path)
    result = subprocess.run([sys.executable, '-m', 'pipeline.validation', '2'], cwd=tmp_path,
                            capture_output=True, text=True, timeout=10, check=True)
    assert all(c['expected'] == c['actual'] for c in json.loads(result.stdout)['cases'])

def test_cancel_blocks_further_work(session):
    w = Workflow(session)
    pid = setup(w)
    w.cancel(pid, 'Reviewer', 'Stop this demo')
    with pytest.raises(GateError): w.validate(pid)
    with pytest.raises(GateError): w.load(pid, 1)
    with pytest.raises(GateError): w.release(pid, 'Reviewer', 'Cannot release cancelled work')

def test_reruns_and_version_scoping(session):
    w = Workflow(session)
    pid = setup(w)
    first = w.validate(pid)
    assert w.validate(pid) == first
    assert session.scalar(select(func.count()).select_from(Finding)) == 1
    w.request_revision(pid, 'Reviewer', 'Confirmed')
    old = w.latest(pid).id
    w.load(pid, 2)
    assert w.latest(pid).id != old
    with pytest.raises(GateError): w.export(pid)
    with pytest.raises(GateError): w.release(pid, 'Reviewer', 'Old approval insufficient')

def test_timeout_persists_failure(session, monkeypatch):
    import subprocess
    w = Workflow(session)
    pid = setup(w)
    def timeout(version):
        raise subprocess.TimeoutExpired('trusted grader', 10)
    monkeypatch.setattr('pipeline.service.execute', timeout)
    run = session.get(Run, w.validate(pid))
    assert run.status == 'failed'
    assert run.evidence['error'] == 'TimeoutExpired'
    session.commit()
    with pytest.raises(GateError): w.release(pid, 'Reviewer', 'Cannot approve failed execution')

@pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'), reason='Requires disposable PostgreSQL database')
def test_postgres_workflow():
    db = create_engine(os.environ['TEST_DATABASE_URL'])
    # Migrations must already be applied. All test changes roll back.
    with db.connect() as connection:
        transaction = connection.begin()
        with Session(connection, join_transaction_mode='create_savepoint') as s:
            w = Workflow(s)
            pid = revised(w)
            w.release(pid, 'Integration reviewer', 'Passed checks')
            assert w.export(pid)
        transaction.rollback()
