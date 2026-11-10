import os
import importlib
from pathlib import Path
import streamlit as st
from sqlalchemy import select, func
from pipeline.db import sessions
from pipeline.models import Client, ClientTask, ClientTaskRevision
from pipeline.csv_uploads import TaskUploadBatch, UploadedTaskCSV
from pipeline.assessment_agent import AssessmentJob, AssessedAttempt, AssessmentToolCall, run_agent, llm_job_filter
from pipeline.agent_dataset import approve_dataset, export_dataset, export_scored_source_csv
from pipeline.ui import workspace
from pipeline import llm_assessment
from pipeline.evaluation_ui import render_quality_review, render_calibration, render_file_coverage
from pipeline.task_review_ui import render_task_review

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

st.set_page_config(page_title='Assessment', layout='wide')
workspace(2)
st.title('Assessment')
st.caption('Review purpose, data quality, task coverage and rewards for one selected file.')
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

bid = st.selectbox('Dataset upload', list(batch_map), format_func=batch_label, key='assessment_batch')
batch = batch_map[bid]
revision = revisions[batch.requirement_revision_id]
blueprint = revision.snapshot
with factory() as session:
    files = list(session.scalars(select(UploadedTaskCSV).where(UploadedTaskCSV.batch_id == bid).order_by(UploadedTaskCSV.filename, UploadedTaskCSV.id)))
file_map = {file.id: file for file in files}
from collections import Counter
filename_counts = Counter(file.filename for file in files)
file_labels = {file.id: file.filename + (f' ({file.id})' if filename_counts[file.filename] > 1 else '') for file in files}
file_id = st.selectbox('Assessment file', list(file_map), format_func=file_labels.get, key='assessment_file_' + bid)
st.caption('Quality, coverage, rewards and responses below apply only to this file.')
st.caption(f'{batch.file_count:,} task files | Requirements revision {revision.revision} | Uploaded {batch.created_at[:10]}')
with st.expander('Rubric'):
    st.write('**Required capabilities**')
    st.write(blueprint.get('required_capabilities', 'Not specified'))
    st.write('**Outside scope**')
    st.write(blueprint.get('outside_scope', 'Not specified'))
    st.write('**Task quantities**')
    st.dataframe(blueprint.get('quantities', []), hide_index=True, width='stretch')
    st.write('**Additional completion and scoring requirements**')
    for row in blueprint.get('completion_requirements', []) + blueprint.get('scoring_requirements', []):
        st.write(f"{row['scope']}: {row['requirement']}")


