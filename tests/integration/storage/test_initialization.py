from pathlib import Path

from storage import DATA_LAYERS, connect_database, initialize_storage


def test_clean_initialization_creates_storage_and_schemas(tmp_path: Path) -> None:
    paths = initialize_storage(tmp_path / "data")

    assert all(paths[layer].is_dir() for layer in DATA_LAYERS)
    assert paths["database"].is_file()

    with connect_database(paths["database"]) as connection:
        schemas = {
            row[0]
            for row in connection.execute(
                "SELECT schema_name FROM information_schema.schemata"
            ).fetchall()
        }

    assert {"bronze", "silver", "gold"} <= schemas


def test_repeat_initialization_preserves_the_same_valid_state(tmp_path: Path) -> None:
    root = tmp_path / "data"
    first_paths = initialize_storage(root)
    marker = first_paths["raw"] / "existing.json"
    marker.write_text("unchanged", encoding="utf-8")

    second_paths = initialize_storage(root)

    assert second_paths == first_paths
    assert marker.read_text(encoding="utf-8") == "unchanged"
    assert all(second_paths[layer].is_dir() for layer in DATA_LAYERS)


def test_database_query_smoke(tmp_path: Path) -> None:
    paths = initialize_storage(tmp_path / "data")

    with connect_database(paths["database"]) as connection:
        result = connection.execute("SELECT 40 + 2").fetchone()

    assert result == (42,)
