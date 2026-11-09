# Project Brief: Supervised RL Data Acceptance Pipeline

## Agreed local-demo simplification

The user requested a working demo without Docker or authentication setup. The first milestone now defaults to a local SQLite file with automatic schema creation and a one-command launcher. PostgreSQL, SQLAlchemy and Alembic support remain available as an optional path. Human specification/review/release gates remain enforced in application code. This is a single-reviewer local demo.

## Agreed staged implementation: client requirements first

The main app now starts with client requirements intake. Stage one captures client name and generated ID, task name and generated ID, and five required text categories: intended use; task coverage; record completeness; outcome and grading evidence; data integrity and restrictions. Clients can have multiple tasks. Records can be reopened and edited, with saved revision snapshots and stable IDs. This stage stores requirements without generating or approving an acceptance baseline. Upload and evaluation are subsequent stages. The earlier bundled grader demo is preserved in `legacy_demo.py`.

The user subsequently replaced the five free-text categories with the RL Post-Training Data rubric: required capabilities and excluded activities; structured category quantities with calculated task/attempt totals; default completion criteria plus optional scoped additions; default scoring/evidence rules plus optional scoped changes; and optional other concerns. A separate reference guide presents the definitions and examples. Defaults are saved alongside client input and prior-form records are preserved. This supersedes the original five required text fields in the intake UI.

Step two adds machine-readable requirements CSV/JSON export and vendor dataset import. Accept multiple CSV, JSON-array or JSONL files for tasks, attempts and completion checks, with bounded local ingestion (50 MiB combined, 200,000 records). Bind each submission to an exact saved requirements revision and preserve original artifacts, checksums, normalized records and assessment evidence. Automatic checks cover structure, quantities, references and reported-score consistency; grader correctness, capability and prose requirements remain human review. No uploaded code runs, and import is not acceptance or release approval.

Correction from the user: the immediate step two is **bulk upload only**, with **each CSV file representing one task**, potentially thousands of files. The active upload page therefore accepts arbitrary CSV columns, preserves each original file, assigns one data-task ID per file, links batches to saved client requirements, and offers paginated browsing and download. It does not require separate task/attempt/check files or run assessments. The prior structured import UI is retained only as `legacy_import_page.py`; its assessment code is not the active workflow. Bulk limits are 10,000 files, 50 MiB per file and 500 MiB per batch. This correction supersedes the preceding step-two design.

Step three requested by the user: embed an assessment agent that organises each CSV's attempts, uses section 3 as its blueprint, and produces a dataset with success/failure scores and reasons. The initial no-key adapter remains deterministic Python: inspect recognised columns, group attempt IDs, compare recorded expected/actual outputs, preserve unverified outcomes when evidence or prose interpretation is insufficient, and log grouping/evidence decisions. Outputs are proposals until human export approval. Arbitrary natural-language reasoning and unfamiliar schema interpretation require a future real model adapter; no model capability is claimed for local rules.

## 1. Why I am building this

I am applying for the Delivery Engineer role at HUD.

I am proficient in Go backend development and Python. This project will
use Python throughout.

I want to demonstrate that I can take an AI lab's requirements, assess
a vendor's RL task submission against them, coordinate revisions, and
deliver a reliable package with evidence supporting its acceptance.

This should be a working engineering project, not just a chatbot or
a collection of agent prompts.

This is an independent portfolio project. Do not imply affiliation
with HUD, access to its internal systems, or real customer activity.

## 2. Company context

Based on the supplied job description and product information:

HUD builds infrastructure for creating, running, inspecting, reviewing,
and quality-checking environments used to evaluate and train AI agents.

Its platform includes:
- Executable environments and tasks.
- Evaluation runs and execution traces.
- Task versioning and team review.
- QA for grader mistakes and reward hacking.
- A marketplace connecting task/environment creators with AI labs.

