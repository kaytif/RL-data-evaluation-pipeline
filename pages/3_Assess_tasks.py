import os
import importlib
from pathlib import Path
import streamlit as st
from sqlalchemy import select, func
from pipeline.db import sessions
from pipeline.models import Client, ClientTask, ClientTaskRevision
from pipeline.csv_uploads import TaskUploadBatch
from pipeline.assessment_agent import AssessmentJob, AssessedAttempt, AssessmentToolCall, run_agent
from pipeline.agent_dataset import approve_dataset, export_dataset
from pipeline.ui import workspace
from pipeline import llm_assessment

# Streamlit can retain imported modules across page updates in a long-running server.
# Refresh the old judge implementation before exposing actions that require the new API.
backend_mtime = Path(llm_assessment.__file__).stat().st_mtime_ns
if isinstance(llm_assessment.OpenAIJudge, type) and (
    not hasattr(llm_assessment.OpenAIJudge, 'test_connection') or
    (llm_assessment.OpenAIJudge.__module__ == llm_assessment.__name__ and
     getattr(llm_assessment, '_loaded_mtime', None) != backend_mtime)
):
    importlib.reload(llm_assessment)
    llm_assessment._loaded_mtime = backend_mtime

st.set_page_config(page_title='Assess & review', layout='wide')
workspace(2)
st.title('Assess & review')
st.write('Independently evaluate each attempt with an LLM, then review its reward, reasoning, and source evidence.')
st.page_link('pages/2_Upload_submission.py', label='Back to upload')
factory = sessions()
with factory() as session:
    batches = list(session.scalars(select(TaskUploadBatch).order_by(TaskUploadBatch.created_at.desc())))
    requests = {r.id: r for r in session.scalars(select(ClientTask))}
    revisions = {r.id: r for r in session.scalars(select(ClientTaskRevision))}
    clients = {r.id: r for r in session.scalars(select(Client))}
# Retain stored history, while removing synthetic demo entries from this workflow.
batches = [b for b in batches if b.request_key != 'synthetic-live-demo-v1']
if not batches:
    st.info('Your assessment workspace is ready. Upload a dataset to begin.')
    st.page_link('pages/2_Upload_submission.py', label='Upload your first dataset')
    st.stop()
batch_map = {b.id: b for b in batches}
if st.session_state.get('assessment_batch') not in batch_map:
    st.session_state.pop('assessment_batch', None)
def batch_label(key):
    batch = batch_map[key]
    req = requests[revisions[batch.requirement_revision_id].task_id]
    return f'{clients[req.client_id].name} / {req.name} | {batch.file_count} files | {batch.created_at[:16]}'

st.subheader('1. Choose your dataset')
bid = st.selectbox('Dataset upload', list(batch_map), format_func=batch_label, key='assessment_batch')
batch = batch_map[bid]
revision = revisions[batch.requirement_revision_id]
blueprint = revision.snapshot
st.caption(f'{batch.file_count:,} task files | Requirements revision {revision.revision} | Uploaded {batch.created_at[:10]}')
with st.expander('View attached requirements'):
    st.write('**Required capabilities**')
    st.write(blueprint.get('required_capabilities', 'Not specified'))
    st.write('**Outside scope**')
    st.write(blueprint.get('outside_scope', 'Not specified'))
    st.write('**Task quantities**')
    st.dataframe(blueprint.get('quantities', []), hide_index=True, width='stretch')
    st.write('**Additional completion and scoring requirements**')
    for row in blueprint.get('completion_requirements', []) + blueprint.get('scoring_requirements', []):
        st.write(f"{row['scope']}: {row['requirement']}")
    st.caption('The current evaluator grades attempt evidence. Dataset quantity compliance and default task-package requirements still need separate review.')

