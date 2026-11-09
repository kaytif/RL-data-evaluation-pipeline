import hashlib
import json
from sqlalchemy import select
from .models import Project, Specification, Requirement, Submission, Run, Finding, Revision, Approval, Release, ModelCall
from .adapter import FakeAdapter, DraftInput
from .validation import execute, digest, correct_output, FIXTURE

class GateError(ValueError):
    pass

class Workflow:
    """Caller owns transaction. Lock project rows to serialize Streamlit reruns."""
    def __init__(self, session, adapter=None):
        self.s = session
        self.adapter = adapter or FakeAdapter()

    def project(self, pid):
        p = self.s.scalar(select(Project).where(Project.id == pid).with_for_update())
        if p is None:
            raise GateError('Unknown project')
        return p

    def spec(self, pid):
        return self.s.scalar(select(Specification).where(Specification.project_id == pid))

    def latest(self, pid):
        return self.s.scalar(select(Submission).where(Submission.project_id == pid).order_by(Submission.version.desc()))

    def human(self, actor, rationale):
        if not actor.strip() or not rationale.strip():
            raise GateError('Reviewer identity and rationale are required')

    def create(self, brief):
        draft = self.adapter.draft(DraftInput(brief=brief))
        p = Project(brief=brief)
        self.s.add(p); self.s.flush()
        spec = Specification(project_id=p.id)
        self.s.add(spec); self.s.flush()
        self.s.add(Requirement(specification_id=spec.id, code='CSV-001', description=draft.requirement,
            source=brief, criteria={'all_probes_must_match': True, 'clarification': draft.clarification}))
        self.s.add(ModelCall(project_id=p.id, payload={'adapter': 'fake', 'simulated': True,
            'instructions': FakeAdapter.instructions, 'input': brief, 'output': draft.model_dump(),
            'tool_calls': 0, 'retries': 0, 'tokens': 0}))
        return p.id

    def approve_spec(self, pid, actor, rationale):
        p = self.project(pid); self.human(actor, rationale)
        if p.state != 'awaiting_specification_approval':
            raise GateError('Specification is already approved or workflow advanced')
        self.s.add(Approval(specification_id=self.spec(pid).id, kind='specification', actor=actor, rationale=rationale))
        p.state = 'submission_received'

    def load(self, pid, version):
        p = self.project(pid)
        if p.state == 'cancelled':
            raise GateError('Project is cancelled')
        spec = self.spec(pid)
        if not self.s.scalar(select(Approval).where(Approval.specification_id == spec.id, Approval.kind == 'specification')):
            raise GateError('Approve specification first')
        previous = self.latest(pid)
        if previous and previous.version == version:
            return previous.id
        if version not in (1, 2) or (version == 1 and previous) or (version == 2 and p.state != 'revisions_requested'):
            raise GateError('Load version 1 first; version 2 requires a reviewed revision request')
        sub = Submission(project_id=pid, specification_id=spec.id, version=version, fixture_hash=digest())
        self.s.add(sub); self.s.flush()
        p.state = 'submission_received'
        return sub.id

    def validate(self, pid):
        p = self.project(pid); sub = self.latest(pid)
        if p.state == 'cancelled':
            raise GateError('Project is cancelled')
        if sub is None:
            raise GateError('Load a submission first')
        existing = self.s.scalar(select(Run).where(Run.submission_id == sub.id).order_by(Run.id.desc()))
        if existing and not (p.state == 'submission_received' and existing.status in ('failed', 'interrupted') and 'error' in existing.evidence):
            return existing.id
        if p.state != 'submission_received':
            raise GateError('Submission is not ready')
        run = Run(submission_id=sub.id, status='running', evidence={})
        self.s.add(run); self.s.flush()
        p.state = 'validation_running'
        try:
            if digest() != sub.fixture_hash:
                raise GateError('Bundled fixture changed after submission load')
            req = self.s.scalar(select(Requirement).where(Requirement.specification_id == sub.specification_id))
            if req.criteria.get('all_probes_must_match') is not True:
                raise GateError('Unsupported acceptance baseline')
            evidence = execute(sub.version)
            run.evidence = evidence
            failed = [c for c in evidence['cases'] if c['expected'] != c['actual']]
            run.status = 'failed' if failed else 'passed'
            if failed:
                self.s.add(Finding(run_id=run.id, requirement_id=req.id, details={
                    'type': 'grader_false_positive', 'severity': 'blocking', 'measured': True,
                    'task_id': sub.task_id, 'task_version': sub.version, 'failed_probes': failed,
                    'action': 'Replace unconditional pass with exact CSV content validation.',
                    'reproduce': evidence['command']}))
                p.state = 'findings_awaiting_review'
            else:
                for revision in self.s.scalars(select(Revision).join(Finding).join(Run).join(Submission).where(Submission.project_id == pid)):
                    revision.status = 'resolved'; revision.recheck_run_id = run.id
                p.state = 'awaiting_release_approval'
        except (Exception, KeyboardInterrupt) as exc:
            run.status = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed'
            run.evidence = {'error': type(exc).__name__, 'message': str(exc)}
            p.state = 'failed'
        return run.id

    def retry(self, pid):
        p = self.project(pid)
        if p.state != 'failed':
            raise GateError('Only failed executions can be retried')
        sub = self.latest(pid)
        run = self.s.scalar(select(Run).where(Run.submission_id == sub.id).order_by(Run.id.desc()))
        # Keep prior runs and record why a new attempt was started.
        self.s.add(ModelCall(project_id=pid, payload={'type': 'execution_retry', 'run_id': run.id,
            'previous_status': run.status, 'previous_evidence': run.evidence}))
        p.state = 'submission_received'
        return self.validate(pid)

    def cancel(self, pid, actor, rationale):
        p = self.project(pid); self.human(actor, rationale)
        if p.state == 'approved_export':
            raise GateError('Cannot cancel a released project')
        if p.state == 'cancelled':
            return
        self.s.add(Approval(specification_id=self.spec(pid).id, kind='cancellation', actor=actor, rationale=rationale))
        p.state = 'cancelled'

    def request_revision(self, pid, actor, rationale):
        p = self.project(pid); self.human(actor, rationale)
        if p.state != 'findings_awaiting_review':
            raise GateError('Review requires pending findings')
        sub = self.latest(pid)
        run = self.s.scalar(select(Run).where(Run.submission_id == sub.id).order_by(Run.id.desc()))
        for f in self.s.scalars(select(Finding).where(Finding.run_id == run.id)):
            f.decision = 'confirmed'
            self.s.add(Revision(finding_id=f.id, owner='simulated vendor', request=f.details['action'] + ' Verify using python -m pipeline.validation 2.'))
        self.s.add(Approval(specification_id=sub.specification_id, submission_id=sub.id, kind='finding_review', actor=actor, rationale=rationale))
        p.state = 'revisions_requested'

    def report(self, pid):
        sub = self.latest(pid)
        if sub is None:
            raise GateError('No submission')
        run = self.s.scalar(select(Run).where(Run.submission_id == sub.id).order_by(Run.id.desc()))
        if run is None or run.status != 'passed' or digest() != sub.fixture_hash:
            raise GateError('Current submission needs a successful recheck')
        req = self.s.scalar(select(Requirement).where(Requirement.specification_id == sub.specification_id))
        history = []
        for finding, previous_run, previous_submission in self.s.execute(
                select(Finding, Run, Submission).join(Run, Finding.run_id == Run.id)
                .join(Submission, Run.submission_id == Submission.id).where(Submission.project_id == pid)):
            revision = self.s.scalar(select(Revision).where(Revision.finding_id == finding.id))
            history.append({'finding_id': finding.id, 'requirement_id': finding.requirement_id,
                'submission_version': previous_submission.version, 'run_id': previous_run.id,
                'details': finding.details, 'review_decision': finding.decision,
                'evidence': previous_run.evidence,
                'revision': {'id': revision.id, 'owner': revision.owner, 'request': revision.request,
                    'status': revision.status, 'recheck_run_id': revision.recheck_run_id} if revision else None})
        approvals = [{'id': a.id, 'kind': a.kind, 'actor': a.actor, 'rationale': a.rationale,
                      'submission_id': a.submission_id, 'timestamp': a.timestamp}
                     for a in self.s.scalars(select(Approval).where(Approval.specification_id == sub.specification_id))]
        return {'project_id': pid, 'specification_id': sub.specification_id, 'specification_version': 1,
            'submission_id': sub.id, 'submission_version': sub.version, 'task_id': sub.task_id,
            'task_version': sub.version, 'requirements': [{'id': req.id, 'code': req.code,
                'description': req.description, 'source': req.source, 'criteria': req.criteria,
                'status': 'passed', 'run_id': run.id}],
            'finding_and_revision_history': history, 'prior_human_decisions': approvals,
            'evidence': run.evidence, 'simulated_model': True,
            'limitations': 'Bundled trusted fixtures only; finite probes; no model capability or training-gain claim. Independent portfolio demo, no HUD affiliation.'}

    def release(self, pid, actor, rationale):
        p = self.project(pid); self.human(actor, rationale)
        sub = self.latest(pid)
        if sub:
            old = self.s.scalar(select(Release).where(Release.submission_id == sub.id))
            if old:
                return old.id
        if p.state != 'awaiting_release_approval':
            raise GateError('Release requires successful checks and a human approval')
        report = self.report(pid)
        approval = Approval(specification_id=sub.specification_id, submission_id=sub.id, kind='release', actor=actor, rationale=rationale)
        self.s.add(approval); self.s.flush()
        report['approval'] = {'id': approval.id, 'actor': actor, 'rationale': rationale, 'timestamp': approval.timestamp}
        files = {'report.json': json.dumps(report, indent=2), 'fixtures/transactions.csv': FIXTURE.read_text(),
                 'reference.csv': correct_output(), 'pipeline/__init__.py': '',
                 'pipeline/validation.py': __import__('inspect').getsource(__import__('pipeline.validation', fromlist=['grade'])),
                 'task.json': json.dumps({'id': sub.task_id, 'version': sub.version,
                     'instructions': 'Export every settled transaction to CSV with columns id,status,amount; row order is irrelevant.',
                     'category': 'finance_operations/csv_export', 'submission_id': sub.id,
                     'specification_id': sub.specification_id, 'fixture_sha256': sub.fixture_hash}),
                 'README.txt': f'From this extracted directory run: python -m pipeline.validation {sub.version}\nNo third-party packages needed for grader reproduction. Expected all seven probe results match.\n'}
        manifest = {'report': report, 'files': files, 'sha256': {k: hashlib.sha256(v.encode()).hexdigest() for k,v in files.items()}}
        release = Release(submission_id=sub.id, approval_id=approval.id, manifest=manifest)
        self.s.add(release); self.s.flush(); p.state = 'approved_export'
        return release.id

    def export(self, pid):
        import io, zipfile
        self.project(pid)
        sub = self.latest(pid)
        release = self.s.scalar(select(Release).where(Release.submission_id == sub.id)) if sub else None
        if release is None:
            raise GateError('Human-approved release required')
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as archive:
            for name, content in release.manifest['files'].items():
                archive.writestr(name, content)
            archive.writestr('manifest.json', json.dumps({k:v for k,v in release.manifest.items() if k != 'files'}, indent=2))
        return out.getvalue()
