# Client requirements workspace — stage one

Independent portfolio project using fictional lab and vendor data. No HUD integration or affiliation. No model API keys required.

## Quick start — no Docker required

Dependencies are already installed in `.venv`. From this directory run:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

Open http://127.0.0.1:8502. Keep the terminal open. The launcher clears leftover PostgreSQL settings and uses `acceptance.db` in this directory. SQLite tables are created automatically. No database password, Docker, migration command or model key is needed. There is no login.

## Stage one: enter and save requirements

1. Enter a client name, or choose an existing client to add another task.
2. Enter the task name and complete the RL post-training rubric. Specify required capabilities and excluded activities, then use the editable quantities table for permitted categories, minimum distinct tasks, and minimum attempts per task. Minimum task and attempt totals are calculated automatically.
3. Click **Save requirements**. The app generates stable `CL-…` and `TASK-…` IDs and opens the saved task.
4. Use **Open a task** in the sidebar to reopen any saved record. Edit and click **Save changes**; IDs stay unchanged and the prior text remains in **Saved revision history**.

Requirements are stored as client and task records with revision snapshots. Saving stores the client's statements; it does not approve them, invent acceptance thresholds or evaluate any data. Data upload and evaluation are later stages. The original bundled grader workflow remains available separately with `python -m streamlit run legacy_demo.py`.

The separate **Requirements guide** page contains the supplied definitions, defaults and examples. The form's guide button opens it in a new tab so you can refer to it without leaving your input. Completion defaults and binary scoring/evidence defaults are displayed and saved with every revision. Extra completion requirements and scoring changes are optional scoped tables; other comments are optional. Category names must be unique, quantities must be positive whole numbers, and additional rules must refer to All tasks or a permitted category. Earlier-form entries remain visible and earlier revisions are retained when you save a new-rubric revision.

## Step two: bulk task CSV upload

Open **Upload submission** in the sidebar, or http://127.0.0.1:8502/Upload_submission.

**One CSV file represents one task.** No specific columns, IDs, traces, or companion files are required inside your CSVs.

1. Select the client/dataset request and saved requirements revision.
2. Select multiple CSV files in the uploader (Ctrl+A selects all files in the picker).
3. Click **Upload task files**. Each original is preserved and receives its own generated data-task ID.
4. Browse saved batches with a paginated file list, inspect the first five CSV rows, or download an original file.

Supports up to **10,000 CSV files per batch**, **50 MiB per file**, and **500 MiB combined**. Send additional batches as needed. Files must be readable UTF-8 CSV. Processing streams one file at a time to disk; the browser uploader still buffers selected files, so split batches to fit available memory. CSV rows are counted without assuming whether the first row is a header. Original bytes and filenames are preserved; disk filenames are generated safely. Database records live in `acceptance.db` and originals under `artifacts/task_uploads`; back up both.

This step uploads and stores files only. Content assessment comes later. The earlier structured task/attempt/check import screen was replaced, and is retained separately in `legacy_import_page.py`.

## Step three: assess and review

Open **Assess tasks**, choose an uploaded dataset, connect your OpenAI model, and run assessment. The interface uses only the independent LLM evaluator. It has no synthetic sample loader or rules-based assessment option. Existing stored records are preserved; local-rule results are excluded from the LLM results screen.

Review reward counts, filter by reward or unresolved cases, and inspect task context beside each candidate response. Check reasoning, reference answers and source-row evidence before using **Review & export**. A reviewer name, rationale and explicit confirmation are required to approve CSV/JSONL downloads. The requirements and upload pages share a three-step workflow indicator; successful uploads select the same batch for assessment.

## Independent LLM assessment

On **Assess tasks**, open **Model connection**, enter an OpenAI API key in the password field (or set `OPENAI_API_KEY` on the server), choose a model, and click **Run assessment agent**. The default model is `gpt-4.1-mini`; `OPENAI_MODEL` overrides it. No additional Python dependencies are required. Keys are not written to the database, artifact cache, or logs.

The evaluator first identifies tasks, candidate responses, applicable criteria and expected answer content, including nested JSON fields. It then independently assesses each extracted attempt. Existing gold labels, rewards and Jev scores are withheld. Results include reward 1 or 0, concise reasoning, source row evidence, task context and the candidate response. Missing evidence, failed requests or unidentifiable records remain unverified. These are LLM judgments, not proof of correctness or executed grader checks; task-package completeness still requires review.

Model requests use the OpenAI Responses API with strict structured outputs and `store=false`. Running transmits task evidence to OpenAI and uses the configured account's credits. Limits are 240 KB and 500 rows per task CSV and 300 uncached calls per run. Oversized evidence is rejected, never silently truncated. Responses and token usage are cached under `artifacts/llm_cache` for reuse after interrupted runs. Successful assessments are reused; failed file assessments can be retried. Export still requires human review. To prepare the downloaded Hugging Face sample for this workflow, run `.\.venv\Scripts\python.exe scripts/run_hf_demo.py --prepare-llm`.

