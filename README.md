# Wine API

A Python API for managing wines, built with FastAPI.

## Requirements

- Python 3.12 or newer

## Local setup (PowerShell)

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

The commands use the virtual environment directly, so activation is optional.

## Run the development server

```powershell
.\.venv\Scripts\fastapi.exe dev wine_api/main.py
```

- Health endpoint: <http://127.0.0.1:8000/health>
- Interactive API documentation: <http://127.0.0.1:8000/docs>
- OpenAPI schema: <http://127.0.0.1:8000/openapi.json>

The health endpoint returns:

```json
{"status": "ok"}
```

Press `Ctrl+C` to stop the server.

## Development checks

```powershell
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
```

The `tests/` directory is ready for tests. Once tests are added, run them with:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Project structure

```text
wine-api-saas/
├── wine_api/
│   ├── __init__.py
│   └── main.py          # FastAPI application and health endpoint
├── tests/
│   └── __init__.py
├── .gitignore
├── pyproject.toml      # Package metadata, dependencies, and tooling
└── README.md
```

## Cleaned wine database

The source is `wine-ratings.csv`. Build a new database and cleaning report with:

```powershell
.\.venv\Scripts\python.exe -m wine_api.import_data
```

Outputs are `data/wines.sqlite3` and `data/import-report.json`. Existing output
files are never overwritten. To import a new dataset, choose new output paths:

```powershell
.\.venv\Scripts\python.exe -m wine_api.import_data --csv wine-ratings.csv --database data/wines-v2.sqlite3 --report data/import-report-v2.json
```

Cleaning rules:

- Exclude `grape` completely. Leave the original CSV unchanged.
- Normalize Unicode to NFC, trim text, and collapse repeated whitespace.
- Store empty regions and notes as SQL `NULL`.
- Map `Red Wine`, `White Wine`, `Sparkling & Champagne`, and `Pink and Rosé` to
  `wine_type`. Leave other labels unclassified and preserve them in `variety_raw`.
  These categories reflect the source labels; individual classifications are not
  independently verified. Do not infer grapes, countries, or vintages from names.
- Merge only records with identical cleaned names, regions, categories, ratings,
  and notes. Keep repeated names when any of those fields differ.
- Reject malformed records, missing names or source IDs, and non-finite or
  out-of-range ratings (outside 0–100). Fail the import rather than silently lose rows.

Database tables:

| Table | Purpose |
| --- | --- |
| `wines` | Cleaned records with generated integer IDs |
| `source_rows` | Every source row's original ID and values, excluding grape, linked to its cleaned wine |
| `import_metadata` | Source checksum, cleaning rules, and import counts |
| `wines_fts` | Full-text index of names and notes, maintained by triggers |

Use `source_rows` to translate original CSV IDs into wine IDs. Generated IDs are
stable for this database, but are not guaranteed to stay the same across different imports.
Query `wines` for deduplicated counts and statistics. SQLite connections that write
to these tables should enable `PRAGMA foreign_keys = ON`.

The database has indexes for region, wine type, rating, and name prefixes.
Full-text search requires SQLite with FTS5 enabled, as provided by the project's
current Python installation. Example queries:

```sql
-- Highest-rated wines in an exact region.
SELECT id, name, rating
FROM wines
WHERE region = 'Napa Valley, California'
ORDER BY rating DESC, id
LIMIT 20;

-- Search names and notes.
SELECT w.id, w.name, w.rating
FROM wines_fts
JOIN wines AS w ON w.id = wines_fts.rowid
WHERE wines_fts MATCH 'zinfandel'
  AND w.rating >= 90
ORDER BY w.rating DESC, w.id
LIMIT 20;
```

The database is generated locally and ignored by Git. The importer and JSON report
can be tracked to reproduce and audit the build.
