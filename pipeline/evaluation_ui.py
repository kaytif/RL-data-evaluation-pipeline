"""Optional readiness/calibration panels and read-only evaluation report views."""
import json
import streamlit as st
from sqlalchemy import select
from .assessment_agent import AssessmentJob, llm_job_filter
from .ui import result_badge
from .evaluation_reports import run_readiness, compare_reports, report_markdown, issues_csv


def report_table(checks, key, section=None):
    from .assessment_summary import summary_rows
    if section is None:
        section = st.selectbox('Report section', ['Data quality', 'Task coverage'], key='report_section_' + key)
    include_pass = st.toggle('Include passing checks', value=False, key='report_pass_' + key)
    groups = summary_rows(checks, section, include_pass)
    if not groups:
        st.caption('No open findings.' if checks else 'Not assessed yet.')
        return
    labels = {'pass': 'Pass', 'fail': 'Fail', 'unverified': 'Needs review'}
    st.dataframe([{'Result': labels[g['status']], 'Finding': g['finding'], 'Affected': g['affected'],
                   'Next step': g['next_step']} for g in groups], hide_index=True, width='stretch',
                 alt=section + ' findings in plain language')
    records = [r for g in groups for r in g['records'] if r['file'] != 'Dataset']
    if records:
        with st.expander('Affected records'):
            page = 1
            if len(records) > 50:
                page = int(st.number_input('Records page', min_value=1, max_value=(len(records)+49)//50,
                    value=1, key='records_page_' + key + section))
            shown = records[(page-1)*50:page*50]
            st.dataframe([{'File': r['file'], 'Rows': ', '.join(map(str,r['rows'])), 'Finding': r['finding'],
                'Change': r['change']} for r in shown], hide_index=True, width='stretch', alt='Affected source records')


def render_quality_review(factory, batch_id, api_key, model, ready, final_report=None, file_id=None):
    with st.container(key='readiness_panel'):
        with st.container(horizontal=True):
            quality = st.button('Inspect data quality', key='quality_' + batch_id)
            semantic = st.button('Check quality & coverage', disabled=not ready, key='readiness_' + batch_id)
        if quality or semantic:
            try:
                judge = None
                if semantic:
                    from .llm_assessment import OpenAIJudge
                    judge = OpenAIJudge(api_key=api_key.strip(), model=model.strip())
                    judge.test_connection()
                with st.spinner('Checking dataset...'):
                    with factory.begin() as session:
                        run_readiness(session, batch_id, judge=judge, file_id=file_id)
                st.success('Checks saved.')
            except (ValueError, RuntimeError) as exc:
                st.error(str(exc))
        with factory() as session:
            saved = list(session.scalars(select(AssessmentJob).where(AssessmentJob.batch_id == batch_id,
                AssessmentJob.adapter.like('readiness:%')).order_by(AssessmentJob.created_at.desc())))
        saved = [j for j in saved if j.summary['report'].get('source_file_id') == file_id]
        structural = next((j.summary['report'] for j in saved if not j.summary['report'].get('llm_reviewed')), None)
        semantic_report = final_report or next((j.summary['report'] for j in saved if j.summary['report'].get('llm_reviewed')), None)
        source = structural or semantic_report
        checks = []
        if source:
            checks += [c for c in source['checks'] if c['phase'] == 'Data quality']
        if semantic_report:
            checks += [c for c in semantic_report['checks'] if c['phase'] != 'Data quality']
        elif structural:
            checks += [c for c in structural['checks'] if c['phase'] != 'Data quality']
        if source and source.get('quality'):
            quality_stats = source['quality']
            missing_files = {c['source_file'] for c in checks if c['requirement_id']=='MISSING-DATA' and c['status']!='pass'}
            cols = st.columns(3)
            cols[0].metric('Records checked', quality_stats['data_rows'], border=True)
            cols[1].metric('Empty fields', quality_stats['empty_cells'], border=True)
            cols[2].metric('Files with missing data', len(missing_files), border=True)
        report_table(checks, 'quality_' + batch_id, section='Data quality')
        if source:
            st.download_button('Download readiness report JSON', json.dumps(source, indent=2),
                f'{batch_id}-readiness.json', 'application/json', key='readiness_download_' + batch_id)
        return checks


