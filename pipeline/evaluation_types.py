"""Structured evaluation outputs; persisted in existing JSON columns."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Output(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Check(Output):
    requirement_id: str
    applicable: bool
    status: Literal['pass', 'fail', 'unverified']
    expected: str
    actual: str | None
    reason: str = Field(min_length=1)
    suggested_fix: str


class CheckSet(Output):
    checks: list[Check] = Field(min_length=1)


class InstructionConcern(Output):
    requirement_id: str
    status: Literal['fail', 'unverified']
    issue: str
    suggested_fix: str


class InstructionReview(Output):
    summary: str
    concerns: list[InstructionConcern]


def validate_checks(result, requirements):
    expected = {r['requirement_id'] for r in requirements}
    actual = [c.requirement_id for c in result.checks]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError('Evaluator did not return exactly one check for each applicable requirement.')
    mandatory = {r['requirement_id'] for r in requirements if r.get('mandatory')}
    if any(not c.applicable and c.requirement_id in mandatory for c in result.checks):
        raise ValueError('Evaluator skipped a mandatory completion check.')
    unknown = {r['requirement_id'] for r in requirements if r.get('scope_unknown')}
    for c in result.checks:
        if c.applicable and c.status == 'pass' and c.actual is None:
            c.status = 'unverified'
            c.reason = 'No actual-result evidence was returned. ' + c.reason
            c.suggested_fix = 'Supply observable actual-result evidence for this check.'
        if c.requirement_id in unknown:
            c.applicable = True
            c.status = 'unverified'
            c.reason = 'Category applicability is unknown. ' + c.reason
            c.suggested_fix = 'Resolve the task category before applying this scoped requirement.'
        if any(r.get('excluded') and r['requirement_id'] == c.requirement_id for r in requirements):
            c.applicable = False
    return result


def reward_from_checks(checks):
    relevant = [c for c in checks if c.applicable]
    if any(c.status == 'fail' for c in relevant):
        return 0
    if relevant and all(c.status == 'pass' for c in relevant):
        return 1
    return None


def requirements(snapshot, *, task_level=False, criteria=(), category=None):
    rows = []
    def add(id, text, mandatory=False):
        rows.append(dict(requirement_id=id, rule=text, mandatory=mandatory))
    if task_level:
        add('USE-001', snapshot.get('required_capabilities', ''), True)
        add('SCOPE-001', snapshot.get('outside_scope', ''), True)
        for i, text in enumerate(snapshot.get('completion_defaults', []), 1):
            add(f'COMP-{i:03}', text)
        if snapshot.get('comments'):
            add('OTHER-001', snapshot['comments'])
    else:
        for i, text in enumerate(criteria, 1):
            add(f'CRIT-{i:03}', text, True)
    fields = [('completion_requirements', 'COMP-EXTRA')]
    if not task_level:
        fields += [('scoring_requirements', 'SCORE-EXTRA')]
    for field, prefix in fields:
        for i, row in enumerate(snapshot.get(field, []), 1):
            if row['scope'] == 'All tasks' or row['scope'] == category:
                add(f'{prefix}-{i:03}', row['requirement'], row['scope'] == 'All tasks' or category is not None)
            elif category is None:
                add(f'{prefix}-{i:03}', f"Category applicability is unknown. Determine whether {row['scope']} applies: {row['requirement']}")
                rows[-1]['scope_unknown'] = True
    return rows


def package_requirements(snapshot, category):
    rows = requirements(snapshot, task_level=True)
    if category is not None:
        scopes = {f'COMP-EXTRA-{i:03}': r['scope'] for i, r in enumerate(snapshot.get('completion_requirements', []), 1)}
        for row in rows:
            if row['requirement_id'] in scopes:
                row.pop('scope_unknown', None)
                row['excluded'] = scopes[row['requirement_id']] not in ('All tasks', category)
                row['mandatory'] = not row['excluded']
    return rows
