![Logo](https://s3.devantage.com.br/devantage-public/logo-100x100.png)

# MCP Pandas

A modern [Model Context Protocol](https://modelcontextprotocol.io) server for
**pandas-based data analysis**. Point it at a CSV/Excel file and it profiles the
data, explains individual columns, runs sandboxed pandas code, and renders
interactive charts.

Exposes 4 tools and 2 guided prompts. Functionality inspired by
[`marlonluo2018/pandas-mcp-server`](https://github.com/marlonluo2018/pandas-mcp-server).

## Requirements

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (or Docker, for the containerized setup)

## Installation

```bash
uv venv

uv pip install -e ".[dev]"
```

## Running

The server supports two transports, selected via the `MCP_TRANSPORT` environment variable (see [Configuration](#configuration)).

### STDIO

The server speaks MCP over **stdio** by default (the transport used by most MCP
clients such as Claude Desktop and Claude Code):

```bash
source .venv/bin/activate

mcp-pandas
```

### Streamable HTTP

To serve over **streamable HTTP** instead:

```bash
source .venv/bin/activate

MCP_TRANSPORT=http mcp-pandas
```

The HTTP endpoint is then available at `http://0.0.0.0:8080/mcp/`.

### Docker

Build the image and run it in HTTP mode (the image defaults to HTTP on port 8080):

```bash
docker build -t mcp-pandas .

docker run --rm -p 8080:8080 mcp-pandas
```

Override any setting at runtime with `-e`, e.g. a different port:

```bash
docker run --rm -p 9000:9000 -e MCP_PORT=9000 mcp-pandas
```

## Configuration

| Variable          | Default   | Description                                                         |
| ----------------- | --------- | ------------------------------------------------------------------- |
| `MCP_TRANSPORT`   | `stdio`   | Transport to use: `stdio` or `http` (streamable HTTP).              |
| `MCP_HOST`        | `0.0.0.0` | Host/interface to bind when using HTTP. Use `0.0.0.0` to expose it. |
| `MCP_PORT`        | `8080`    | Port to listen on when using HTTP.                                  |
| `MCP_CHARTS_DIR`  | `charts`  | Directory where `generate_chartjs` writes HTML files.              |

## Available Tools

| Tool                    | Description                                                                          |
| ----------------------- | ----------------------------------------------------------------------------------- |
| `read_metadata`         | Profile a CSV/Excel file: shape, dtypes, null counts, cardinality, sample values, quality warnings and suggested operations (samples the first 100 rows). |
| `interpret_column_data` | Full value distribution of one or more columns (scans the whole file).              |
| `run_pandas_code`       | Execute pandas code in a restricted sandbox; optionally preload a file as `df`.      |
| `generate_chartjs`      | Render an interactive Chart.js HTML file (`bar`, `line`, `pie`) from series data.    |

### Guided Prompts

| Prompt             | Description                                                       |
| ------------------ | ---------------------------------------------------------------- |
| `explore_dataset`  | Walks metadata → column analysis → pandas code → visualization.  |
| `visualize_column` | Summarizes a single column and turns its distribution into a chart. |

### `run_pandas_code` safety

The executed code runs with a replaced `__builtins__` and a pattern filter. It
must assign its output to a variable named `result`, and constructs that escape
the sandbox are rejected: `import`, `open`, `exec`, `eval`, and references to
`os`/`sys`/`subprocess`/`shutil`/`socket`/dunder attributes. `pd` (pandas) and
`np` (numpy) are available; pass `file_path` to preload the data as `df`.

### Limits

- Maximum input file size: **100 MB**.
- `read_metadata` profiles the **first 100 rows** for speed.
- `interpret_column_data` returns up to **200 distinct values** per column.
- Supported formats: `.csv`, `.tsv`, `.txt`, `.xlsx`, `.xls`.

## Project layout

```
src/mcp_pandas/
├── server.py       # FastMCP instance, registration, transport entry point
├── loader.py       # shared file loading, validation, memory optimization
├── utils.py        # code-safety and column validation helpers
├── charting.py     # Chart.js HTML generation
├── prompts.py      # guided prompts
└── tools/          # one module per tool, each exposing register(mcp)
    ├── metadata.py
    ├── columns.py
    ├── execution.py
    └── charts.py
```

## Development

### Testing

The suite writes fixture files to a temp directory and needs no network:

```bash
pytest
```

## License

[MIT](./LICENSE)