def render_calibration(factory, batch_id, api_key, model, ready):
    with st.container(key='calibration_panel'):
        st.subheader('Grader calibration')
        from .calibration import starter_cases, run_calibration
        st.caption('Optional. Confirm the reference labels before checking the grader against them.')
        with st.expander('Review or edit reference cases'):
            proposed = st.text_area('Calibration reference cases (JSON)', value=json.dumps(starter_cases(), indent=2),
                height=200, key='calibration_cases_' + batch_id,
                help='2–30 cases. Each needs id, task, candidate, reference_answer, criteria, expected_reward (0, 1 or null), and reviewer_reason. Expected labels and reviewer reasons are withheld from the evaluator.')
        reviewer = st.text_input('Calibration reviewer name', key='calibration_reviewer_' + batch_id)
        confirmed = st.checkbox('I reviewed the calibration tasks and reference rewards and confirm these labels.', key='calibration_confirm_' + batch_id)
        if st.button('Evaluate grader on reference cases', disabled=not (ready and reviewer.strip() and confirmed), key='calibrate_' + batch_id):
            try:
                raw = json.loads(proposed)
                from .llm_assessment import OpenAIJudge
                judge = OpenAIJudge(api_key=api_key.strip(), model=model.strip())
                judge.test_connection()
                with st.spinner('Evaluating the reviewed calibration set...'):
                    with factory.begin() as session:
                        run_calibration(session, batch_id, judge, raw, reviewer, confirmed=True)
                st.success('Calibration saved for subsequent assessments with this model.')
            except (ValueError, RuntimeError) as exc:
                st.error(str(exc))
        with factory() as session:
            calibrations = list(session.scalars(select(AssessmentJob).where(AssessmentJob.batch_id == batch_id,
                AssessmentJob.adapter.like('calibration:%')).order_by(AssessmentJob.created_at.desc())))
        if calibrations:
            by_id = {j.id: j for j in calibrations}
            chosen = st.selectbox('Saved calibration', list(by_id), key='calibration_view_' + batch_id,
                format_func=lambda key: f'{by_id[key].created_at[:16]} | {by_id[key].adapter}')
            report = by_id[chosen].summary['report']
            left, right = st.columns(2)
            left.metric('Reference agreement', f"{report['agreement']:.1%}")
            right.metric('Binary reference accuracy', f"{report['binary_accuracy']:.1%}")
            st.write(f"False passes: {report['false_passes']} | False failures: {report['false_failures']} | Unresolved/disagreements: {report['unresolved']}")
            st.dataframe(report['cases'], hide_index=True, width='stretch', alt='Calibration reference rewards compared with independently assessed rewards')
            with st.expander('Calibration limits'):
                st.caption(report['limitations'])


def render_final_report(job, downloads=True):
    report = job.summary.get('report')
    if not report:
        st.caption('This earlier assessment has no evaluation report. A new run will include current checks.')
        return
    result_badge(report['overall'], 'Dataset compliance: ' + report['overall'].capitalize())
    st.caption('Dataset compliance is separate from candidate success; valid reward-0 examples are not automatically dataset defects.')
    rewards = report['rewards']
    success = rewards['success_rate_verified']
    rate, coverage = st.columns(2)
    rate.metric('Success among scored attempts', f'{success:.1%}' if success is not None else 'Unverified', border=True)
    coverage.metric('Verified coverage', f"{rewards['verified_coverage']:.1%}", border=True)
    if report.get('rewards_by_category'):
        st.dataframe([{'Category': category, **counts} for category, counts in report['rewards_by_category'].items()],
            hide_index=True, width='stretch', alt='Candidate reward distribution by task category')
    if report.get('quality'):
        quality = report['quality']
        st.caption(f"Data quality: {quality['files']} files, {quality['data_rows']} rows, {quality['empty_cells']} empty cells, {quality['exact_duplicate_rows']} exact duplicate rows. Optional empty cells are counted without automatically rejecting a record.")
    st.write('**Grader reliability**')
    if not report.get('calibration'):
        result_badge('unverified', 'Not calibrated for this run')
    if report.get('calibration'):
        calibration = report['calibration']
        st.write(f"Reference agreement: {calibration['agreement']:.1%}; false passes: {calibration['false_passes']}; false failures: {calibration['false_failures']}.")
        st.caption(calibration['limitations'])
    with st.expander('Evaluation limits'):
        st.caption(report['limitations'])
    report_table(report['checks'], 'final_' + job.id)
    if downloads and job.approval:
        st.download_button('Download final report JSON', json.dumps(report, indent=2), f'{job.id}-report.json', 'application/json')
        st.download_button('Download final report Markdown', report_markdown(report), f'{job.id}-report.md', 'text/markdown')
    return report


