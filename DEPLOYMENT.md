# GitHub, Render Free and Neon

## GitHub first

The repository excludes local databases, uploaded data, model-response caches, credentials,
logs and the virtual environment. `.env.example` contains placeholders only. GitHub Actions
runs the test suite on pushes and pull requests.

Run `powershell -ExecutionPolicy Bypass -File .\publish.ps1` from a normal PowerShell window.
It uses GitHub CLI browser sign-in if needed, then creates a private `rl-dataset-review`
repository and pushes the prepared `main` branch. It refuses to replace an existing remote.

## Render and Neon

`render.yaml` targets Render's **free** Python web service in Singapore. It never provisions
a paid disk or Render database. Set `DATABASE_URL` to a Neon PostgreSQL URL (keep its SSL
query parameters). The app normalizes `postgresql://` to the installed `psycopg` driver,
checks database connections, and applies Alembic migrations before starting Streamlit.

OpenAI credentials can be entered separately in each browser session. No server API key is
required to open the app. LLM scoring still requires available API credits.

**Free-tier storage limitation:** Neon retains database records, but Render Free does not
retain uploaded CSV files or local model-response caches across restarts or redeploys.
`ARTIFACT_ROOT` currently points to temporary storage. Durable file storage must be added
before treating this as a persistent hosted assessment workspace. Do not assume that
uploading files to the current free-tier configuration backs them up in Neon.

The hosted database starts empty; local SQLite data is not automatically migrated. This is
a shared workspace without user authentication or per-user data isolation. Use only demo
data if exposing it publicly. Keep database URLs and API keys out of source control.

References: [Render Free](https://render.com/docs/free),
[Render disks](https://render.com/docs/disks),
[Neon SQLAlchemy](https://neon.com/docs/guides/sqlalchemy).
