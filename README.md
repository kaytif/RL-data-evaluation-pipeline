# RL Data Evaluation Pipeline

An LLM-powered workspace for reviewing reinforcement-learning post-training datasets: define requirements, upload task evidence, assess candidate responses, and review rewards before exporting results.

**Deployed app: [rl-data-evaluation-pipeline.onrender.com](https://rl-data-evaluation-pipeline.onrender.com/)**

Hosted on **Render Free**, with **Neon PostgreSQL** for database records. This is an independent portfolio project with no HUD integration or affiliation.

## What the app does

- Saves client requirements, task categories, minimum quantities, and versioned requirement snapshots.
- Stores uploaded task CSVs with generated IDs, checksums, filenames, and previews.
- Uses an LLM to identify task context, candidate attempts, assessment criteria, and reference answers when present, including nested JSON fields.
- Independently assesses each attempt and returns a **reward of 1 or 0**, concise reasoning, and source-row evidence.
- Keeps insufficient evidence unverified instead of inventing a reward.
- Provides result filters, side-by-side task and response inspection, and reviewer-approved CSV/JSONL exports.

Existing gold labels, rewards, and Jev predictions are withheld from the LLM evaluator. The main interface offers only LLM assessment; it has no rules-based assessment selector or synthetic sample loader.

## Use the deployed app

1. Open the [live app](https://rl-data-evaluation-pipeline.onrender.com/).
2. On **Client requirements**, create or select a dataset request, enter its requirements, and save a revision.
3. Open **Upload submission**, select the request and revision, then upload your task CSV files.
4. Open **Assess tasks**, select the upload, and expand **Model connection**.
5. Enter your OpenAI API key, select a model, and click **Test connection** before running assessment.
6. Click **Run assessment agent**, then inspect rewards, reasoning, references, and source evidence.
7. In **Review & export**, enter your reviewer name and rationale and explicitly approve the results before downloading CSV or JSONL.

Opening the app and saving requirements do not require a model key. LLM assessment requires an OpenAI API key with available credits and model access. The default evaluator is `gpt-4.1-mini`; `OPENAI_MODEL` can override it. Keys entered in the interface stay in the browser session's server-side state and are not written to the database, logs, or artifact cache.

### Input format

Use UTF-8 CSV files with a header row. The upload workflow treats each file as a task package; put its candidate attempts and related evidence together. Column names may vary, but the file must contain enough context to identify what was asked and what the candidate answered. A reference answer is useful when available, but self-contained semantic criteria can be assessed without one.

For example, a definition-to-word task can contain just two columns:

```csv
definition,answer
An instrument used to measure temperature.,thermometer
```

In that example, `answer` is the candidate response being assessed, not a supplied reference answer. State that interpretation in the saved required capabilities.

| Limit | Value |
| --- | --- |
| LLM assessment per task file | 240 KB and 500 data rows |
| New model requests per run | 300, including retry attempts |
| Upload storage per file | 50 MiB |
| Upload storage per batch | 10,000 files and 500 MiB combined |

Storage limits are larger than assessment limits. Oversized assessment files must be split; evidence is never silently truncated.

### Rewards and evidence

- **1:** The evaluator judges all applicable attempt requirements satisfied.
- **0:** The evaluator identifies a demonstrated violation.
- **Unverified:** Available evidence cannot establish a result, or a file could not be assessed.

These are model judgments, not proof of correctness. The evaluator does not execute uploaded code, run vendor graders, browse external sources, or independently verify external facts. Dataset quantity compliance and default task-package requirements still require separate review; the app does not yet fully enforce every saved requirement at dataset level.

Connection and account failures stop the run rather than assigning fabricated rewards. The app distinguishes recognized billing/quota errors from temporary rate limits and retries temporary rate limits a bounded number of times. Completed model responses are cached for reuse when retrying interrupted work.

## Run locally

Requires Python 3.11 or later. From the project directory in Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

Open [localhost:8502](http://127.0.0.1:8502). Keep the terminal running. To use a different port:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1 -Port 8503
```

Database selection:

- If `DATABASE_URL` is set in the shell, that value takes precedence.
- Otherwise, an ignored local `.env` file can supply `DATABASE_URL`.
- Without either, the app uses local SQLite at `acceptance.db` and creates its tables automatically.

For local Neon use, create `.env` using the placeholders in [.env.example](.env.example) and provide your own PostgreSQL connection string. Keep its SSL parameters. The launcher applies PostgreSQL migrations before opening Streamlit. Local SQLite records are not automatically copied to Neon.

Do not commit `.env`, database passwords, or API keys. Uploaded files and model-response caches are excluded from Git as well.

## Render and Neon deployment

The [Render Blueprint](render.yaml) defines a free Python web service. Configure these values when deploying from GitHub:

| Setting | Value |
| --- | --- |
| Runtime | Python 3 |
| Build command | `pip install -r requirements.txt` |
| Start command | `python scripts/start_server.py` |
| Health check | `/_stcore/health` |
| `DATABASE_URL` | Your Neon PostgreSQL connection string |
| `ARTIFACT_ROOT` | `/tmp/rl-dataset-review/artifacts` |
| `OPENAI_MODEL` | `gpt-4.1-mini` |

The startup script applies Alembic migrations, then starts Streamlit on Render's assigned `PORT`. Standard PostgreSQL URLs are normalized to the installed `psycopg` driver. Add secrets in **Render > Environment**, not in GitHub source files. A server-side `OPENAI_API_KEY` is optional; users can enter a key in the assessment page instead.

**Render Free storage limitation:** Neon persists database records, but uploaded CSV files and local model-response caches are currently stored on Render's temporary filesystem. They can disappear after a restart or redeploy. Keep original datasets backed up; the current free deployment is not a durable file archive. The app is a shared workspace without authentication or per-user data isolation.

See [DEPLOYMENT.md](DEPLOYMENT.md) for publishing and hosting details.

## Architecture

| File or module | Responsibility |
| --- | --- |
| `app.py` | Client requirements form and saved revisions |
| `pages/1_Requirements_guide.py` | Requirement definitions and examples |
| `pages/2_Upload_submission.py` | Batch CSV uploads and file inspection |
| `pages/3_Assess_tasks.py` | LLM connection, assessment, review, and export |
| `pipeline/intake.py`, `pipeline/rubric.py` | Requirement validation and persistence |
| `pipeline/csv_uploads.py` | Original file storage, checksums, and upload records |
| `pipeline/llm_assessment.py` | LLM extraction, independent grading, API errors, and response caching |
| `pipeline/assessment_agent.py` | Assessment orchestration, saved results, and call records |
| `pipeline/agent_dataset.py` | Reviewer approval and dataset exports |
| `pipeline/db.py`, `pipeline/environment.py` | Database connections and local environment settings |
| `pipeline/storage.py` | Configurable artifact directory |
| `migrations/` | Alembic database schema migrations |
| `scripts/start_server.py` | Hosted migration and startup entrypoint |

The evaluator uses the OpenAI Responses API with structured output validation and `store=false`. Processing is synchronous; there is no background worker or durable job queue. An interrupted database transaction rolls back, while completed cached model responses can be reused if the artifact files remain available.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests exercise temporary SQLite databases, requirement revisions, file uploads, extraction and scoring contracts, API error handling, caching, UI interactions, and approval-gated exports. Model calls are mocked in automated tests; a passing suite does not establish live model access or judgment quality. GitHub Actions runs the suite on pushes and pull requests.

Optional PostgreSQL integration tests use `TEST_DATABASE_URL`; point it at a disposable test database, not production.

## Legacy workflow

The original bundled grader demonstration remains separate in `legacy_demo.py`, with repository-owned executable graders in `pipeline/validation.py`. Run it with `python -m streamlit run legacy_demo.py`. Its fake adapter and synthetic fixtures are not the current LLM assessment workflow. No training improvement or downstream model capability gain is claimed.