In this context, RL task data can include:
- Task instructions.
- An initial environment and its tools/data.
- A grader or reward function.
- Metadata and provenance.
- Execution traces and evaluation results.

Do not assume a particular HUD API, SDK contract, or marketplace
submission format. HUD integration is outside the initial scope.

## 3. Target role: Delivery Engineer

The job description says:

"We’re looking for a Delivery Engineer to own complex, time-sensitive
data and evaluation programs for frontier AI labs, data vendors, and
internal teams."

The role turns ambiguous technical requests into executable programs,
coordinates people and dependencies, manages vendors, and ensures that
the delivered data is useful for evaluating and improving LLMs and agents.

Responsibilities from the supplied description:

- Own data and evaluation programs end to end, including scoping,
  requirements, production, QA, delivery, and retrospective.
- Translate ambiguous requests into milestones, owners, dependencies,
  and acceptance criteria.
- Maintain and improve data quality using quantitative signals and
  qualitative inspection, including coverage, difficulty, and reward
  distributions.
- Maintain delivery pipelines and manage external vendors.
- Improve workflows, documentation, automation, and handoffs.
- Turn research and delivery feedback into product improvements.

The role values:
- Ownership of complex technical programs.
- Quantitative and qualitative judgment about data.
- Process improvement.
- Sound decisions under ambiguity and changing requirements.
- Clear communication of decisions, owners, and next steps.

## 4. What this project must demonstrate

The central question is:

"Does this submitted RL task package meet this lab's requirements,
and what evidence supports that conclusion?"

Demonstrate:
1. Turning an ambiguous brief into an approved acceptance specification.
2. Checking an incoming submission against that specification.
3. Finding quality problems with reproducible evidence.
4. Distinguishing objective failures from uncertain judgments.
5. Producing actionable vendor revision requests.
6. Rechecking revised submissions.
7. Delivering a versioned package with a human-approved acceptance report.

The value is reliable acceptance decisions and efficient rework,
not the number of agents or amount of generated content.

Meeting agreed acceptance criteria is not proof that a dataset improves
a model. Actual training improvement would require a separate experiment.

## 5. Product definition

Build a supervised RL task-data acceptance pipeline.

Inputs:
- A lab's brief and intended use: evaluation, training, or both.
- An approved acceptance specification.
- A vendor submission containing task packages.

Outputs:
- Requirement-by-requirement acceptance results.
- Task-level findings with supporting evidence.
- Revision requests and their resolution status.
- An approved, versioned export.
- A quality report explaining results and limitations.

Initial users:
- Delivery owner/reviewer: me.
- Lab/customer: simulated through a supplied brief.
- Vendor: simulated through imported submissions and revisions.

External messaging is out of scope.
Generate revision requests locally rather than sending them.

## 6. Initial domain and demo dataset

Use synthetic finance-operations tasks, such as:
- Exporting filtered transaction records to CSV.
- Reconciling invoice totals.
- Calculating partial refunds according to a policy.

Start with one task type: CSV exports.

Expand to around 10 tasks once the first vertical slice works.

Each task should include:
- Stable task ID and version.
- Instructions.
- Synthetic input data and initial state.
- Expected capability/category metadata.
- Executable grader.
- Reference solution or independently verifiable expected result.
- Provenance linking it to its submission and requirements.

Deliberately include demo defects:
- A grader that accepts an empty CSV.
- A grader that rejects a valid alternative output.
- A duplicate or near-duplicate task.
- A coverage gap against the approved specification.

Treat reference solutions as evidence to validate, not automatically
infallible ground truth.

## 7. Workflow and human supervision

Use an explicit, persisted workflow:

Brief received
→ specification drafted
→ awaiting specification approval
→ submission received
→ validation running
→ findings awaiting review
→ revisions requested, if needed
→ revised submission checked
→ acceptance report prepared
→ awaiting release approval
→ approved export

Also support failed, interrupted, and cancelled runs.