## Optional PostgreSQL setup (Windows PowerShell)

Install Python 3.11+ and Docker Desktop with Linux containers. Open PowerShell in this directory. Start Docker Desktop, then run:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
docker compose up -d --wait db
$env:DATABASE_URL = 'postgresql+psycopg://acceptance:acceptance@127.0.0.1:55432/acceptance'
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open http://localhost:8502. If the Windows Store Python launcher fails, substitute an installed Python executable for `py -3` (for example `& "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"`). No virtualenv activation is required. The Compose password is a local demo default. PostgreSQL data persists in the `pgdata` volume. Stop with `docker compose stop`; restart with the same commands. Do not run `docker compose down -v` if you want to retain review history.

## Original bundled grader demo (legacy_demo.py)

1. Keep the supplied fictional brief and click **Draft proposed specification**. The fake adapter proposes one fixed criterion; it does not interpret arbitrary briefs. Read its applicability question.
2. Enter your reviewer name and a rationale confirming that the criterion matches the brief. Click **Approve specification as human reviewer**.
3. Load bundled flawed task v1, then run executable checks. Inspect the evidence: `empty` has expected `false`, actual `true`. The correct solution also passes. Finding links identify its requirement, run, task and version.
4. Confirm findings and generate a local revision request. Inspect its owner, requested action and reproduction command.
5. Load corrected task v2 and run checks. All seven probes should match expectations. The original finding remains visible; its revision records the successful recheck run.
6. Inspect the acceptance report. Enter a release rationale and explicitly approve release. Only then does the download button appear. The ZIP includes approval attribution, report, checksums, task metadata, input CSV, reference CSV, and executable grader/probes. Extract it and follow its README to reproduce the checks.

Reviewer names are self-attested for a single-user local demo; this is not an authentication system. Approval buttons record human decisions. The fake adapter cannot call those services. No external messages are sent.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pipeline.validation 1
.\.venv\Scripts\python.exe -m pipeline.validation 2
```

Unit tests use temporary SQLite files to exercise the application without a database server. To exercise the optional migrated PostgreSQL schema as well:

```powershell
# Use a disposable local database. Integration data is rolled back.
$env:TEST_DATABASE_URL = $env:DATABASE_URL
.\.venv\Scripts\python.exe -m pytest -q
```

## Architecture and acceptance criteria

`app.py` renders persisted state and calls `pipeline/service.py` inside transactions. The service enforces specification approval, finding review, revision ordering, successful current-version checks, and explicit release approval. It locks the project row on PostgreSQL so simultaneous button actions serialize. Repeated load/check/release actions reuse completed work. Submission changes never inherit release approval. Failed and interrupted executions are recorded and can be retried; cancellation records a reviewer and rationale.

`pipeline/models.py` contains relational projects, specifications, requirements, submissions, runs, findings, revisions, approvals, releases and call logs. PostgreSQL JSONB stores probe evidence and small release snapshots. Alembic owns schema creation. The initial schema intentionally supports only one immutable specification version per project; requirement-change analysis belongs to a later milestone.

`pipeline/adapter.py` exposes a replaceable typed adapter with Pydantic inputs/outputs. The fake adapter makes one deterministic, logged call with no tools, retries or token spending. Role orchestration stays deterministic; a multi-agent framework and real adapter are deferred. Objective validation is Python, not model judgment.

`pipeline/validation.py` executes only repository-owned graders in a subprocess with a ten-second timeout. It records correct, reordered valid, empty, missing, header-only, malformed and incorrect probes. The proposed baseline explicitly requires every probe to match. The source input checksum binds the submission and evidence. A subprocess is not a security sandbox; there are no arbitrary uploads or submitted commands.

The milestone is accepted when the full defect → review → revision → successful recheck → human-approved export path works; findings trace to requirements and executable evidence; gates cannot be skipped through service calls; history survives new database sessions; and exported checksums and reproduction commands work. Tests cover these behaviors plus repeated actions, version scoping and execution failure.

## Scope and limitations

This is one synthetic CSV task, two grader versions and one fixed acceptance criterion. It does not implement all later portfolio requirements, ambiguous finding waivers, baseline model runs, duplicate/coverage analysis, untrusted execution or requirement-change impact analysis. The reference result is computed from the input and checked against explicit fixture expectations in tests; finite probes do not establish general grader correctness. No training improvement or model capability is demonstrated.

The fixtures are small, so release files are snapshotted in the database and downloaded as ZIPs. Large artifact storage and background execution are deferred. Checks run synchronously; no queue or durable worker recovery is claimed. Model adapter errors abort the transaction; tool errors are persisted. The UI identifies failed runs and allows retry. Hard process termination rolls back the current transaction rather than committing a partial run. Dependencies are bounded by major version, not a fully locked production environment.

