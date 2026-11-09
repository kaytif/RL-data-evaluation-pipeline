from pathlib import Path
import streamlit as st
from sqlalchemy import select
from pipeline.db import sessions
from pipeline.models import Project, Requirement, Run, Finding, Revision, ModelCall, Submission
from pipeline.service import Workflow, GateError

st.set_page_config(page_title='Supervised acceptance', layout='wide')
st.title('Supervised RL task acceptance')
st.caption('Independent synthetic portfolio demo · fake model adapter · trusted bundled fixtures only')
factory = sessions()

def action(callback):
    try:
        with factory.begin() as session:
            callback(Workflow(session))
        st.rerun()
    except (GateError, ValueError) as exc:
        st.error(str(exc))

try:
    with factory() as session:
        projects = list(session.scalars(select(Project).order_by(Project.id)))
except Exception:
    st.error('Database unavailable. Use start.ps1 to launch the local demo.')
    st.stop()

with st.expander('Enter a fictional brief', expanded=not projects):
    brief = st.text_area('Lab brief', (Path(__file__).resolve().parent / 'fixtures/brief.txt').read_text(), height=150)
    if st.button('Draft proposed specification'):
        action(lambda w: w.create(brief))
if not projects:
    st.stop()
pid = st.selectbox('Project', [p.id for p in projects], index=len(projects)-1)
actor = st.text_input('Human reviewer name')
rationale = st.text_input('Decision rationale (confirm task applicability when approving specification)')
with factory() as session:
    w = Workflow(session)
    p = session.get(Project, pid)
    state = p.state
    st.info(f'Persisted workflow state: {state}')
    spec = w.spec(pid)
    st.subheader(f'Proposed acceptance baseline · specification {spec.id}, version {spec.version}')
    for req in session.scalars(select(Requirement).where(Requirement.specification_id == spec.id)):
        st.write(f'{req.code}: {req.description}')
        st.warning(req.criteria['clarification'])
        with st.expander('Brief source'):
            st.write(req.source)
    sub = w.latest(pid)
    if sub:
        st.write(f'Task {sub.task_id} · submission {sub.id} · version {sub.version}')
        run = session.scalar(select(Run).where(Run.submission_id == sub.id).order_by(Run.id.desc()))
        if run:
            st.subheader(f'Executable evidence · run {run.id} · {run.status}')
            st.json(run.evidence)
    for finding in session.scalars(select(Finding).join(Run).join(Submission).where(Submission.project_id == pid)):
        st.write(f'Finding {finding.id} → requirement {finding.requirement_id} → run {finding.run_id} · {finding.decision}')
        st.json(finding.details)
        revision = session.scalar(select(Revision).where(Revision.finding_id == finding.id))
        if revision:
            st.write(f'Revision {revision.id} · owner: {revision.owner} · {revision.status} · recheck: {revision.recheck_run_id}')
            st.code(revision.request)
    with st.expander('Simulated model call log'):
        for call in session.scalars(select(ModelCall).where(ModelCall.project_id == pid)):
            st.json(call.payload)
    if state in ('awaiting_release_approval', 'approved_export'):
        st.subheader('Proposed acceptance report')
        st.json(w.report(pid))
    if state == 'approved_export':
        st.download_button('Download human-approved package', w.export(pid), f'acceptance-{pid}-v{sub.version}.zip', 'application/zip')

if state == 'awaiting_specification_approval' and st.button('Approve specification as human reviewer'):
    action(lambda w: w.approve_spec(pid, actor, rationale))
if state == 'submission_received':
    if sub is None and st.button('Load bundled flawed task v1'):
        action(lambda w: w.load(pid, 1))
    if sub and st.button('Run executable checks'):
        action(lambda w: w.validate(pid))
if state == 'findings_awaiting_review' and st.button('Confirm findings and generate local revision request'):
    action(lambda w: w.request_revision(pid, actor, rationale))
if state == 'revisions_requested' and st.button('Load corrected bundled task v2'):
    action(lambda w: w.load(pid, 2))
if state == 'awaiting_release_approval' and st.button('Approve release as human reviewer'):
    action(lambda w: w.release(pid, actor, rationale))
if state == 'failed' and st.button('Retry failed execution'):
    action(lambda w: w.retry(pid))
if state not in ('approved_export', 'cancelled') and st.button('Cancel project'):
    action(lambda w: w.cancel(pid, actor, rationale))
