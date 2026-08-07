from pathlib import Path

import pytest

from storage import DATA_LAYERS, storage_paths, write_raw_payload


def test_storage_paths_are_deterministic(tmp_path: Path) -> None:
    paths = storage_paths(tmp_path)

    assert paths == {
        "root": tmp_path,
        "raw": tmp_path / "raw",
        "bronze": tmp_path / "bronze",
        "silver": tmp_path / "silver",
        "gold": tmp_path / "gold",
        "database": tmp_path / "mlb.duckdb",
    }


def test_raw_payload_cannot_be_overwritten(tmp_path: Path) -> None:
    relative_path = Path("mlb") / "2026-04-01" / "schedule.json"
    target = write_raw_payload(tmp_path, relative_path, b'{"games": []}')

    with pytest.raises(FileExistsError):
        write_raw_payload(tmp_path, relative_path, b'{"games": [1]}')

    assert target.read_bytes() == b'{"games": []}'


@pytest.mark.parametrize("relative_path", ["../escape.json", "."])
def test_raw_payload_must_stay_beneath_raw_directory(
    tmp_path: Path, relative_path: str
) -> None:
    with pytest.raises(ValueError):
        write_raw_payload(tmp_path, relative_path, b"payload")


def test_data_layers_include_raw_and_analytical_layers() -> None:
    assert DATA_LAYERS == ("raw", "bronze", "silver", "gold")