Human approval is required:
- Before a proposed specification becomes the acceptance baseline.
- For ambiguous findings, exceptions, and waivers.
- Before releasing the final package.

Application code must enforce these gates.

Approval must reference the exact specification and submission version.
A changed submission must not inherit approval automatically.

For each finding, record:
- The requirement it relates to.
- Affected task and version.
- Finding type and severity.
- Evidence and reproduction details.
- Proposed action.
- Review decision and rationale.
- Revision and recheck history.

Record owners, status, and next actions for rework items.

## 8. Agent roles

Implement distinct roles incrementally.
They may share the same model and do not need separate services.

### Requirements agent
- Extract requirements from the lab brief.
- Identify ambiguity and missing information.
- Propose measurable acceptance criteria.
- Identify qualitative criteria needing human review.
- Link criteria back to the original brief.
- Never invent customer requirements or silently resolve material ambiguity.

Output:
A structured draft specification and clarification questions.

### QA agent
- Inspect submission structure.
- Select and call permitted validation tools.
- Investigate suspicious results.
- Connect findings to requirements.
- Distinguish tool-verified facts from model judgments.
- Return "needs review" when evidence is insufficient.

Output:
Structured findings, evidence references, and proposed acceptance statuses.

### Rework agent
- Convert confirmed findings into actionable revision requests.
- Explain expected behavior and how the vendor can verify a fix.
- Compare new and previous submissions.
- Request appropriate rechecks.
- Preserve unresolved issues.

Output:
Revision requests and resolution summaries.

### Delivery agent
- Assemble results against the approved specification.
- Summarize accepted, failed, waived, and unresolved items.
- Prepare the delivery manifest and quality report.
- Include limitations and relevant evidence.
- Never authorize release itself.

Output:
A proposed acceptance report and export manifest.

No autonomous agent-to-agent delegation initially.
A deterministic Python workflow coordinates role execution.

## 9. Agent implementation requirements

Each role needs:
- Explicit instructions.
- Allowed tools.
- Pydantic input/output schemas.
- Structured error handling.
- Logged model and tool calls.
- Limits on tool calls, retries, runtime, and token spending.

Use a replaceable model adapter:
- Fake adapter for reproducible local demos and tests.
- Real model adapter supporting tool calling.

Make simulated model outputs clearly identifiable.
Do not present a fake-adapter run as evidence of model capability.

Agents should inspect results and choose allowed next actions.
They must not bypass workflow rules.

Treat vendor files and traces as untrusted input, not instructions.
Do not expose secrets to task execution or let agents execute arbitrary
commands taken from submissions.

## 10. Quality checks

Implement objective checks in Python wherever possible.

### Submission integrity
- Required files and fields.
- Valid schemas.
- Unique IDs and consistent versions.
- Loadable inputs and valid references.

### Requirement coverage
- Task counts by required category.
- Missing categories.
- Required proportions where specified.
- Traceability between requirements and tasks.

### Grader reliability
Test against:
- Known-correct solutions.
- Valid alternative solutions.
- Incorrect solutions.
- Empty or missing outputs.
- Malformed outputs.
- Plausible exploit attempts.

Distinguish:
- False positives: incorrect behavior receives a pass.
- False negatives: valid behavior receives a failure.

### Reproducibility
- Repeatability from the same initial state.
- Environment reset behavior.
- Dependency and seed recording where relevant.

### Dataset usefulness
- Exact duplicates and candidate near-duplicates.
- Coverage of intended capabilities.
- Ambiguous or underspecified tasks.
- Baseline success rates and reward distributions.
- Failure patterns and possible shortcuts.

Observed difficulty depends on the tested model and configuration.
Do not label it an intrinsic task property.

Run baselines only after checking grader reliability.
Keep model, prompt, settings, and run count in the evidence.

Use qualitative inspection where objective checks are insufficient.
Do not claim that an LLM judging its own outputs proves quality.

