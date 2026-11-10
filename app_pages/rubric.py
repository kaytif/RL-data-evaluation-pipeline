import math
from pathlib import Path
import pandas as pd
import streamlit as st
from pydantic import ValidationError
from sqlalchemy import select
from pipeline.db import sessions
from pipeline.intake import IntakeService
from pipeline.rubric import COMPLETION_DEFAULTS, SCORING_DEFAULTS
from pipeline.models import Client, ClientTask, ClientTaskRevision, ClientTaskRubric

st.set_page_config(page_title='Rubric', layout='wide')
from pipeline.ui import workspace
workspace(0)
st.title('Rubric')
st.write('Define your dataset, task quantities, and acceptance requirements. Save a revision before uploading data.')
with st.expander('Rubric guide'):
    st.markdown('**Task:** a problem to solve. **Attempt:** one separate execution. **Completion check:** evidence that a requirement was met. **Reward:** the attempt score.')
    st.caption('Define capabilities, categories and minimum quantities below. Completion and scoring defaults are saved with each revision.')
st.link_button('Continue to upload', '/Upload_submission', width='stretch')

factory = sessions()
with factory() as session:
    clients = list(session.scalars(select(Client).order_by(Client.name, Client.id)))
    tasks = list(session.scalars(select(ClientTask).order_by(ClientTask.updated_at.desc(), ClientTask.id)))
client_map = {c.id: c for c in clients}
task_map = {t.id: t for t in tasks}
if 'open_after_save' in st.session_state:
    st.session_state['selected_task'] = st.session_state.pop('open_after_save')
if message := st.session_state.pop('saved_message', None):
    st.success(message)
with st.sidebar:
    st.subheader('Your dataset requests')
    selected = st.selectbox('Open a task', [None, *task_map], key='selected_task',
        format_func=lambda tid: '+ New task' if tid is None else f'{client_map[task_map[tid].client_id].name} / {task_map[tid].name} | {tid}')
    st.caption(f'{len(clients)} clients · {len(tasks)} saved requests')

task = task_map.get(selected)
payload = {}
st.subheader('Edit rubric' if task else 'New rubric')
if task:
    client = client_map[task.client_id]
    left, right = st.columns(2)
    left.text_input('Client ID', client.id, disabled=True, key=f'client_id_{task.id}')
    right.text_input('Task ID', task.id, disabled=True, key=f'task_id_{task.id}')
    st.caption(f'Revision {task.revision} | Last saved {task.updated_at}')
    client_id = client.id
    with factory() as session:
        rubric = session.get(ClientTaskRubric, task.id)
        payload = rubric.payload if rubric else {}
    if not payload:
        st.info('This record uses the previous form. Review the original entries below, then complete this rubric to save a new revision. Earlier revisions are preserved.')
        with st.expander('Original requirements'):
            for field in ['intended_use', 'task_coverage', 'record_completeness', 'outcome_evidence', 'integrity_restrictions']:
                st.write(field.replace('_', ' ').capitalize())
                st.text(getattr(task, field))
    else:
        import json
        from pipeline.assessment_spec import assessment_spec, spec_csv
        spec = {**assessment_spec(payload), 'requirement_revision': task.revision}
        st.write('Saved assessment specification')
        first, second = st.columns(2)
        first.download_button('Download requirements CSV', spec_csv(spec), f'{task.id}-r{task.revision}-requirements.csv', 'text/csv')
        second.download_button('Download assessment JSON', json.dumps(spec, indent=2), f'{task.id}-r{task.revision}-assessment.json', 'application/json')
        st.caption('Exports reflect the saved revision. Save changes before downloading updated requirements.')
else:
    client_id = st.selectbox('Client', [None, *client_map], key='new_task_client',
        format_func=lambda cid: 'Create a new client' if cid is None else f'{client_map[cid].name} | {cid}')
    client = client_map.get(client_id)
    st.caption('Client and task IDs are generated automatically when you save. This task name identifies the dataset request; the dataset can contain many distinct agent tasks.')

prefix = f'{task.id}_{task.revision}' if task else f'new_{client_id}'
left, right = st.columns(2)
client_name = left.text_input('Client name *', value=client.name if client else '', disabled=client is not None,
                              max_chars=200, key=f'{prefix}_client_name')
task_name = right.text_input('Task name *', value=task.name if task else '', max_chars=200,
                             placeholder='e.g. File-processing post-training dataset', key=f'{prefix}_task_name')

st.header('1. Intended use')
st.write('This dataset is intended for reinforcement-learning post-training. Tell us which agent capabilities it must help improve.')
capabilities = st.text_area('Required capabilities *', value=payload.get('required_capabilities', ''),
    placeholder='Tasks must require agents to filter CSV records, transform JSON fields, or extract specified information from text files.',
    help='Name the actions the agent must learn to perform.', height=120, max_chars=20000, key=f'{prefix}_capabilities')
outside = st.text_area('Activities outside scope *', value=payload.get('outside_scope', ''),
    placeholder='Exclude browser interaction, image interpretation, or external network access. Enter None if no activities are excluded.',
    height=100, max_chars=20000, key=f'{prefix}_outside')

