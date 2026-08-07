"""Initialize the local DuckDB and layered data directories."""

from pathlib import Path

import duckdb

DATA_LAYERS = ("raw", "bronze", "silver", "gold")
DATABASE_FILENAME = "mlb.duckdb"


def storage_paths(root: str | Path) -> dict[str, Path]:
    """Return the deterministic paths managed beneath ``root``."""
    root_path = Path(root)
    return {
        "root": root_path,
        **{layer: root_path / layer for layer in DATA_LAYERS},
        "database": root_path / DATABASE_FILENAME,
    }


def connect_database(database_path: str | Path) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection at ``database_path``."""
    return duckdb.connect(str(Path(database_path)))


def initialize_storage(root: str | Path) -> dict[str, Path]:
    """Create the local data layout and initialize DuckDB schemas."""
    paths = storage_paths(root)
    paths["root"].mkdir(parents=True, exist_ok=True)

    for layer in DATA_LAYERS:
        paths[layer].mkdir(exist_ok=True)

    with connect_database(paths["database"]) as connection:
        for schema in DATA_LAYERS[1:]:
            connection.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    return paths


def write_raw_payload(
    root: str | Path, relative_path: str | Path, payload: bytes
) -> Path:
    """Write a raw payload once, refusing path escapes and overwrites."""
    raw_root = storage_paths(root)["raw"].resolve()
    target = (raw_root / relative_path).resolve()
    if not target.is_relative_to(raw_root) or target == raw_root:
        raise ValueError("raw payload path must name a file beneath the raw directory")

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as raw_file:
        raw_file.write(payload)
    return target