st.subheader('2. Run LLM assessment')
with st.container(border=True):
    st.write('The evaluator identifies tasks and responses, selects reference answers or criteria, and assigns a reward with supporting evidence. Existing labels and previous model scores are hidden.')
    configured = bool(os.getenv('OPENAI_API_KEY'))
    if configured:
        st.caption('An API key is configured on this server.')
    with st.expander('Model connection', expanded=not configured and not st.session_state.get('llm_api_key')):
        api_key = st.text_input('OpenAI API key', type='password', key='llm_api_key',
            help='Kept in this session only. Not saved to the database or logs. Leave blank to use the server key.')
        st.link_button('Get an API key', 'https://platform.openai.com/api-keys')
        st.link_button('API billing & credits', 'https://platform.openai.com/settings/organization/billing/overview')
        model = st.text_input('Evaluator model', value=os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'))
    st.caption('Uses OpenAI API credits and sends task evidence to OpenAI. Each file must be within 240 KB and 500 rows. Completed calls are cached; up to 300 new calls per run.')
    ready = bool(api_key.strip() or configured) and bool(model.strip())
    if st.button('Test connection', disabled=not ready):
        from pipeline.llm_assessment import OpenAIJudge
        try:
            with st.spinner('Checking API connection and model access...'):
                message = OpenAIJudge(api_key=api_key.strip(), model=model.strip()).test_connection()
            st.success(message)
        except (ValueError, RuntimeError) as exc:
            st.error(str(exc))
    if not ready:
        st.caption('Add your API key above to enable assessment.')
    if st.button('Run assessment agent', type='primary', disabled=not ready, use_container_width=True):
        from pipeline.llm_assessment import OpenAIJudge
        bar = st.progress(0, text='Identifying tasks and grading attempts...')
        def progress(done, total):
            bar.progress(done / total, text=f'Assessed {done:,} of {total:,} task files')
        try:
            adapter = OpenAIJudge(api_key=api_key.strip(), model=model.strip())
            adapter.test_connection()
            with factory.begin() as session:
                run_agent(session, bid, adapter=adapter, progress=progress)
            st.success('Assessment saved. Review the rewards and evidence below.')
        except (ValueError, RuntimeError) as exc:
            bar.empty()
            st.error(str(exc))

with factory() as session:
    jobs = list(session.scalars(select(AssessmentJob).where(AssessmentJob.batch_id == bid,
        AssessmentJob.adapter.like('openai-%')).order_by(AssessmentJob.created_at.desc())))
if not jobs:
    st.info('No LLM assessment yet for this upload. Connect your model and run assessment above.')
    st.stop()
st.divider()
st.subheader('3. Review results')
job_map = {j.id: j for j in jobs}
jid = st.selectbox('Saved assessment', list(job_map),
    format_func=lambda key: f"{job_map[key].created_at[:16]} | {job_map[key].adapter.split(':', 1)[-1]} | {job_map[key].summary['attempts']} attempts")
job = job_map[jid]
if job.summary.get('file_errors') == job.summary.get('tasks'):
    st.error('This run failed before producing any complete assessments. These are system errors, not judgments of your dataset.')
    with factory() as session:
        failed_call = session.scalar(select(AssessmentToolCall).where(AssessmentToolCall.job_id == jid))
    if failed_call:
        st.write(failed_call.payload.get('error') or 'No usable model response was received.')
    st.info('Use Test connection above. Once it succeeds, run assessment again. No reward or export is available for this failed run.')
    st.stop()
for col, key, label in zip(st.columns(4), ['attempts', 'success', 'failure', 'unverified'],
                           ['Attempts', 'Reward 1', 'Reward 0', 'Needs review']):
    col.metric(label, job.summary[key])
st.caption('1 = requirements satisfied | 0 = a demonstrated failure | Needs review = insufficient evidence or an assessment error.')
if job.summary.get('file_errors'):
    st.warning(f"{job.summary['file_errors']} file(s) could not be fully assessed. Inspect their reasons below, correct the issue, and run again.")
results_tab, export_tab = st.tabs(['Attempt results', 'Review & export'])
with results_tab:
    status = st.selectbox('Filter results', ['All attempts', 'Reward 1', 'Reward 0', 'Needs review'], key=f'filter_{jid}')
    status_map = {'Reward 1': 'success', 'Reward 0': 'failure', 'Needs review': 'unverified'}
    query = select(AssessedAttempt).where(AssessedAttempt.job_id == jid)
    if status != 'All attempts':
        query = query.where(AssessedAttempt.result['status'].as_string() == status_map[status])
    with factory() as session:
        matching = session.scalar(select(func.count()).select_from(query.subquery()))
    total_pages = max(1, (matching + 49) // 50)
    page = int(st.number_input('Results page', min_value=1, max_value=total_pages, value=1, key=f'page_{jid}_{status}'))
    with factory() as session:
        attempts = list(session.scalars(query.order_by(AssessedAttempt.task_id, AssessedAttempt.id).offset((page - 1) * 50).limit(50)))
    st.caption(f'{matching:,} matching attempts | Page {page} of {total_pages}')
    st.dataframe([{'Source file': a.result['source_file'], 'Reward': a.result['score'],
        'Result': a.result['status'].capitalize(), 'Reasoning': a.result['reasoning']} for a in attempts],
        hide_index=True, width='stretch')
    if attempts:
        chosen = st.selectbox('Inspect an attempt', [a.id for a in attempts],
            format_func=lambda key: next(f"{a.result['source_file']} | {a.result['source_attempt_id'] or a.result['attempt_id']}" for a in attempts if a.id == key))
        attempt = next(a for a in attempts if a.id == chosen)
        result = attempt.result
        with st.container(border=True):
            st.write('**Reward: ' + (str(result['score']) if result['score'] is not None else 'Needs review') + '**')
            st.write(result['reasoning'])
            task_col, response_col = st.columns(2)
            with task_col:
                st.write('**Task & context**')
                for message in result.get('source_task') or []:
                    st.caption(message.get('role', 'Context').capitalize())
                    st.text(message.get('content', ''))
            with response_col:
                st.write('**Candidate response**')
                st.text(result.get('candidate_response') or 'No response could be extracted. See the assessment reason.')
            st.write('**Checks & evidence**')
            for check in result.get('evidence', []):
                with st.expander(f"{check['check']} | {check['status'].capitalize()}", expanded=True):
                    st.write(check['reason'])
                    st.caption('Source rows: ' + ', '.join(map(str, check.get('source_rows', []))))
                    if check.get('expected') is not None:
                        st.write('Reference answer')
                        st.text(check['expected'])
            if result.get('task_review_notes'):
                with st.expander('Review notes'):
                    for note in result['task_review_notes']:
                        st.write(note)
        with st.expander('Assessment record & technical details'):
            st.json(result)
            with factory() as session:
                call = session.scalar(select(AssessmentToolCall).where(AssessmentToolCall.job_id == jid, AssessmentToolCall.task_id == attempt.task_id))
            if call:
                st.json(call.payload)
    else:
        st.info('No attempts match this filter.')
with export_tab:
    st.write('Review the grouping, references, individual rewards, and unresolved cases before approving this assessment for download.')
    st.caption(job.summary['limitations'])
    if not job.approval:
        actor = st.text_input('Reviewer name', key=f'actor_{jid}')
        rationale = st.text_area('Review rationale', key=f'rationale_{jid}', placeholder='Describe what you reviewed and any limitations you accepted.')
        confirm = st.checkbox('I reviewed the task grouping, rewards, evidence, and unresolved items. I approve export with unverified scores left empty.', key=f'confirm_{jid}')
        if st.button('Approve dataset export', type='primary', disabled=not (confirm and actor.strip() and rationale.strip())):
            try:
                with factory.begin() as session:
                    approve_dataset(session, jid, actor, rationale)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    else:
        st.success(f"Export approved by {job.approval['actor']}")
        with factory() as session:
            csv_data = export_dataset(session, jid)
            jsonl_data = export_dataset(session, jid, 'jsonl')
        left, right = st.columns(2)
        left.download_button('Download results CSV', csv_data, f'{jid}-dataset.csv', 'text/csv', use_container_width=True)
        right.download_button('Download results JSONL', jsonl_data, f'{jid}-dataset.jsonl', 'application/jsonl', use_container_width=True)
