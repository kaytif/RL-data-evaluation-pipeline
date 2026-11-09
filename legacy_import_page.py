import hashlib
import io
import json
import zipfile
import streamlit as st
from sqlalchemy import select, func
from pipeline.db import sessions
from pipeline.models import Client, ClientTask, ClientTaskRevision
from pipeline.upload_models import DataSubmission, ImportedRecord
from pipeline.assessment_spec import assessment_spec, spec_csv, template, TEMPLATE_FIELDS
from pipeline.imports import parse, assess, save_import, ImportProblem, MAX_BYTES

st.set_page_config(page_title='Upload submission', layout='wide')
st.title('Upload a submission')
st.caption('Step 2 | Compare vendor data with a saved client requirements revision')
st.page_link('app.py', label='Back to client requirements')
factory = sessions()
with factory() as session:
    tasks = list(session.scalars(select(ClientTask).order_by(ClientTask.updated_at.desc())))
    clients = {c.id: c for c in session.scalars(select(Client))}
if not tasks:
    st.info('Save client requirements first, then return here to upload the vendor data.')
    st.stop()
task_map = {task.id: task for task in tasks}
tid = st.selectbox('Client and dataset request', list(task_map),
    format_func=lambda key: f'{clients[task_map[key].client_id].name} / {task_map[key].name} | {key}')
with factory() as session:
    revisions = [r for r in session.scalars(select(ClientTaskRevision).where(ClientTaskRevision.task_id == tid)
                 .order_by(ClientTaskRevision.revision.desc())) if r.snapshot.get('rubric_version') == 2]
if not revisions:
    st.info('Open this task in the requirements form and save it using the new post-training rubric first.')
    st.stop()
revision_map = {r.id: r for r in revisions}
rid = st.selectbox('Requirements revision to assess against', list(revision_map),
    format_func=lambda key: f'Revision {revision_map[key].revision} | {revision_map[key].saved_at}')
snapshot = revision_map[rid].snapshot
spec = {**assessment_spec(snapshot), 'requirement_revision': revision_map[rid].revision, 'requirement_revision_id': rid}
st.write('This submission will stay linked to this exact saved revision, even if the client changes requirements later.')
left, right = st.columns(2)
left.download_button('Download requirements CSV', spec_csv(spec), f'requirements-r{rid}.csv', 'text/csv')
right.download_button('Download assessment JSON', json.dumps(spec, indent=2), f'assessment-r{rid}.json', 'application/json')

with st.expander('File format and downloadable templates', expanded=True):
    st.write('Upload three kinds of records. Each may be CSV, a JSON array of objects, or JSONL (one object per line). You may split a large dataset across several files of the same kind.')
    st.write('1. **Tasks:** one row per distinct agent task, with a category, version, instructions, required result, grader reference, special-case behavior, and completion check names.')
    st.write('2. **Attempts:** one row per execution, linked to a task ID and version, with model, reward, and trace.')
    st.write('3. **Checks:** one row per named check for an attempt, with expected result, actual result, status and reason if unverified.')
    for role in TEMPLATE_FIELDS:
        st.download_button(f'Download {role} CSV template', template(role), f'{role}.csv', 'text/csv')
    st.caption('Use strings for IDs and task_version. In CSV, completion_checks and trace cells contain JSON arrays. Example completion_checks: ["contents"]. Example trace: [{"action":"write output","observation":"file saved"}]. Rewards: 0, 1, or unverified. Check statuses: pass, fail, or unverified. Leave unverified_reason empty for completed checks.')
    example = io.BytesIO()
    with zipfile.ZipFile(example, 'w') as archive:
        category = snapshot['quantities'][0]['category']
        task = dict(task_id='example-001', task_version='1', category=category, instructions='Write the word hello to result.txt',
                    expected_result='result.txt contains hello', grader_reference='vendor-grader-v1', special_case_behavior='No inputs; missing output fails.', completion_checks=['contents'])
        attempt = dict(attempt_id='attempt-001', task_id='example-001', task_version='1', model='synthetic-example', reward=1,
                       trace=[{'action': 'write result.txt', 'observation': 'hello written'}])
        check = dict(attempt_id='attempt-001', check_name='contents', expected_result='hello', actual_result='hello', status='pass', unverified_reason='')
        for role, value in [('tasks', task), ('attempts', attempt), ('checks', check)]:
            archive.writestr(f'{role}.jsonl', json.dumps(value) + '\n')
    st.download_button('Download a small example package', example.getvalue(), 'example-submission.zip', 'application/zip')
    st.caption('Extract the example ZIP and upload its three JSONL files below. The one-record example illustrates structure; it may not meet your quantities or capabilities. ZIP upload and executable Python uploads are not supported.')

