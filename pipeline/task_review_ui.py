"""Compact, filterable task and response workspace."""
import streamlit as st
from sqlalchemy import select
from .assessment_agent import AssessedAttempt
from .assessment_summary import one_line


def render_task_review(factory, job, file_id=None):
    st.subheader('Task workspace')
    left, right = st.columns([2, 1])
    search = left.text_input('Find a task', placeholder='Search tasks, responses or filenames', key='task_search_' + job.id)
    status = right.selectbox('Filter results', ['All attempts', 'Correct', 'Incorrect', 'Unverified'], key='task_filter_' + job.id)
    query = select(AssessedAttempt).where(AssessedAttempt.job_id == job.id)
    if file_id:
        query = query.where(AssessedAttempt.task_id == file_id)
    with factory() as session:
        attempts = list(session.scalars(query.order_by(AssessedAttempt.task_id, AssessedAttempt.id)))
    labels = {'success': 'Correct', 'failure': 'Incorrect', 'unverified': 'Unverified'}
    rows = []
    for saved in attempts:
        item = saved.result
        task = ' '.join(m.get('content', '') for m in item.get('source_task') or [] if m.get('role') == 'user')
        row = {'File': item['source_file'], 'Task': one_line(task) if task else '(Missing task)',
               'Response': item.get('candidate_response') or '(Missing response)',
               'Result': labels.get(item['status'], 'Unverified'), 'Reward': item.get('score'),
               'Reason': one_line(item.get('reasoning', ''))}
        if status != 'All attempts' and row['Result'] != status:
            continue
        if search.strip() and search.casefold().strip() not in ' '.join(str(v) for v in row.values()).casefold():
            continue
        rows.append(row)
    st.caption(f'{len(rows):,} of {len(attempts):,} attempts shown | selected file')
    if rows:
        st.dataframe(rows, hide_index=True, width='stretch', height=560,
                     alt='Tasks, candidate responses, rewards and concise assessment reasons')
    else:
        st.info('No attempts match this filter.')
