import streamlit as st
from pipeline.rubric import COMPLETION_DEFAULTS, SCORING_DEFAULTS

st.set_page_config(page_title='Requirements guide', layout='wide')
from pipeline.ui import workspace
workspace(0)
st.title('Client Requirements Form: RL Post-Training Data')
st.write('Use this guide to specify what the dataset must contain and the rules it must satisfy before acceptance. Keep this page open beside the requirements form.')
st.page_link('app.py', label='Go to the requirements form')
st.header('Definitions')
st.markdown('''
- **Task:** A problem an agent is asked to solve.
- **Attempt:** One separate execution of a task by an agent.
- **Completion check:** A test used to determine whether the agent met a task requirement.
- **Reward:** The score assigned to an attempt.
''')
st.caption('The client/task name and generated IDs on the form identify your dataset request. The quantities table describes the agent tasks required inside that dataset.')
st.header('1. Intended use')
st.write('This dataset is intended for reinforcement-learning post-training. Tell us which agent capabilities it must help improve.')
st.markdown('''
**Required capabilities:** Name the actions the agent must learn to perform.

Example: “The tasks must require agents to filter CSV records, transform JSON fields, or extract specified information from text files.”

**Activities outside scope:** List activities that must not be included. Enter “None” if there are no exclusions.

Example: “Exclude tasks requiring browser interaction, image interpretation, or external network access.”
''')
st.header('2. Required task quantities')
st.markdown('''
Enter one row per permitted category. Each task must belong to exactly one category.

| Task category | Minimum distinct tasks | Minimum attempts per task |
|---|---:|---:|
| CSV processing | 10 | 4 |
| JSON processing | 10 | 4 |
| Text extraction | 5 | 4 |

This example requires at least **25 distinct tasks and 100 attempts**. Totals are calculated from the table rather than entered separately.

- **Task categories:** List the permitted categories.
- **Distinct tasks per category:** Minimum different tasks required. Repeated attempts do not count as different tasks.
- **Attempts per task:** Minimum separate executions required for every task in that category. Each execution must have a unique attempt ID.

Use the table row controls to add or delete categories. Quantities must be whole numbers greater than zero.
''')
st.header('3. Task completion criteria')
st.write('These requirements apply to every submitted task by default:')
for i, rule in enumerate(COMPLETION_DEFAULTS, 1):
    st.write(f'{i}. {rule}')
st.markdown('''
**Additional client requirements — optional:** Add completion requirements beyond the defaults. In the table, choose **All tasks** or a named task category under **Applies to**, then enter the requirement.

Example: “For all CSV-processing tasks, graders must compare the output’s actual contents against the expected results. File existence alone must not count as successful completion.”
''')
st.header('4. Scoring and evidence requirements')
st.write('These rules apply to every agent attempt by default:')
for i, rule in enumerate(SCORING_DEFAULTS, 1):
    st.write(f'{i}. {rule}')
st.markdown('''
**Additional client requirements — optional:** Specify changes to the default scoring rules or additional evidence. Choose **All tasks** (all attempts) or a named task category in the table. Changes are recorded explicitly for later review.

Example: “For database tasks, include the executed query alongside the check results.”
''')
st.header('5. Other requirements or concerns')
st.write('Optional. Describe additional requirements, restrictions, priorities, or questions not covered above. Examples include prohibited data, source restrictions, delivery deadlines, reporting preferences, or known quality concerns.')
st.info('Examples are guidance, not prefilled customer requirements. Saving captures your input and the stated defaults; approval and dataset evaluation happen in later stages.')
