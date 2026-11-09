"""Client-supplied post-training rubric and persisted default rules."""
from pydantic import BaseModel, ConfigDict, Field, model_validator

COMPLETION_DEFAULTS = [
    'Each task must specify its required result. Instructions must describe the expected output or final state clearly enough to determine whether an attempt succeeded.',
    'Each task must have verifiable completion checks. The vendor must supply a grader and either reference results or a reproducible method for determining correctness.',
    'Each task must define relevant special-case behavior. If empty, missing, or invalid inputs can occur, instructions must specify the required response.',
]
SCORING_DEFAULTS = [
    'Assign one score per attempt: 1 if every applicable completion check passes; 0 if any applicable check fails. No partial credit. If a check cannot be completed and no completed check has failed, mark the attempt unverified rather than assuming it passed or failed.',
    'Record every check result: check name, expected result, actual result, and pass/fail status. For checks that cannot be completed, record unverified and explain why.',
]

class CategoryQuantity(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    category: str = Field(min_length=1, max_length=200)
    minimum_tasks: int = Field(strict=True, ge=1, le=1000000)
    attempts_per_task: int = Field(strict=True, ge=1, le=1000000)

class ScopedRequirement(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    scope: str = Field(min_length=1, max_length=200)
    requirement: str = Field(min_length=1, max_length=20000)

class RequirementsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    client_name: str = Field(min_length=1, max_length=200)
    task_name: str = Field(min_length=1, max_length=200)
    required_capabilities: str = Field(min_length=1, max_length=20000)
    outside_scope: str = Field(min_length=1, max_length=20000)
    quantities: list[CategoryQuantity] = Field(min_length=1, max_length=100)
    completion_requirements: list[ScopedRequirement] = Field(default_factory=list, max_length=100)
    scoring_requirements: list[ScopedRequirement] = Field(default_factory=list, max_length=100)
    comments: str = Field(default='', max_length=20000)

    @model_validator(mode='after')
    def check_categories_and_scopes(self):
        names = [row.category.casefold() for row in self.quantities]
        if len(set(names)) != len(names):
            raise ValueError('Task categories must be unique (ignoring capitalization).')
        if 'all tasks' in names:
            raise ValueError('All tasks is reserved for requirements applying to every category.')
        allowed = {'All tasks', *(row.category for row in self.quantities)}
        for row in self.completion_requirements + self.scoring_requirements:
            if row.scope not in allowed:
                raise ValueError(f'Unknown category scope: {row.scope}. Choose All tasks or a listed category.')
        return self

    @property
    def totals(self):
        return {'minimum_distinct_tasks': sum(r.minimum_tasks for r in self.quantities),
                'minimum_attempts': sum(r.minimum_tasks * r.attempts_per_task for r in self.quantities)}

    def snapshot(self):
        return {**self.model_dump(), 'rubric_version': 2, 'intended_use': 'reinforcement-learning post-training',
                'totals': self.totals, 'completion_defaults': list(COMPLETION_DEFAULTS),
                'scoring_defaults': list(SCORING_DEFAULTS),
                'category_rule': 'Each task must belong to exactly one permitted category; each execution must have a unique attempt ID.'}
