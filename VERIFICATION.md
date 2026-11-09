# Verification in the implementation environment

## Step three: local assessment agent

- **9 relevant tests passed**, covering attempt grouping, success/failure evidence, unverified outcomes, section-three additions, changed-file checksum failure, persisted results, repeated-run reuse, approval-gated CSV/JSONL exports and the real Alembic migration/schema comparison.
- Streamlit test runs the agent on one CSV with two attempts, inspects 1 success and 1 failure, and approves export using explicitly synthetic reviewer inputs.
- The live local server responds `ok`; the assessment page loads against the local database.
- Adapter: deterministic `local-evidence-v1`, no language model or API. Labels are proposed exact comparisons against supplied reference values. Unsupported layouts and prose criteria remain unverified, and task review notes are included in exports.

## Corrected step two: one CSV file per task

- Active upload screen replaced with schema-free bulk task CSV storage. The previous structured-import page is no longer the active workflow.
- **7 relevant tests passed**: bulk upload and UI tests plus real Alembic migration/schema comparison.
- Uploaded and reopened **2,001 separate CSV files** with arbitrary columns. Verified unique generated task IDs, original bytes, per-file row counts, safe generated storage paths and repeated-request protection.
- Tested empty, malformed, unsupported and invalid-UTF-8 files; failed batches roll back records and remove their new artifact directory.
- Streamlit test uploads two task files, browses the saved batch and repeats the upload click without duplicate records.
- Limits: 10,000 CSV files, 50 MiB per file, 500 MiB per batch. No schema mapping, scoring or content assessment is performed by this page.

## Step-two requirements export and submission import

- Full suite: **40 passed, 1 skipped**. The skipped test requires optional PostgreSQL.
- CSV, JSON-array and JSONL parsing assessed against the same saved rubric; malformed, empty, unsupported and oversized inputs rejected.
- Imported 5,000 tasks, 5,000 attempts and 5,000 checks (15,000 normalized records), reopened persisted records, checked artifact existence and identical-import reuse.
- Checked requirement-linked failure evidence, score failure/unverified precedence, CSV traces larger than the default CSV parser field limit, and scoped prose rules remaining human review.
- Streamlit tests upload actual files, preview the assessment, confirm import, reopen saved results, and repeat confirmation without duplicating the submission.
- Alembic schema comparison passes with the new submission tables on SQLite. Local server health endpoint responds `ok` on port 8502.
- No uploaded graders executed. Automatic results do not establish grader or dataset content correctness. PostgreSQL integration remains unverified here.

## Stage-one client requirements update

- Full pytest suite: **15 passed, 1 skipped** (PostgreSQL integration requires a configured server).
- New intake tests cover required-field validation without partial records, generated client/task IDs, persistent save/reopen, edits with stable IDs, immutable revision snapshots, stale-edit rejection, and multiple tasks for one client.
- Streamlit AppTest submits and edits the actual requirements form, then reopens the saved task in a fresh app session.
- The Alembic migration including the new intake tables matches SQLAlchemy metadata on temporary SQLite.
- The main entrypoint is now `app.py` for client requirements. The prior grader demonstration is preserved as `legacy_demo.py`. Local runtime remains SQLite with automatic table creation.

## Prior vertical-slice verification

The usable interpreter was `C:\Users\USER\AppData\Local\Programs\Python\Python313\python.exe` (Python 3.13.5). The Windows Store interpreter selected by `py -3` was inaccessible.

- Created `.venv` with the usable interpreter.
- Ran `python -m compileall -q app.py pipeline migrations tests`: successful.
- Ran `python -m pipeline.validation 1`: correct solution accepted; empty, missing, header-only, malformed and incorrect outputs incorrectly accepted, reproducing the defect.
- Ran `python -m pipeline.validation 2`: all seven expected probe outcomes matched, including valid row reordering.
- Ran dependency-free grader tests using `python -m unittest discover -s tests -p test_validation_standalone.py -v`: four tests passed.
- Initial dependency installation was blocked by socket access restrictions (WinError 10013). The user subsequently installed the dependencies in `.venv`; `pip check` reports no broken requirements.
- Ran `python -m pytest -q`: 12 passed, 1 skipped. The skipped test requires PostgreSQL via `TEST_DATABASE_URL`.
- Passing tests include service approval gates, revision history, persistence across transaction boundaries, idempotent actions, cancelled runs, timeouts, checksummed export and execution of the extracted export outside the repository.
- The Streamlit AppTest runs the full review cycle using synthetic test reviewer inputs against a temporary SQLite database created by the real Alembic migration. The migration schema matches SQLAlchemy metadata.
- Docker remains inaccessible through the engine named pipe. PostgreSQL migrations and integration tests have not been run here.

Use the README commands to start PostgreSQL, migrate, and launch the UI. No claim is made that the PostgreSQL integration checks have passed. Automated test approvals are explicitly synthetic and confined to temporary test databases; produce your own approved example report and ZIP through the review UI.
