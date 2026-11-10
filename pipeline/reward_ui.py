"""Reward review for exactly one assessed file."""
import streamlit as st
from .reward_review import reward_statistics, reward_review_payload
from .assessment_summary import one_line


def render_reward_results(factory, job, file, results, api_key, model, ready):
    stats = reward_statistics(results)
    st.subheader('Reward percentages')
    for col, label, key in zip(st.columns(3),
        ['Success rate (reward 1)', 'Failure rate (reward 0)', 'Unverified rate'],
        ['reward_1_rate', 'reward_0_rate', 'unverified_rate']):
        col.metric(label, f'{stats[key]:.1%}' if stats[key] is not None else 'Not assessed', border=True)
    st.caption('Percentages use all assessed attempts in this selected file. Unverified answers stay separate.')
    for score, label in ((1, 'Successful answers · reward 1'), (0, 'Unsuccessful answers · reward 0')):
        matching = [item for item in results if item.get('score') == score]
        with st.expander(f'{label} ({len(matching)})'):
            if matching:
                st.dataframe([{'Task ID': item.get('source_task_key') or item.get('attempt_id'),
                    'Task': one_line(' '.join(m.get('content', '') for m in item.get('source_task') or [] if m.get('role') == 'user')),
                    'Response': item.get('candidate_response', ''), 'Reward': score,
                    'Reasoning': item.get('reasoning', '')} for item in matching],
                    hide_index=True, width='stretch', alt=label + ' with assessment reasoning')
            else:
                st.caption('No assessed answers in this group.')
    st.subheader('LLM assessment of rewards')
    st.caption('Optional: summarizes the saved rewards and sampled reasoning. Uses the configured model and API credits.')
    review = job.summary.get('reward_review') if job.summary.get('reward_review_source_file_id') == file.id else None
    if st.button('Summarize rewards with LLM', disabled=not (ready and results), key='review_rewards_' + job.id):
        from .llm_assessment import OpenAIJudge
        from .assessment_agent import AssessmentJob
        try:
            with st.spinner('Summarizing rewards...'):
                judge = OpenAIJudge(api_key=api_key.strip(), model=model.strip())
                review = judge.review_rewards(reward_review_payload(job.blueprint, file.filename, results)).model_dump()
                with factory.begin() as session:
                    stored = session.get(AssessmentJob, job.id)
                    stored.summary = {**stored.summary, 'reward_review': review,
                        'reward_review_source_file_id': file.id, 'reward_review_model': model.strip()}
        except (ValueError, RuntimeError) as exc:
            st.error(str(exc))
    if review:
        st.write(review['summary'])
        for label, field in (('Strengths', 'strengths'), ('Concerns', 'concerns')):
            if review[field]:
                st.caption(label + ': ' + ' '.join(review[field]))
    else:
        st.caption('No LLM reward summary has been generated for this file yet.')
    return stats
