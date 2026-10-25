"""Exercise cleaning, provenance, search, and failed-import behavior."""

import csv
import json
import sqlite3

import pytest

from wine_api.import_data import import_csv


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["", "name", "grape", "region", "variety", "rating", "notes"])
        writer.writerows(rows)


def test_cleaning_preserves_provenance_and_conflicting_records(tmp_path):
    source = tmp_path / "source.csv"
    rows = [
        [
            0,
            "  Café Rouge  ",
            "wrong",
            " Napa  Valley ",
            "Red Wine",
            90,
            "berry\n spice",
        ],
        [1, "Cafe\u0301 Rouge", "", "Napa Valley", "Red Wine", 90, "berry spice"],
        [2, "Café Rouge", "", "Napa Valley", "Red Wine", 91, "berry spice"],
        [3, "Dessert", "", "Italy", "Screw Cap", 92, "   "],
        [4, "Dessert", "", "Italy", "Collectible", 92, "   "],
        [5, "Unknown", "", "  ", "", 88, ""],
    ]
    write_csv(source, rows)
    original = source.read_bytes()
    database = tmp_path / "wines.sqlite3"
    report_path = tmp_path / "report.json"

    report = import_csv(source, database, report_path)

    assert source.read_bytes() == original
    assert report["source_rows"] == 6
    assert report["wines"] == 5
    assert report["duplicate_rows_removed"] == 1
    assert report["cleaned_wines_unclassified_type"] == 3
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT name, region, wine_type, rating, notes FROM wines WHERE id = 1"
        ).fetchone() == ("Café Rouge", "Napa Valley", "Red Wine", 90.0, "berry spice")
        assert connection.execute(
            "SELECT wine_id FROM source_rows WHERE source_row_id IN ('0', '1')"
        ).fetchall() == [(1,), (1,)]
        assert connection.execute(
            "SELECT name, notes FROM source_rows WHERE source_row_id = '0'"
        ).fetchone() == ("  Café Rouge  ", "berry\n spice")
        assert connection.execute(
            "SELECT wine_type, variety_raw, notes FROM wines WHERE id = 3"
        ).fetchone() == (None, "Screw Cap", None)
        assert connection.execute(
            "SELECT region, wine_type, notes FROM wines WHERE name = 'Unknown'"
        ).fetchone() == (None, None, None)
        for table in ("wines", "source_rows"):
            columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
            assert "grape" not in {column[1] for column in columns}


def test_search_index_tracks_edits(tmp_path):
    source = tmp_path / "source.csv"
    write_csv(source, [[0, "Café Rouge", "", "France", "Red Wine", 90, "berry spice"]])
    database = tmp_path / "wines.sqlite3"
    import_csv(source, database, tmp_path / "report.json")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT rowid FROM wines_fts WHERE wines_fts MATCH 'cafe AND spice'"
        ).fetchall() == [(1,)]
        cursor = connection.execute(
            "INSERT INTO wines(name, rating, notes) VALUES ('New Wine', 90, 'sugar')"
        )
        wine_id = cursor.lastrowid
        assert connection.execute(
            "SELECT rowid FROM wines_fts WHERE wines_fts MATCH 'sugar'"
        ).fetchall() == [(wine_id,)]
        connection.execute(
            "UPDATE wines SET notes = 'vanilla' WHERE id = ?", (wine_id,)
        )
        assert (
            connection.execute(
                "SELECT rowid FROM wines_fts WHERE wines_fts MATCH 'sugar'"
            ).fetchall()
            == []
        )
        assert connection.execute(
            "SELECT rowid FROM wines_fts WHERE wines_fts MATCH 'vanilla'"
        ).fetchall() == [(wine_id,)]
        connection.execute("DELETE FROM wines WHERE id = ?", (wine_id,))
        assert (
            connection.execute(
                "SELECT rowid FROM wines_fts WHERE wines_fts MATCH 'vanilla'"
            ).fetchall()
            == []
        )


@pytest.mark.parametrize("rating", ["NaN", "inf", "101", "-1", "invalid", ""])
def test_invalid_rating_does_not_publish_partial_outputs(tmp_path, rating):
    source = tmp_path / "source.csv"
    write_csv(source, [[0, "Wine", "", "France", "Red Wine", rating, "notes"]])
    database = tmp_path / "wines.sqlite3"
    report = tmp_path / "report.json"
    with pytest.raises(ValueError, match="rating|Rating"):
        import_csv(source, database, report)
    assert not database.exists()
    assert not report.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["source.csv"]


def test_repeated_import_refuses_to_overwrite_database(tmp_path):
    source = tmp_path / "source.csv"
    write_csv(source, [[0, "Wine", "", "France", "Red Wine", 90, "notes"]])
    database = tmp_path / "wines.sqlite3"
    report = tmp_path / "report.json"
    import_csv(source, database, report)
    original = database.read_bytes()
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        import_csv(source, database, tmp_path / "another-report.json")
    assert database.read_bytes() == original