def render_issues(factory, job, downloads=True):
    report = job.summary.get('report')
    if not report:
        st.caption('No issues report is stored for this earlier assessment.')
        return
    st.caption('Linked to source rows; original datasets are unchanged.')
    with factory() as session:
        previous = list(session.scalars(select(AssessmentJob).where(AssessmentJob.id != job.id,
            llm_job_filter(), AssessmentJob.created_at < job.created_at,
            AssessmentJob.blueprint['task_id'].as_string() == job.blueprint.get('task_id')).order_by(AssessmentJob.created_at.desc()).limit(100)))
    previous = [p for p in previous if p.blueprint == job.blueprint and p.summary.get('report')]
    baseline = None
    if previous:
        baseline = st.selectbox('Compare with an earlier assessment', [None, *[p.id for p in previous]], key='baseline_' + job.id,
            format_func=lambda id: 'No comparison' if id is None else next(f'{p.created_at[:16]} | {p.batch_id}' for p in previous if p.id == id))
    issues = report['issues']
    if baseline:
        old = next(p for p in previous if p.id == baseline)
        try:
            issues = compare_reports(report, old.summary['report'])
        except ValueError as exc:
            st.warning(str(exc))
            return
        st.caption('Matching checks use the same requirements. Missing records stay Not reassessed; keep filenames and execution IDs stable.')
    report_table(issues, 'issues_' + job.id + str(baseline))
    if downloads and job.approval:
        st.download_button('Download issues & fixes CSV', issues_csv(issues), f'{job.id}-issues.csv', 'text/csv')
    return issues


def render_file_coverage(factory, batch_id, blueprint, job, api_key, model, ready, file_id=None):
    from collections import Counter
    from .csv_uploads import UploadedTaskCSV
    from .evaluation_reports import file_quantity_checks
    with factory() as session:
        files = list(session.scalars(select(UploadedTaskCSV).where(UploadedTaskCSV.batch_id == batch_id).order_by(UploadedTaskCSV.filename, UploadedTaskCSV.id)))
        saved = list(session.scalars(select(AssessmentJob).where(AssessmentJob.batch_id == batch_id,
            AssessmentJob.adapter.like('readiness:%')).order_by(AssessmentJob.created_at.desc())))
    if not files:
        return
    names = Counter(f.filename for f in files)
    labels = {f.id: f.filename + (f' (file {i + 1})' if names[f.filename] > 1 else '') for i, f in enumerate(files)}
    chosen = file_id or st.selectbox('Coverage file', list(labels), format_func=labels.get, key='coverage_file_' + batch_id)
    file = next(f for f in files if f.id == chosen)
    st.caption('Only this file is checked against the saved rubric; other files contribute no tasks or attempts.')
    if st.button('Review selected file', disabled=not ready, key='file_review_' + batch_id):
        from .llm_assessment import OpenAIJudge
        try:
            with st.spinner('Checking selected file...'):
                with factory.begin() as session:
                    report_id = run_readiness(session, batch_id, judge=OpenAIJudge(api_key=api_key.strip(), model=model.strip()), file_id=chosen)
                with factory() as session:
                    saved.insert(0, session.get(AssessmentJob, report_id))
        except (ValueError, RuntimeError) as exc:
            st.error(str(exc))
    # Prefer complete observed task metadata over older or partial model reports.
    source = next((j.summary['report'] for j in saved
        if j.summary['report'].get('source_file_id') == chosen
        and chosen in j.summary['report'].get('inventoried_file_ids', [])), None)
    if source is None and job and job.summary.get('source_file_id') == chosen:
        linked = (job.summary.get('report') or {}).get('readiness_id')
        if linked:
            with factory() as session:
                linked_job = session.get(AssessmentJob, linked)
                if linked_job and linked_job.summary.get('report', {}).get('source_file_id') == chosen:
                    source = linked_job.summary['report']
    source = source or next((j.summary['report'] for j in saved
        if j.summary['report'].get('source_file_id') == chosen), None)
    inventory = (source or {}).get('inventory', [])
    ambiguous = names[file.filename] > 1
    relevant = [r for r in inventory if r.get('source_file_id') == chosen]
    complete = chosen in (source or {}).get('inventoried_file_ids', [])
    checks = file_quantity_checks(blueprint, relevant, file.filename, chosen, complete=complete, ambiguous_filename=ambiguous)
    rows = coverage_rows(blueprint, relevant, complete)
    required_rows = [r for r in rows if r['Required tasks'] > 0]
    required = sum(r['Required tasks'] for r in rows)
    found = sum(r['Distinct tasks'] for r in rows)
    covered = sum(min(r['Distinct tasks'], r['Required tasks']) for r in rows)
    met = sum(r['Threshold met'] for r in rows)
    attempts = sum(r['Tasks meeting attempt minimum'] for r in rows)
    cols = st.columns(3)
    cols[0].metric('Task coverage', f'{covered / required:.1%}' if required and complete else 'Unverified', border=True)
    cols[0].caption(f'{found:,} identified / {required:,} required tasks')
    cols[1].metric('Categories meeting minimum', f'{met / len(required_rows):.1%}' if required_rows and complete else 'Unverified', border=True)
    cols[1].caption(f'{met:,} / {len(required_rows):,} required categories')
    cols[2].metric('Tasks meeting attempt minimum', f'{attempts / found:.1%}' if found and complete else 'Unverified', border=True)
    cols[2].caption(f'{attempts:,} / {found:,} identified tasks; real execution IDs required')
    if not complete:
        st.caption('Coverage is unverified until every row in this file has been identified.')
    st.dataframe([{k: v for k, v in r.items() if k != 'Threshold met'} for r in rows], hide_index=True, width='stretch', alt='Selected file coverage against saved category and attempt thresholds')
    scoped = [c for c in (source or {}).get('checks', []) if c['phase'] == 'Task completion' and
              (c.get('source_file_id') == chosen or (not c.get('source_file_id') and not ambiguous and c.get('source_file') == file.filename))]
    report_table(checks + scoped, 'coverage_' + batch_id + chosen, section='Task coverage')