Acceptance thresholds must come from the approved specification,
not arbitrary constants buried in code.

## 11. Architecture and stack

Use:
- Python.
- Streamlit for the review interface.
- Pydantic for structured data and agent outputs.
- PostgreSQL for persisted state and audit history.
- SQLAlchemy for database access.
- Alembic for migrations.
- Docker Compose for local PostgreSQL.
- pytest for meaningful tests.
- pandas only where useful for analysis.

No FastAPI initially.
Streamlit calls application services directly.

Keep workflow, agents, validation, and persistence separate from the UI.

Use ordinary relational tables for entities and relationships.
Use JSONB for flexible metadata and structured evidence where appropriate.

Store large files and traces as local artifacts initially, with database
references and checksums.

Do not add a distributed queue, multiple services, or an agent framework
unless a concrete requirement justifies it.

Streamlit reruns must not duplicate jobs or lose workflow state.

## 12. Core records

Use these as a starting point:
- Lab brief.
- Acceptance specification and version.
- Requirement.
- Submission and version.
- Task and version.
- Validation run.
- Baseline execution and trace.
- Finding and evidence.
- Revision request.
- Human review/approval.
- Release and export manifest.

Preserve enough history to explain:
"What changed, why, who approved it, and which evidence was used?"

## 13. Execution boundary

Vendor-provided graders are executable code.

For the first vertical slice:
- Use only bundled, trusted synthetic fixtures.
- Do not execute arbitrary uploaded Python.
- Apply execution timeouts.

Before supporting untrusted executable submissions:
- Define and implement an appropriate isolated runner.
- Restrict credentials, network, filesystem access, and resources.
- Document the actual isolation guarantees.

A subprocess alone must not be described as a security sandbox.

## 14. First milestone

Build one complete local vertical slice:

1. Enter a fictional lab brief.
2. Draft and approve acceptance requirements.
3. Load one bundled CSV-export task.
4. Run a correct solution and an empty-output negative test.
5. Detect that the deliberately flawed grader accepts the empty output.
6. Show the finding and executable evidence in Streamlit.
7. Record my review and generate a revision request.
8. Load the corrected task version and rerun checks.
9. Prepare an acceptance report.
10. Require my approval before exporting.

Use the fake model adapter initially.
The first milestone must run without model API credentials.

## 15. Later milestones

After the vertical slice works:
- Add the real model adapter.
- Expand to approximately 10 tasks and more defect types.
- Add coverage and duplicate analysis.
- Add baseline agent runs and qualitative sampling.
- Demonstrate a requirement change and impact analysis.
- Invalidate affected acceptance results and rerun relevant checks.
- Add more realistic vendor imports only after execution boundaries exist.

## 16. Definition of done

The portfolio demo must show:
- A successful accepted delivery.
- A quality failure with evidence and a revision cycle.
- A requirement change affecting prior results.
- Persisted state across application restarts.
- Enforced approval gates.
- Reproducible checks.
- Clear separation of measured results and model judgments.

Provide:
- Working local application.
- Synthetic sample brief and submissions.
- Setup and demo instructions.
- Architecture explanation.
- Example acceptance report and export.
- Tests for critical workflow and grading behavior.
- Honest limitations.

Do not claim real vendor coordination, HUD integration, production scale,
or demonstrated training gains unless actually implemented and measured.

## 17. How Codex should work with me

Inspect the repository and applicable instructions first.

Before implementing, briefly describe:
- Your understanding of the goal.
- The proposed architecture.
- First-milestone acceptance criteria.
- Any material assumptions.

Then proceed incrementally with the first milestone.

Use sensible defaults for reversible implementation choices.
Ask only about decisions that materially affect scope or correctness.

Prioritize a working end-to-end slice over building all agent roles first.

Explain important choices so I can defend them in an interview.
Run relevant tests and report what was actually verified.

Keep this brief updated when we agree on material scope changes.
