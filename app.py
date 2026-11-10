import streamlit as st

st.set_page_config(page_title='Rubric', layout='wide')
page = st.navigation([
    st.Page('app_pages/rubric.py', title='Rubric', icon=':material/checklist:', default=True,
            help='Define and save the evaluation rubric, with the guide included on the same page.'),
    st.Page('pages/2_Upload_submission.py', title='Upload', icon=':material/upload_file:', url_path='Upload_submission',
            help='Upload and inspect task CSV files for a saved rubric.'),
    st.Page('pages/3_Assess_tasks.py', title='Assessment', icon=':material/analytics:', url_path='Assess_tasks',
            help='Review data quality, task coverage, rewards and exports.'),
])
page.run()
