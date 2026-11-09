import uuid
from pathlib import Path
import streamlit as st
from sqlalchemy import select, func
from pipeline.db import sessions
from pipeline.models import Client, ClientTask, ClientTaskRevision
from pipeline.csv_uploads import TaskUploadBatch, UploadedTaskCSV, save_csv_batch

st.set_page_config(page_title='Upload task CSVs', layout='wide')
from pipeline.ui import workspace
workspace(1)
st.title('Upload your dataset')
st.write('Choose a saved request, then add your task CSV files. Original files are preserved for assessment and review.')
back, forward = st.columns(2)
back.page_link('app.py', label='← Requirements', use_container_width=True)
forward.page_link('pages/3_Assess_tasks.py', label='Continue to assessment →', use_container_width=True)
factory = sessions()
with factory() as session:
    requests = list(session.scalars(select(ClientTask).order_by(ClientTask.updated_at.desc())))
    clients = {c.id: c for c in session.scalars(select(Client))}
if not requests:
    st.info('Save client requirements first, then return here to upload the task files.')
    st.stop()
request_map = {r.id: r for r in requests}
tid = st.selectbox('Client and dataset request', list(request_map),
    format_func=lambda key: f'{clients[request_map[key].client_id].name} / {request_map[key].name}')
with factory() as session:
    revisions = list(session.scalars(select(ClientTaskRevision).where(ClientTaskRevision.task_id == tid)
                                    .order_by(ClientTaskRevision.revision.desc())))
revision_map = {r.id: r for r in revisions}
rid = st.selectbox('Saved requirements revision', list(revision_map),
    format_func=lambda key: f'Revision {revision_map[key].revision}')
st.subheader('Select your CSV files')
st.caption('Use one CSV per task, with its attempts in that file. You can select multiple files together.')
st.info('For LLM assessment, keep each task file within 240 KB and 500 data rows. Larger files can be stored but must be split before assessment.')
with st.expander('Upload limits and supported format'):
    st.write('UTF-8 CSV files with a header row. Up to 10,000 files per batch, 50 MiB per file, and 500 MiB combined for storage. Your column names may vary; the LLM identifies task and attempt evidence.')
uploads = st.file_uploader('Task CSV files', type=['csv'], accept_multiple_files=True, key='bulk_task_csvs')
signature = (rid, tuple((f.name, f.size, f.file_id) for f in uploads))
if signature != st.session_state.get('csv_upload_signature'):
    st.session_state['csv_upload_signature'] = signature
    st.session_state['csv_upload_key'] = uuid.uuid4().hex
if uploads:
    left, right = st.columns(2)
    left.metric('Task files selected', f'{len(uploads):,}')
    right.metric('Total selected size', f'{sum(f.size for f in uploads) / (1024 * 1024):.2f} MiB')
if st.button('Upload task files', type='primary', disabled=not uploads):
    bar = st.progress(0, text='Saving task files...')
    def progress(done, total):
        if done % 100 == 0 or done == total:
            bar.progress(done / total, text=f'Saved {done:,} of {total:,} task files')
    try:
        with factory.begin() as session:
            bid = save_csv_batch(session, rid, uploads, st.session_state['csv_upload_key'], progress=progress)
        st.success(f'Uploaded {len(uploads):,} task files. Batch ID: {bid}')
        st.session_state['last_csv_batch'] = bid
        st.session_state['assessment_batch'] = bid
        st.page_link('pages/3_Assess_tasks.py', label='Assess this upload →', use_container_width=True)
    except ValueError as exc:
        bar.empty()
        st.error(str(exc))

st.subheader('Uploaded task files')
with factory() as session:
    batches = list(session.scalars(select(TaskUploadBatch).join(ClientTaskRevision)
        .where(ClientTaskRevision.task_id == tid).order_by(TaskUploadBatch.created_at.desc())))
if not batches:
    st.caption('No task files uploaded for this request yet.')
    st.stop()
batch_map = {b.id: b for b in batches}
last = st.session_state.get('last_csv_batch')
index = list(batch_map).index(last) if last in batch_map else 0
bid = st.selectbox('Upload batch', list(batch_map), index=index,
    format_func=lambda key: f'{batch_map[key].file_count:,} task files | {batch_map[key].created_at} | {key}')
batch = batch_map[bid]
st.write(f'**{batch.file_count:,} tasks stored** | {batch.total_bytes / (1024 * 1024):.2f} MiB | Requirements revision record {batch.requirement_revision_id}')
page = int(st.number_input('File list page', min_value=1, max_value=max(1, (batch.file_count + 49) // 50), value=1, key=f'page_{bid}'))
with factory() as session:
    rows = list(session.scalars(select(UploadedTaskCSV).where(UploadedTaskCSV.batch_id == bid)
                               .order_by(UploadedTaskCSV.filename, UploadedTaskCSV.id).offset((page - 1) * 50).limit(50)))
st.dataframe([{'Task ID': r.id, 'Original filename': r.filename, 'CSV rows (including header if any)': r.row_count,
               'Bytes': r.byte_count} for r in rows], hide_index=True, width='stretch')
chosen = st.selectbox('Inspect a file on this page', [r.id for r in rows],
    format_func=lambda key: next(r.filename + ' | ' + r.id for r in rows if r.id == key))
record = next(r for r in rows if r.id == chosen)
st.caption('First five CSV rows, up to 30 columns and 500 characters per cell. Originals are preserved unchanged.')
st.json(record.preview)
st.caption(f'SHA-256: {record.sha256}')
path = Path(record.path)
if path.exists():
    st.download_button('Download original CSV', path.read_bytes(), Path(record.filename).name, 'text/csv')
else:
    st.error('Original file is missing from local artifact storage.')
