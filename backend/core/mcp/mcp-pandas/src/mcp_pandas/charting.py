"""Chart.js HTML generation for the visualization tool.

Builds a self-contained, interactive HTML page (Chart.js loaded from a CDN) from
plain series data, and writes it to the configured charts directory.
"""

from __future__ import annotations

import json
import re
import uuid

from .loader import CHARTS_DIR, DataFrameError

CHART_TYPES = ("bar", "line", "pie")

# Colorblind-friendly qualitative palette, cycled across series/slices.
PALETTE = (
    "#4E79A7", "#F28E2B", "#59A14F", "#E15759", "#B07AA1",
    "#76B7B2", "#EDC948", "#FF9DA7", "#9C755F", "#BAB0AC",
)

_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem; }}
  .wrap {{ max-width: 900px; margin: 0 auto; }}
  h1 {{ font-size: 1.25rem; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>{title}</h1>
  <canvas id="chart"></canvas>
</div>
<script>
  const config = {config};
  new Chart(document.getElementById("chart"), config);
</script>
</body>
</html>
"""


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "chart"


def _build_config(data: dict, chart_type: str) -> dict:
    labels = data.get("labels")
    series = data.get("series")
    if not isinstance(labels, list) or not labels:
        raise DataFrameError("data.labels must be a non-empty list.")
    if not isinstance(series, list) or not series:
        raise DataFrameError("data.series must be a non-empty list of {name, values}.")

    for item in series:
        values = item.get("values") if isinstance(item, dict) else None
        if not isinstance(values, list) or len(values) != len(labels):
            raise DataFrameError(
                "Each series needs a 'values' list the same length as 'labels'."
            )

    if chart_type == "pie":
        values = series[0]["values"]
        datasets = [
            {
                "label": series[0].get("name", "series"),
                "data": values,
                "backgroundColor": [PALETTE[i % len(PALETTE)] for i in range(len(values))],
            }
        ]
    else:
        datasets = [
            {
                "label": item.get("name", f"series {i + 1}"),
                "data": item["values"],
                "backgroundColor": PALETTE[i % len(PALETTE)],
                "borderColor": PALETTE[i % len(PALETTE)],
                "fill": False,
            }
            for i, item in enumerate(series)
        ]

    return {
        "type": chart_type,
        "data": {"labels": labels, "datasets": datasets},
        "options": {"responsive": True, "plugins": {"legend": {"position": "top"}}},
    }


def generate(data: dict, chart_type: str, title: str) -> dict:
    """Render ``data`` as a Chart.js HTML file and return where it was written."""
    if chart_type not in CHART_TYPES:
        raise DataFrameError(
            f"Unsupported chart_type '{chart_type}'. Use one of: {', '.join(CHART_TYPES)}."
        )

    config = _build_config(data, chart_type)
    html = _HTML_TEMPLATE.format(title=title or "Chart", config=json.dumps(config, indent=2))

    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{_slug(title or chart_type)}-{uuid.uuid4().hex[:8]}.html"
    path = CHARTS_DIR / filename
    try:
        path.write_text(html, encoding="utf-8")
    except OSError as exc:
        raise DataFrameError(f"Failed to write chart file: {exc}") from exc

    return {
        "chart_type": chart_type,
        "title": title,
        "file_path": str(path.resolve()),
        "series_count": len(config["data"]["datasets"]),
        "points": len(config["data"]["labels"]),
    }