def coverage_rows(blueprint, inventory, complete):
    """Use only the supplied file inventory; never count attempts across files."""
    from .evaluation_reports import category_policy
    rows, tasks = [], {}
    for record in inventory:
        tasks.setdefault(record['task_key'], []).append(record)
    def observed_category(items):
        categories = {r['category'] for r in items}
        return next(iter(categories)) if len(categories) == 1 else None
    policies = list(blueprint.get('quantities', []))
    extra = {observed_category(items) for items in tasks.values()
             if category_policy(blueprint, observed_category(items)) is None}
    policies += [dict(category=c or 'Unclassified', minimum_tasks=0, attempts_per_task=0)
                 for c in sorted(extra, key=lambda c: c or '')]
    for policy in policies:
        matching = [items for items in tasks.values() if
            (category_policy(blueprint, observed_category(items)) or
             {'category': observed_category(items) or 'Unclassified'})['category'] == policy['category']]
        attempt_met = sum(all(r.get('execution_id') for r in items) and
            len({r['execution_id'] for r in items}) >= policy['attempts_per_task'] for items in matching) if policy['minimum_tasks'] else 0
        observed_attempts = [len({('explicit', r['execution_id']) if r.get('execution_id') else
            ('row', tuple(r['source_rows'])) for r in items}) for items in matching]
        count, minimum = len(matching), policy['minimum_tasks']
        rows.append({'Category': policy['category'],
            'Observed categories': ', '.join(sorted({r['category'] or 'Unclassified' for items in matching for r in items})),
            'Distinct tasks': count, 'Required tasks': minimum,
            'Coverage': 'No rubric target' if not minimum else f'{min(count / minimum, 1):.1%}' if complete else 'Unverified',
            'Observed attempts per task': (str(min(observed_attempts)) if min(observed_attempts) == max(observed_attempts)
                else f'{min(observed_attempts)}–{max(observed_attempts)}') if observed_attempts else '0',
            'Required attempts per task': policy['attempts_per_task'],
            'Tasks meeting attempt minimum': attempt_met, 'Threshold met': complete and minimum > 0 and count >= minimum})
    return rows
