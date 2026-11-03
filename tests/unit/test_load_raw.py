from pathlib import Path

import pandas as pd
import pytest

from ask_edgar.etl import load_raw

TAG = load_raw.TABLES[1]


def test_tables_follow_the_sec_file_layout() -> None:
    assert [t.name for t in load_raw.TABLES] == ["sub", "tag", "num", "pre"]
    num = load_raw.TABLES[2]
    assert num.filename == "num.txt"
    assert num.columns == (
        "adsh", "tag", "version", "ddate", "qtrs", "uom", "segments", "coreg", "value", "footnote",
    )  # fmt: skip
    assert len(load_raw.TABLES[0].columns) == 36


def test_chunk_to_csv_leaves_empties_unquoted_and_escapes_quotes() -> None:
    chunk = pd.DataFrame({"a": ["x", "", 'say "hi"', "NA"], "b": ["1", "", "2,5", "3"]})
    # unquoted empty -> NULL under COPY (FORMAT csv); quotes doubled; "NA" untouched
    assert load_raw.chunk_to_csv(chunk) == 'x,1\n,\n"say ""hi""","2,5"\nNA,3\n'


def test_read_chunks_rejects_an_unexpected_header(tmp_path: Path) -> None:
    path = tmp_path / "tag.txt"
    path.write_text("tag\tversion\tbogus\nA\tv\t1\n", encoding="utf-8")
    with pytest.raises(load_raw.LoadError, match="unexpected columns"):
        next(load_raw.read_chunks(path, TAG))


def test_read_chunks_points_at_make_data_when_the_file_is_missing(tmp_path: Path) -> None:
    with pytest.raises(load_raw.LoadError, match="make data"):
        next(load_raw.read_chunks(tmp_path / "tag.txt", TAG))


def test_read_chunks_keeps_strings_verbatim_and_honours_chunksize(tmp_path: Path) -> None:
    path = tmp_path / "tag.txt"
    rows = [
        ["Revenues", "us-gaap/2024", "0", "0", "monetary", "D", "C", "Revenues", 'doc "quoted"'],
        ["NA", "v1", "1", "0", "", "", "", "", ""],
        ["null", "v1", "1", "1", "", "", "", "", "a, comma"],
    ]
    lines = ["\t".join(TAG.columns)] + ["\t".join(r) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    chunks = list(load_raw.read_chunks(path, TAG, chunksize=2))

    assert [len(c) for c in chunks] == [2, 1]
    first = chunks[0]
    assert list(first.columns) == list(TAG.columns)
    assert first["doc"].tolist() == ['doc "quoted"', ""]  # no NaN: empties stay ""
    assert first["tag"].tolist() == ["Revenues", "NA"]  # "NA" is not a missing value
    assert chunks[1]["tag"].tolist() == ["null"]


def test_read_chunks_unwraps_csv_style_quoting(tmp_path: Path) -> None:
    # SEC files wrap a field in double quotes when it holds a tab, comma or quote
    path = tmp_path / "tag.txt"
    row = "\t".join(["A", "v", "0", "0", "", "", "", "", '"has\ttab, ""and"" quotes"'])
    path.write_text("\t".join(TAG.columns) + f"\n{row}\n", encoding="utf-8")
    [chunk] = list(load_raw.read_chunks(path, TAG))
    assert chunk["doc"].tolist() == ['has\ttab, "and" quotes']


def test_read_chunks_turns_parser_errors_into_load_errors(tmp_path: Path) -> None:
    path = tmp_path / "tag.txt"
    row = "\t".join(["A", "v", "0", "0", "", "", "", "", '"never closed'])
    path.write_text("\t".join(TAG.columns) + f"\n{row}\n", encoding="utf-8")
    with pytest.raises(load_raw.LoadError, match=r"tag\.txt"):
        list(load_raw.read_chunks(path, TAG))