st.header('2. Required task quantities')
st.write('List permitted categories, minimum distinct tasks per category, and minimum attempts per task. Each task must belong to exactly one category. Each execution must have a unique attempt ID.')
st.caption('Repeated attempts do not count as distinct tasks. Add or delete rows using the table controls. Totals update as you edit.')
initial = payload.get('quantities') or [{'category': '', 'minimum_tasks': 1, 'attempts_per_task': 1}]
quantities_grid = st.data_editor(pd.DataFrame(initial), num_rows='dynamic', hide_index=True,
    key=f'{prefix}_quantities', width='stretch',
    column_config={
        'category': st.column_config.TextColumn('Task category', required=True),
        'minimum_tasks': st.column_config.NumberColumn('Minimum distinct tasks', min_value=1, step=1, default=1, required=True),
        'attempts_per_task': st.column_config.NumberColumn('Minimum attempts per task', min_value=1, step=1, default=1, required=True)})

def clean_rows(frame):
    return [{k: (None if pd.isna(v) else int(v) if isinstance(v, float) and math.isfinite(v) and v.is_integer() else v)
             for k, v in row.items()} for row in frame.to_dict('records')]

quantities = [r for r in clean_rows(quantities_grid) if str(r.get('category') or '').strip() or
              r.get('minimum_tasks') not in (None, 1) or r.get('attempts_per_task') not in (None, 1)]
valid_counts = [r for r in quantities if isinstance(r.get('minimum_tasks'), int) and r['minimum_tasks'] > 0
                and isinstance(r.get('attempts_per_task'), int) and r['attempts_per_task'] > 0]
left, right = st.columns(2)
left.metric('Minimum distinct tasks', sum(r['minimum_tasks'] for r in valid_counts))
right.metric('Minimum attempts', sum(r['minimum_tasks'] * r['attempts_per_task'] for r in valid_counts))
st.caption('Minimum attempts = sum of (minimum distinct tasks x minimum attempts per task). Incomplete rows must be corrected before saving.')
scopes = ['All tasks', *dict.fromkeys(r['category'].strip() for r in quantities if isinstance(r.get('category'), str) and r['category'].strip())]

def additions(field, label):
    st.write(label)
    st.caption('Optional. Add a row and choose All tasks or a named category, then enter the requirement. Leave the table empty if the defaults are sufficient.')
    frame = pd.DataFrame(payload.get(field, []), columns=['scope', 'requirement'])
    edited = st.data_editor(frame, num_rows='dynamic', hide_index=True, width='stretch', key=f'{prefix}_{field}',
        column_config={'scope': st.column_config.SelectboxColumn('Applies to', options=scopes, default='All tasks', required=True),
                       'requirement': st.column_config.TextColumn('Requirement', required=True, width='large')})
    return [row for row in clean_rows(edited) if row.get('requirement') or row.get('scope') not in (None, '', 'All tasks')]

st.header('3. Task completion criteria')
st.write('The following requirements apply to every submitted task by default:')
for i, rule in enumerate(COMPLETION_DEFAULTS, 1):
    st.write(f'{i}. {rule}')
completion = additions('completion_requirements', 'Additional client completion requirements')
st.caption('Example: CSV processing | Graders must compare actual contents against expected results; file existence alone must not count as completion.')

st.header('4. Scoring and evidence requirements')
st.write('The following rules apply to every attempt by default:')
for i, rule in enumerate(SCORING_DEFAULTS, 1):
    st.write(f'{i}. {rule}')
scoring = additions('scoring_requirements', 'Changes to scoring rules or additional evidence requirements')
st.caption('Example: Database tasks | Include the executed query alongside the check results. Any requested changes are stored explicitly for later review.')

st.header('5. Other requirements or concerns')
comments = st.text_area('Other requirements or concerns (optional)', value=payload.get('comments', ''),
    placeholder='Prohibited data, source restrictions, delivery deadlines, reporting preferences, or known quality concerns.',
    height=120, max_chars=20000, key=f'{prefix}_comments')
st.caption('Saving records your requirements; it does not approve an acceptance baseline or evaluate a dataset.')
if st.button('Save changes' if task else 'Save rubric', type='primary'):
    values = dict(client_name=client_name, task_name=task_name, required_capabilities=capabilities, outside_scope=outside,
                  quantities=quantities, completion_requirements=completion, scoring_requirements=scoring, comments=comments)
    try:
        with factory.begin() as session:
            tid = IntakeService(session).save(values, client_id=client_id,
                task_id=task.id if task else None, expected_revision=task.revision if task else None)
        st.session_state['open_after_save'] = tid
        st.session_state['saved_message'] = 'Rubric saved, including quantities, default rules, and your additions.'
        st.rerun()
    except ValidationError as exc:
        for error in exc.errors():
            location = ' / '.join(str(p + 1) if isinstance(p, int) else p.replace('_', ' ').capitalize() for p in error['loc'])
            st.error(f"{location or 'Requirements'}: {error['msg']}")
    except ValueError as exc:
        st.error(str(exc))
if task:
    with st.expander('Saved revision history'):
        with factory() as session:
            revisions = list(session.scalars(select(ClientTaskRevision).where(ClientTaskRevision.task_id == task.id)
                                           .order_by(ClientTaskRevision.revision.desc())))
        for revision in revisions:
            st.markdown(f'**Revision {revision.revision}** | {revision.saved_at}')
            st.json(revision.snapshot, expanded=False)
