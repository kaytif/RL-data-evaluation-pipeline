"""Shared presentation for the dataset review workspace."""
import streamlit as st


def workspace(step):
    st.html('''<style>
    .workflow {display:flex;gap:8px;margin:0 0 28px;flex-wrap:wrap;}
    .workflow span {padding:8px 14px;border-radius:8px;font-size:.85rem;background:rgba(128,128,128,.08);}
    .workflow .active {background:#dcebe5;color:#174f3c;font-weight:650;}
    .st-key-task_workspace {background:#ffffff;border:1px solid #d8e4de;border-top:4px solid #23735b;border-radius:14px;padding:1.25rem;}
    .st-key-readiness_panel {background:#f0f6fb;border-radius:14px;padding:1.1rem;}
    .st-key-calibration_panel {background:#f5f2fa;border-radius:14px;padding:1.1rem;}
    .st-key-export_panel {background:#edf5ef;border-radius:14px;padding:1.1rem;}
    </style>''')
    labels = ['Rubric', 'Upload', 'Assessment']
    st.caption('DATASET REVIEW WORKSPACE')
    st.markdown('<div class="workflow">' + ''.join(
        f'<span class="{"active" if i == step else ""}">{i + 1}. {label}</span>'
        for i, label in enumerate(labels)) + '</div>', unsafe_allow_html=True)


def section(number, title, color='green'):
    st.header(f'{number}. {title}', divider=color)


def result_badge(status, label=None):
    colors = {'pass': 'green', 'success': 'green', 'fail': 'red', 'failure': 'red', 'unverified': 'orange'}
    names = {'pass': 'Pass', 'success': 'Reward 1', 'fail': 'Fail', 'failure': 'Reward 0', 'unverified': 'Needs review'}
    st.badge(label or names.get(status, status.capitalize()), color=colors.get(status, 'gray'))
