"""Tool tests. Files are created in a tmp dir; no network is involved."""

from __future__ import annotations

import textwrap

import pytest

from mcp_pandas import charting
from mcp_pandas.loader import DataFrameError

CSV = textwrap.dedent(
    """\
    city,state,sales,units
    São Paulo,SP,100,3
    Campinas,SP,50,1
    Rio de Janeiro,RJ,30,2
    Niterói,RJ,,4
    """
)


@pytest.fixture()
def csv_file(tmp_path):
    path = tmp_path / "sales.csv"
    path.write_text(CSV, encoding="utf-8")
    return str(path)


async def _call(tool_name: str, **kwargs):
    """Run the real function behind a registered tool and return its raw value.

    Calling ``.fn`` directly returns the raw Python value and propagates the
    original exceptions (``DataFrameError``/``ValueError``) without the MCP
    transport-layer wrapping.
    """
    from mcp_pandas.server import mcp

    tool = await mcp.get_tool(tool_name)
    return tool.fn(**kwargs)


# --------------------------------------------------------------------------- #
# read_metadata                                                               #
# --------------------------------------------------------------------------- #
async def test_read_metadata_profiles_columns(csv_file):
    meta = await _call("read_metadata", file_path=csv_file)
    assert meta["column_count"] == 4
    names = {c["name"] for c in meta["columns"]}
    assert names == {"city", "state", "sales", "units"}
    sales = next(c for c in meta["columns"] if c["name"] == "sales")
    assert sales["nulls"] == 1
    assert "mean" in sales
    assert meta["suggested_operations"]


async def test_read_metadata_missing_file():
    with pytest.raises(DataFrameError, match="not found"):
        await _call("read_metadata", file_path="/no/such/file.csv")


async def test_read_metadata_unsupported_type(tmp_path):
    bad = tmp_path / "data.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(DataFrameError, match="Unsupported file type"):
        await _call("read_metadata", file_path=str(bad))


# --------------------------------------------------------------------------- #
# interpret_column_data                                                       #
# --------------------------------------------------------------------------- #
async def test_interpret_column_distribution(csv_file):
    out = await _call("interpret_column_data", file_path=csv_file, column_names=["state"])
    dist = {d["value"]: d["count"] for d in out["columns"]["state"]["distribution"]}
    assert dist == {"SP": 2, "RJ": 2}
    assert out["columns"]["state"]["unique"] == 2


async def test_interpret_unknown_column(csv_file):
    with pytest.raises(ValueError, match="not found"):
        await _call("interpret_column_data", file_path=csv_file, column_names=["missing"])


# --------------------------------------------------------------------------- #
# run_pandas_code                                                             #
# --------------------------------------------------------------------------- #
async def test_run_code_with_preloaded_df(csv_file):
    out = await _call(
        "run_pandas_code",
        code="result = df.groupby('state')['sales'].sum()",
        file_path=csv_file,
    )
    assert out["result"]["kind"] == "series"
    assert out["result"]["values"]["SP"] == 150.0


async def test_run_code_plain_expression():
    out = await _call("run_pandas_code", code="result = pd.Series([1, 2, 3]).sum()")
    assert out["result"] == 6


async def test_run_code_requires_result():
    with pytest.raises(ValueError, match="result"):
        await _call("run_pandas_code", code="x = 1 + 1")


async def test_run_code_blocks_imports():
    with pytest.raises(ValueError, match="not allowed"):
        await _call("run_pandas_code", code="import os\nresult = 1")


async def test_run_code_blocks_open():
    with pytest.raises(ValueError, match="not allowed"):
        await _call("run_pandas_code", code="result = open('/etc/passwd').read()")


async def test_run_code_runtime_error_is_friendly(csv_file):
    with pytest.raises(DataFrameError, match="Error while executing"):
        await _call("run_pandas_code", code="result = df['nope'].sum()", file_path=csv_file)


# --------------------------------------------------------------------------- #
# generate_chartjs                                                            #
# --------------------------------------------------------------------------- #
async def test_generate_chart_writes_html(tmp_path, monkeypatch):
    monkeypatch.setattr(charting, "CHARTS_DIR", tmp_path)
    data = {"labels": ["SP", "RJ"], "series": [{"name": "sales", "values": [150, 30]}]}
    out = await _call("generate_chartjs", data=data, chart_type="bar", title="Sales by state")
    from pathlib import Path

    written = Path(out["file_path"])
    assert written.exists()
    assert out["points"] == 2
    assert "Chart(" in written.read_text(encoding="utf-8")


async def test_generate_chart_bad_type(tmp_path, monkeypatch):
    monkeypatch.setattr(charting, "CHARTS_DIR", tmp_path)
    data = {"labels": ["a"], "series": [{"name": "s", "values": [1]}]}
    with pytest.raises(DataFrameError, match="Unsupported chart_type"):
        await _call("generate_chartjs", data=data, chart_type="scatter", title="x")


async def test_generate_chart_mismatched_lengths(tmp_path, monkeypatch):
    monkeypatch.setattr(charting, "CHARTS_DIR", tmp_path)
    data = {"labels": ["a", "b"], "series": [{"name": "s", "values": [1]}]}
    with pytest.raises(DataFrameError, match="same length"):
        await _call("generate_chartjs", data=data, chart_type="bar", title="x")
