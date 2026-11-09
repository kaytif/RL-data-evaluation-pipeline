"""Shared presentation for the dataset review workspace."""
import streamlit as st


def workspace(step):
    st.markdown('''<style>
    .block-container {max-width:1240px; padding-top:2rem; padding-bottom:4rem;}
    h1 {letter-spacing:-.045em; font-weight:750!important;}
    h2 {letter-spacing:-.025em;}
    [data-testid="stMetric"] {background:var(--secondary-background-color); padding:1rem 1.2rem; border-radius:12px;}
    [data-testid="stSidebar"] {border-right:1px solid rgba(128,128,128,.15);}
    [data-testid="stExpander"] {border-radius:12px;}
    .workflow {display:flex;gap:8px;margin:0 0 28px;flex-wrap:wrap;}
    .workflow span {padding:8px 14px;border-radius:8px;font-size:.85rem;background:rgba(128,128,128,.08);}
    .workflow .active {background:#dcebe5;color:#174f3c;font-weight:650;}
    </style>''', unsafe_allow_html=True)
    labels = ['Requirements', 'Upload dataset', 'Assess & review']
    st.caption('DATASET REVIEW WORKSPACE')
    st.markdown('<div class="workflow">' + ''.join(
        f'<span class="{"active" if i == step else ""}">{i + 1}. {label}</span>'
        for i, label in enumerate(labels)) + '</div>', unsafe_allow_html=True)