st.subheader('Upload data files')
st.caption('UTF-8 files. Up to 50 MiB combined and 200,000 total records per submission. Only 20 preview rows are rendered for each record type.')
files = []
for role in TEMPLATE_FIELDS:
    uploads = st.file_uploader(f'{role.capitalize()} files', type=['csv', 'json', 'jsonl'], accept_multiple_files=True, key=f'upload_{role}')
    files.extend((role, upload.name, upload.getvalue()) for upload in uploads)
signature = hashlib.sha256((str(rid) + repr([(role, name, hashlib.sha256(data).hexdigest()) for role, name, data in files])).encode()).hexdigest()
if st.button('Preview and assess', type='primary'):
    st.session_state.pop('import_preview', None)
    try:
        with st.spinner('Parsing records and checking requirements...'):
            records = parse(files)
            report = assess(snapshot, records)
        st.session_state['import_preview'] = {'signature': signature, 'report': report,
            'sample': {role: [r['payload'] for r in rows[:20]] for role, rows in records.items()}}
    except ImportProblem as exc:
        st.error(str(exc))
preview = st.session_state.get('import_preview')
if preview and preview['signature'] == signature:
    report = preview['report']
    st.subheader('Preview assessment')
    st.warning(f"{report['status'].replace('_', ' ').capitalize()} | {report['failure_count']} detected failures")
    for role, count in report['counts'].items():
        st.write(f'**{role.capitalize()}:** {count:,} records')
        with st.expander(f'Preview {role} (first 20 records)'):
            st.json(preview['sample'][role])
    st.dataframe([{k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in r.items() if k != 'examples'} for r in report['results']], hide_index=True)
    with st.expander('Evidence: source files and data-record numbers'):
        st.json(report['results'])
    st.info(report['limitations'])
    st.caption('You can import a structurally readable submission with failed checks to retain its evidence for review. Importing is not acceptance or release approval.')
    if st.button('Confirm import and save evidence'):
        try:
            with factory.begin() as session:
                sid = save_import(session, rid, files)
            st.success(f'Submission saved: {sid}')
        except (ImportProblem, ValueError) as exc:
            st.error(str(exc))

st.subheader('Saved submissions')
with factory() as session:
    stored = list(session.scalars(select(DataSubmission).join(ClientTaskRevision).where(ClientTaskRevision.task_id == tid)
                                 .order_by(DataSubmission.created_at.desc())))
if not stored:
    st.caption('No submissions imported for this request yet.')
else:
    chosen = st.selectbox('Open saved submission', [r.id for r in stored])
    submission = next(r for r in stored if r.id == chosen)
    st.write(f'Requirements revision record: {submission.requirement_revision_id} | Imported {submission.created_at}')
    st.json(submission.report, expanded=False)
    st.download_button('Download assessment report', json.dumps(submission.report, indent=2), f'{chosen}-report.json', 'application/json')
    with factory() as session:
        sample = list(session.scalars(select(ImportedRecord).where(ImportedRecord.submission_id == chosen).limit(20)))
    with st.expander('Saved records (first 20)'):
        st.json([{'role': r.role, 'file': r.source_file, 'record': r.source_row, 'data': r.payload} for r in sample])
    with st.expander('Original file checksums'):
        st.json(submission.files)