with st.expander('Model & calibration settings'):
    configured = bool(os.getenv('OPENAI_API_KEY'))
    api_key = st.text_input('OpenAI API key', type='password', key='llm_api_key', help='Session only; leave blank to use the server key.')
    model = st.text_input('Evaluator model', value=os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'))
    ready = bool(api_key.strip() or configured) and bool(model.strip())
    st.caption('New model assessments send evidence to OpenAI and use API credits; 240 KB / 500 rows per file.')
    if st.button('Test connection', disabled=not ready):
        from pipeline.llm_assessment import OpenAIJudge
        try:
            st.success(OpenAIJudge(api_key=api_key.strip(), model=model.strip()).test_connection())
        except (ValueError, RuntimeError) as exc:
            st.error(str(exc))

with st.expander('Grader calibration · JSON reference examples'):
    render_calibration(factory, bid, api_key, model, ready)

with st.container(border=True):
    if st.button('Run assessment agent', type='primary', disabled=not ready, width='stretch'):
        from pipeline.llm_assessment import OpenAIJudge
        bar = st.progress(0, text='Identifying tasks and grading attempts...')
        def progress(done, total):
            bar.progress(done / total, text=f'Processed file {done:,} of {total:,} (readiness and grading phases)')
        try:
            adapter = OpenAIJudge(api_key=api_key.strip(), model=model.strip())
            adapter.test_connection()
            with factory.begin() as session:
                run_agent(session, bid, adapter=adapter, progress=progress, file_id=file_id)
            st.success('Assessment saved. Review the rewards and evidence below.')
        except (ValueError, RuntimeError) as exc:
            bar.empty()
            st.error(str(exc))

if not ready:
    st.caption('Add an API key in model settings to run a new model assessment.')

with factory() as session:
    jobs = list(session.scalars(select(AssessmentJob).where(AssessmentJob.batch_id == bid,
        llm_job_filter()).order_by(AssessmentJob.created_at.desc())))
jobs = [j for j in jobs if j.summary.get('source_file_id') == file_id or
        (len(files) == 1 and not j.summary.get('source_file_id'))]
job = None
if jobs:
    job_map = {j.id: j for j in jobs}
    jid = st.selectbox('Saved assessment', list(job_map),
        format_func=lambda key: f"{job_map[key].created_at[:16]} | {'Conversation review' if job_map[key].summary.get('assessment_source') == 'assistant_conversation' else job_map[key].adapter.split(':', 1)[-1]}")
    job = job_map[jid]
    if job.summary.get('assessment_source') == 'assistant_conversation':
        st.caption('Conversation review; no API calls.')

st.header('Data quality', divider='blue')
checks = render_quality_review(factory, bid, api_key, model, ready, job.summary.get('report') if job else None, file_id=file_id)

st.header('Task coverage', divider='green')
coverage = render_file_coverage(factory, bid, blueprint, job, api_key, model, ready, file_id=file_id)
from pipeline.reward_review import quick_report, reward_statistics
with factory() as session:
    results = list(session.scalars(select(AssessedAttempt).where(
        AssessedAttempt.job_id == job.id, AssessedAttempt.task_id == file_id))) if job else []
result_data = [a.result for a in results]
short_report = quick_report(blueprint, checks, coverage, reward_statistics(result_data))
st.subheader('Quick report')
for line in short_report.splitlines():
    label, text = line.split(': ', 1)
    st.write(f'**{label}:** {text}')
st.header('Rewards', divider='orange')
if not job:
    st.info('Click Run assessment agent to assign 0/1 rewards and reasoning to this file. Data-quality checks alone do not grade answers.')
    st.stop()
if job.summary.get('file_errors') == job.summary.get('tasks'):
    st.error('Assessment did not complete; no rewards were assigned.')
    with factory() as session:
        failed_call = session.scalar(select(AssessmentToolCall).where(AssessmentToolCall.job_id == job.id))
    if failed_call:
        from pipeline.assessment_summary import one_line
        st.caption(one_line(failed_call.payload.get('error')))
    st.stop()
counts = dict(attempts=len(results), success=sum(a.result['score'] == 1 for a in results),
              failure=sum(a.result['score'] == 0 for a in results), unverified=sum(a.result['score'] is None for a in results))
for col, key, label in zip(st.columns(4), ['attempts', 'success', 'failure', 'unverified'],
                           ['Attempts', 'Reward 1', 'Reward 0', 'Needs review']):
    count = counts[key]
    total = counts['attempts']
    col.metric(label, f'{count:,}', border=True)
    col.caption(f'{count / total:.1%} of {total:,} attempts' if total else 'No attempts')
st.caption('Rewards describe answers; coverage describes dataset completeness.')
from pipeline.reward_ui import render_reward_results
render_task_review(factory, job, file_id=file_id)
render_reward_results(factory, job, file_map[file_id], result_data, api_key, model, ready)
report_for_export = job.summary.get('report')
issues_for_export = report_for_export.get('issues', []) if report_for_export else None
with st.expander('Review & export'):
    with st.container(key='export_panel'):
        st.caption('Approve the reviewed results to unlock dataset and report downloads.')
    
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
            try:
                with factory() as session:
                    enriched_csv = export_scored_source_csv(session, jid, file_id)
                st.download_button('Download original CSV + rewards & reasoning', enriched_csv,
                    f'{Path(file_map[file_id].filename).stem}-with-rewards.csv', 'text/csv', width='stretch')
            except ValueError as exc:
                st.error(str(exc))
            st.download_button('Download quick report', short_report, f'{jid}-quick-report.txt', 'text/plain')
            with factory() as session:
                csv_data = export_dataset(session, jid)
                jsonl_data = export_dataset(session, jid, 'jsonl')
            left, right = st.columns(2)
            left.download_button('Download results CSV', csv_data, f'{jid}-dataset.csv', 'text/csv', width='stretch')
            right.download_button('Download results JSONL', jsonl_data, f'{jid}-dataset.jsonl', 'application/jsonl', width='stretch')
    
            if report_for_export:
                import json
                from pipeline.evaluation_reports import report_markdown, issues_csv
                st.write('**Reports**')
                with st.container(horizontal=True):
                    st.download_button('Download final report JSON', json.dumps(report_for_export, indent=2), f'{jid}-report.json', 'application/json')
                    st.download_button('Download final report Markdown', report_markdown(report_for_export), f'{jid}-report.md', 'text/markdown')
                    if issues_for_export is not None:
                        st.download_button('Download issues & fixes CSV', issues_csv(issues_for_export), f'{jid}-issues.csv', 'text/csv')
