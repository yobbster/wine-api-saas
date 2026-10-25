"""Clean the source CSV and build an indexed SQLite wine catalogue."""

import argparse
import csv
import hashlib
import json
import math
import sqlite3
import tempfile
import unicodedata
from collections import Counter
from contextlib import closing
from pathlib import Path

WINE_TYPES = {"Red Wine", "White Wine", "Sparkling & Champagne", "Pink and Rosé"}
REQUIRED_COLUMNS = {"", "name", "region", "variety", "rating", "notes"}

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE wines (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    region TEXT,
    wine_type TEXT CHECK (wine_type IN (
        'Red Wine', 'White Wine', 'Sparkling & Champagne', 'Pink and Rosé'
    )),
    rating REAL NOT NULL CHECK (rating BETWEEN 0 AND 100),
    notes TEXT,
    variety_raw TEXT
);
CREATE TABLE source_rows (
    source_row_id TEXT PRIMARY KEY NOT NULL,
    wine_id INTEGER NOT NULL REFERENCES wines(id),
    name TEXT NOT NULL,
    region TEXT NOT NULL,
    variety TEXT NOT NULL,
    rating TEXT NOT NULL,
    notes TEXT NOT NULL
);
CREATE TABLE import_metadata (
    key TEXT PRIMARY KEY NOT NULL,
    value TEXT NOT NULL
);
CREATE INDEX idx_wines_name ON wines(name COLLATE NOCASE);
CREATE INDEX idx_wines_region_rating ON wines(region, rating DESC, id);
CREATE INDEX idx_wines_type_rating ON wines(wine_type, rating DESC, id);
CREATE INDEX idx_wines_rating ON wines(rating DESC, id);
CREATE INDEX idx_source_rows_wine ON source_rows(wine_id);
CREATE VIRTUAL TABLE wines_fts USING fts5(
    name, notes, content='wines', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER wines_insert AFTER INSERT ON wines BEGIN
    INSERT INTO wines_fts(rowid, name, notes) VALUES (new.id, new.name, new.notes);
END;
CREATE TRIGGER wines_delete AFTER DELETE ON wines BEGIN
    INSERT INTO wines_fts(wines_fts, rowid, name, notes)
    VALUES ('delete', old.id, old.name, old.notes);
END;
CREATE TRIGGER wines_update AFTER UPDATE ON wines BEGIN
    INSERT INTO wines_fts(wines_fts, rowid, name, notes)
    VALUES ('delete', old.id, old.name, old.notes);
    INSERT INTO wines_fts(rowid, name, notes) VALUES (new.id, new.name, new.notes);
END;
"""


def clean_text(value: str) -> str | None:
    """Normalize Unicode and whitespace without guessing missing information."""
    return unicodedata.normalize("NFC", " ".join(value.split())) or None


def import_csv(source: Path, database: Path, report_path: Path) -> dict:
    """Build new outputs, refusing to overwrite existing data."""
    source = Path(source)
    database = Path(database)
    report_path = Path(report_path)
    if len({p.resolve() for p in (source, database, report_path)}) != 3:
        raise ValueError("Source CSV, database, and report must use different paths")
    for output in (database, report_path):
        if output.exists():
            raise FileExistsError(f"Refusing to overwrite {output}")
    database.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    counts = Counter()
    unknown_labels = Counter()
    seen = {}
    with tempfile.TemporaryDirectory(dir=database.parent) as temporary:
        staged_database = Path(temporary) / "wines.sqlite3"
        with closing(sqlite3.connect(staged_database)) as connection, connection:
            connection.executescript(SCHEMA)
            with source.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if not REQUIRED_COLUMNS.issubset(reader.fieldnames or []):
                    raise ValueError("CSV is missing required wine columns")
                for row in reader:
                    counts["source_rows"] += 1
                    source_id = row[""]
                    if None in row or any(v is None for v in row.values()):
                        raise ValueError(f"Malformed CSV record: {source_id}")
                    name = clean_text(row["name"])
                    if not name or not source_id.strip():
                        raise ValueError("Wine name and source row ID are required")
                    try:
                        rating = float(row["rating"])
                    except ValueError as error:
                        raise ValueError(f"Invalid rating at {source_id}") from error
                    if not math.isfinite(rating) or not 0 <= rating <= 100:
                        raise ValueError(f"Rating outside 0–100 at {source_id}")
                    region = clean_text(row["region"])
                    notes = clean_text(row["notes"])
                    variety = clean_text(row["variety"])
                    wine_type = variety if variety in WINE_TYPES else None
                    # Keep conflicting names, ratings, notes, and raw categories.
                    key = (name, region, wine_type, rating, notes, variety)
                    if key in seen:
                        wine_id = seen[key]
                        counts["duplicate_rows_removed"] += 1
                    else:
                        cursor = connection.execute(
                            "INSERT INTO wines "
                            "(name, region, wine_type, rating, notes, variety_raw) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            key,
                        )
                        wine_id = cursor.lastrowid
                        seen[key] = wine_id
                        counts["wines"] += 1
                        counts["missing_region"] += region is None
                        counts["missing_notes"] += notes is None
                        counts["unclassified_wine_type"] += wine_type is None
                        counts["missing_source_variety"] += variety is None
                        if variety and wine_type is None:
                            unknown_labels[variety] += 1
                    connection.execute(
                        "INSERT INTO source_rows VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            source_id,
                            wine_id,
                            row["name"],
                            row["region"],
                            row["variety"],
                            row["rating"],
                            row["notes"],
                        ),
                    )
            report = {
                "cleaning_version": 1,
                "source_file": source.name,
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "source_rows": counts["source_rows"],
                "wines": counts["wines"],
                "duplicate_rows_removed": counts["duplicate_rows_removed"],
                "excluded_columns": ["grape"],
                "cleaned_wines_missing_region": counts["missing_region"],
                "cleaned_wines_missing_notes": counts["missing_notes"],
                "cleaned_wines_unclassified_type": counts["unclassified_wine_type"],
                "cleaned_wines_missing_source_variety": counts[
                    "missing_source_variety"
                ],
                "unmapped_variety_labels": dict(sorted(unknown_labels.items())),
                "deduplication": "All cleaned fields including variety_raw must match",
                "id_policy": "Generated wine IDs; original CSV IDs in source_rows",
            }
            connection.executemany(
                "INSERT INTO import_metadata VALUES (?, ?)",
                [
                    (key, json.dumps(value, ensure_ascii=False))
                    for key, value in report.items()
                ],
            )
            connection.execute(
                "INSERT INTO wines_fts(wines_fts) VALUES('integrity-check')"
            )
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Database foreign key check failed")
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("Database integrity check failed")
            connection.execute("ANALYZE")
        staged_report = Path(temporary) / "import-report.json"
        staged_report.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        for output in (database, report_path):
            if output.exists():
                raise FileExistsError(f"Refusing to overwrite {output}")
        staged_database.rename(database)
        staged_report.rename(report_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=Path("wine-ratings.csv"))
    parser.add_argument("--database", type=Path, default=Path("data/wines.sqlite3"))
    parser.add_argument("--report", type=Path, default=Path("data/import-report.json"))
    args = parser.parse_args()
    report = import_csv(args.csv, args.database, args.report)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
