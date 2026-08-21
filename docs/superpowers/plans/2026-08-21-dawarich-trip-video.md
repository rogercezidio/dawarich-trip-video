# dawarich-trip-video Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A standalone, publicly-distributable Docker tool that renders a Dawarich trip's date window as an animated MP4 travel video, by fetching points from Dawarich's official API and feeding them into a patched, vendored copy of google-timeline-visualizer.

**Architecture:** A single-purpose CLI (`generate_trip_video.py`), packaged as a Docker image, fetches points for a user-supplied date range from `{--dawarich-url}/api/v1/points`, converts them into the minimal `semanticSegments` JSON shape google-timeline-visualizer accepts, and shells out to a vendored+patched copy of `visualizer.py` (patched to filter by date range instead of calendar year) to render the MP4.

**Tech Stack:** Python 3.11, `requests`, `matplotlib`/`numpy`/`Pillow`/`python-dateutil` (upstream visualizer deps), `ffmpeg`, Docker, `pytest` for tests.

**Spec:** `docs/superpowers/specs/2026-08-21-dawarich-trip-video-design.md`

## Global Constraints

- Vendored `visualizer.py` must record the exact upstream commit SHA it was vendored from, in a header comment (spec decision 2).
- Only Dawarich's versioned `/api/v1/points` endpoint is called. Never call the `Trips` web routes (session/cookie-only, unversioned) — the trip date range is always supplied by the user via `--start`/`--end` CLI flags (spec decision 5).
- `--dawarich-url` and `--api-key` have NO code default — a default like `http://localhost:3000` would resolve to the container itself, not the user's Dawarich host. Both must come from a CLI flag or an env var, or the tool errors out before any network call (spec decision 3).
- No automatic triggering (no webhooks, no polling, no always-on service, no docker-compose file). The tool only runs via `docker run` (spec decision 4, 6).
- Rendered MP4s land wherever the user bind-mounts to `/output` (spec decision 7).
- This repo lives independently at `~/dawarich-trip-video`, with nothing specific to any one person's deployment (spec decision 8). Nothing in this plan pushes to a remote GitHub or a Docker registry — that stays a manual, explicitly user-triggered action outside this plan.
- License: MIT, crediting the vendored upstream project (spec decision 9).
- The wrapper script never swallows the vendored script's stdout/stderr — errors must be visible to the user running the command.

---

## Task 1: Repo scaffolding + vendor and patch `visualizer.py`

**Files:**
- Create: `.gitignore`
- Create: `vendor/visualizer.py`

**Interfaces:**
- Produces: a CLI script invocable as `python vendor/visualizer.py --input <path> --start-date YYYY-MM-DD --end-date YYYY-MM-DD --title <str> --output <path> --camera-movement {fixed,steady,dynamic} --long-trip-compression {off,gentle,balanced,strong}`, replacing upstream's `--year` flag.

- [ ] **Step 1: Initialize the repo and add `.gitignore`**

```bash
cd /home/roger/dawarich-trip-video
git init
mkdir -p vendor tests
cat > .gitignore <<'EOF'
.env
__pycache__/
*.pyc
.venv/
output/
.superpowers/
EOF
git add .gitignore
git commit -m "chore: initialize repo"
```

- [ ] **Step 2: Download the pinned upstream file**

```bash
curl -sL -o vendor/visualizer.py \
  https://raw.githubusercontent.com/mahlernim/google-timeline-visualizer/676ee3de8a7c0b50f4f7c84337500d9708570903/visualizer.py
```

- [ ] **Step 3: Add a vendoring header comment**

At the very top of `vendor/visualizer.py`, right after the existing `#!/usr/bin/env python3` line, insert:

```python
# --- VENDORED COPY ---
# Source: https://github.com/mahlernim/google-timeline-visualizer
# Upstream commit: 676ee3de8a7c0b50f4f7c84337500d9708570903
# License: MIT
# Local patch: --year (calendar-year filter) replaced with --start-date/--end-date
#              (explicit date range), to match Dawarich trip windows that can
#              span or fall outside a single calendar year. See patches below,
#              search this file for "PATCHED".
# --- END VENDORED COPY HEADER ---
```

- [ ] **Step 4: Patch the datetime import**

Old:
```python
from datetime import datetime, timedelta
```
New:
```python
from datetime import datetime, timedelta, date  # PATCHED: date range support
```

- [ ] **Step 5: Patch `add_point` to filter by date range instead of year**

Old:
```python
    def add_point(output, timestamp, coordinate):
        if timestamp is None or coordinate is None:
            return False
        if timestamp.year == year:
            output.append({'dt': timestamp, 'lat': coordinate[0], 'lon': coordinate[1]})
        return True
```
New:
```python
    def add_point(output, timestamp, coordinate):  # PATCHED
        if timestamp is None or coordinate is None:
            return False
        if start_date <= timestamp.date() <= end_date:
            output.append({'dt': timestamp, 'lat': coordinate[0], 'lon': coordinate[1]})
        return True
```

- [ ] **Step 6: Patch `extract_timeline_points`'s signature**

Old:
```python
def extract_timeline_points(data, year):
    """Return normalized points for a year from supported Timeline JSON roots."""
```
New:
```python
def extract_timeline_points(data, start_date, end_date):  # PATCHED
    """Return normalized points within [start_date, end_date] from supported Timeline JSON roots."""
```

- [ ] **Step 7: Patch `parse_timeline`**

Old:
```python
def parse_timeline(input_path, year):
    print(f"Loading {input_path}...")
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TimelineParseError(f"Could not read Timeline JSON: {error}") from error
        
    segments = data if isinstance(data, list) else data.get('semanticSegments', []) if isinstance(data, dict) else []
    print(f"Parsing {len(segments)} segments for year {year}...")
    points = extract_timeline_points(data, year)

    if not points:
        raise NoDataFoundError(f"No data points found for year {year}.")
```
New:
```python
def parse_timeline(input_path, start_date, end_date):  # PATCHED
    print(f"Loading {input_path}...")
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TimelineParseError(f"Could not read Timeline JSON: {error}") from error
        
    segments = data if isinstance(data, list) else data.get('semanticSegments', []) if isinstance(data, dict) else []
    print(f"Parsing {len(segments)} segments for {start_date}..{end_date}...")
    points = extract_timeline_points(data, start_date, end_date)

    if not points:
        raise NoDataFoundError(f"No data points found between {start_date} and {end_date}.")
```

- [ ] **Step 8: Patch the argument parser**

Old:
```python
    parser.add_argument('--input', '-i', required=True, type=existing_file, help="Path to Timeline.json")
    parser.add_argument('--year', '-y', type=int, default=datetime.now().year, help="Year to visualize")
```
New:
```python
    parser.add_argument('--input', '-i', required=True, type=existing_file, help="Path to Timeline.json")
    parser.add_argument('--start-date', required=True, type=date.fromisoformat,  # PATCHED
                        help="Start of the date range to visualize (YYYY-MM-DD)")
    parser.add_argument('--end-date', required=True, type=date.fromisoformat,  # PATCHED
                        help="End of the date range to visualize (YYYY-MM-DD, inclusive)")
```

- [ ] **Step 9: Patch the `main()` call site**

Old:
```python
        timestamps, xs, ys, cum_dist, lats, lons = parse_timeline(args.input, args.year)
```
New:
```python
        timestamps, xs, ys, cum_dist, lats, lons = parse_timeline(args.input, args.start_date, args.end_date)  # PATCHED
```

- [ ] **Step 10: Sanity-check the patched file parses**

```bash
python3 -c "import ast; ast.parse(open('vendor/visualizer.py').read()); print('OK')"
python3 -c "
src = open('vendor/visualizer.py').read()
assert '--year' not in src, '--year still present'
assert '--start-date' in src and '--end-date' in src, 'date-range flags missing'
print('Patch check OK')
"
```
Expected: both commands print `OK` / `Patch check OK` with no errors.

- [ ] **Step 11: Commit**

```bash
git add vendor/visualizer.py
git commit -m "feat: vendor google-timeline-visualizer, patch year filter to date range"
```

---

## Task 2: Pure helper functions — title slugging and JSON building

**Files:**
- Create: `generate_trip_video.py`
- Create: `tests/test_generate_trip_video.py`

**Interfaces:**
- Consumes: nothing from other tasks yet.
- Produces: `slugify_title(title: str) -> str` and `points_to_timeline_json(points: list[dict]) -> dict`, where each input point is `{'latitude': str, 'longitude': str, 'timestamp': int}` (Dawarich's slim point shape) and the output matches the `semanticSegments` shape `vendor/visualizer.py` accepts (Task 1).

- [ ] **Step 1: Set up a local Python environment for running tests**

The unit tests in Tasks 2–4 only need `requests` and `pytest` — not the
heavy `matplotlib`/`numpy`/`ffmpeg` stack that `requirements.txt` (Task 5)
installs for the Docker image.

```bash
cd /home/roger/dawarich-trip-video
python3 -m venv .venv
source .venv/bin/activate
pip install pytest requests
```

Keep this venv activated for the rest of Tasks 2–4 (re-activate with
`source .venv/bin/activate` in any new shell).

- [ ] **Step 2: Write the failing tests**

Create `tests/test_generate_trip_video.py`:

```python
from generate_trip_video import slugify_title, points_to_timeline_json


def test_slugify_title_replaces_spaces_and_punctuation():
    assert slugify_title("Ceara 2026!") == "Ceara_2026"


def test_slugify_title_falls_back_when_nothing_alphanumeric():
    assert slugify_title("!!!") == "trip"


def test_points_to_timeline_json_builds_single_segment_in_order():
    points = [
        {'latitude': '-3.71', 'longitude': '-38.54', 'timestamp': 1752278400},
        {'latitude': '-3.72', 'longitude': '-38.55', 'timestamp': 1752278460},
    ]

    result = points_to_timeline_json(points)

    segment = result['semanticSegments'][0]
    assert len(segment['timelinePath']) == 2
    assert segment['timelinePath'][0]['point'] == '-3.71,-38.54'
    assert segment['startTime'] == segment['timelinePath'][0]['time']
    assert segment['endTime'] == segment['timelinePath'][-1]['time']


def test_points_to_timeline_json_empty_input():
    assert points_to_timeline_json([]) == {'semanticSegments': []}
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
python -m pytest tests/test_generate_trip_video.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'generate_trip_video'` (file doesn't exist yet).

- [ ] **Step 4: Write the minimal implementation**

Create `generate_trip_video.py`:

```python
#!/usr/bin/env python3
"""Fetch a Dawarich trip's points and render them as an MP4 via the vendored visualizer."""

import re
from datetime import datetime, timezone


def slugify_title(title):
    slug = re.sub(r'[^A-Za-z0-9]+', '_', title).strip('_')
    return slug or 'trip'


def points_to_timeline_json(points):
    """Convert Dawarich slim points into the minimal semanticSegments shape visualizer.py accepts."""
    path_points = []
    for point in points:
        timestamp = datetime.fromtimestamp(point['timestamp'], tz=timezone.utc)
        path_points.append({
            'point': f"{point['latitude']},{point['longitude']}",
            'time': timestamp.isoformat(),
        })

    if not path_points:
        return {'semanticSegments': []}

    return {
        'semanticSegments': [{
            'startTime': path_points[0]['time'],
            'endTime': path_points[-1]['time'],
            'timelinePath': path_points,
        }],
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_generate_trip_video.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add generate_trip_video.py tests/test_generate_trip_video.py
git commit -m "feat: add title slugging and timeline JSON builder"
```

---

## Task 3: Dawarich API client — `fetch_points`

**Files:**
- Modify: `generate_trip_video.py`
- Modify: `tests/test_generate_trip_video.py`

**Interfaces:**
- Consumes: nothing new from Task 2 (independent function in the same module).
- Produces: `fetch_points(base_url: str, api_key: str, start_date: date, end_date: date, per_page: int = 500) -> list[dict]` and `DawarichApiError(Exception)`. Task 4 calls this with the resolved `--dawarich-url`/`--api-key` to get the point list to pass into `points_to_timeline_json`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_generate_trip_video.py`:

```python
import json
from datetime import date
from unittest.mock import Mock, patch

import pytest

from generate_trip_video import fetch_points, DawarichApiError


def _mock_response(json_body, status_code=200, total_pages='1'):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = json_body
    response.headers = {'X-Total-Pages': total_pages}
    response.text = json.dumps(json_body)
    return response


@patch('generate_trip_video.requests.get')
def test_fetch_points_single_page(mock_get):
    mock_get.return_value = _mock_response([{'latitude': '1', 'longitude': '2', 'timestamp': 100}])

    points = fetch_points('http://localhost:3000', 'key', date(2026, 7, 12), date(2026, 7, 12))

    assert points == [{'latitude': '1', 'longitude': '2', 'timestamp': 100}]
    assert mock_get.call_count == 1


@patch('generate_trip_video.requests.get')
def test_fetch_points_paginates_until_total_pages_reached(mock_get):
    mock_get.side_effect = [
        _mock_response([{'latitude': '1', 'longitude': '2', 'timestamp': 100}], total_pages='2'),
        _mock_response([{'latitude': '3', 'longitude': '4', 'timestamp': 200}], total_pages='2'),
    ]

    points = fetch_points('http://localhost:3000', 'key', date(2026, 7, 12), date(2026, 7, 26))

    assert len(points) == 2
    assert mock_get.call_count == 2


@patch('generate_trip_video.requests.get')
def test_fetch_points_raises_on_401(mock_get):
    mock_get.return_value = _mock_response([], status_code=401)

    with pytest.raises(DawarichApiError, match='401'):
        fetch_points('http://localhost:3000', 'key', date(2026, 7, 12), date(2026, 7, 12))


@patch('generate_trip_video.requests.get')
def test_fetch_points_raises_on_other_error_status(mock_get):
    mock_get.return_value = _mock_response({'error': 'boom'}, status_code=500)

    with pytest.raises(DawarichApiError, match='500'):
        fetch_points('http://localhost:3000', 'key', date(2026, 7, 12), date(2026, 7, 12))
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_generate_trip_video.py -v
```
Expected: the 4 new tests FAIL with `ImportError: cannot import name 'fetch_points'`.

- [ ] **Step 3: Write the minimal implementation**

Add to the top of `generate_trip_video.py` (with the other imports) and after `points_to_timeline_json`:

```python
import requests

DEFAULT_PER_PAGE = 500


class DawarichApiError(Exception):
    """Raised when the Dawarich API call fails in a way the user must fix."""


def fetch_points(base_url, api_key, start_date, end_date, per_page=DEFAULT_PER_PAGE):
    """Fetch all points in [start_date, end_date] from the Dawarich points API, paginated."""
    headers = {'Authorization': f'Bearer {api_key}'}
    params = {
        'start_at': f'{start_date.isoformat()}T00:00:00Z',
        'end_at': f'{end_date.isoformat()}T23:59:59Z',
        'order': 'asc',
        'slim': 'true',
        'per_page': per_page,
        'page': 1,
    }

    points = []
    while True:
        try:
            response = requests.get(f'{base_url}/api/v1/points', headers=headers, params=params, timeout=30)
        except requests.exceptions.RequestException as error:
            raise DawarichApiError(f"Could not reach Dawarich at {base_url}: {error}") from error

        if response.status_code == 401:
            raise DawarichApiError(
                "Dawarich rejected the request (401 Unauthorized). Check your API key.",
            )
        if response.status_code != 200:
            raise DawarichApiError(
                f"Dawarich returned {response.status_code} for /api/v1/points: {response.text[:500]}",
            )

        page_points = response.json()
        points.extend(page_points)

        total_pages = int(response.headers.get('X-Total-Pages', '1') or '1')
        if params['page'] >= total_pages or not page_points:
            break
        params['page'] += 1

    return points
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_generate_trip_video.py -v
```
Expected: PASS (8 tests total).

- [ ] **Step 5: Commit**

```bash
git add generate_trip_video.py tests/test_generate_trip_video.py
git commit -m "feat: fetch paginated points from the Dawarich API"
```

---

## Task 4: Config resolution + CLI wiring — `resolve_setting`, `build_argument_parser`, `run_visualizer`, `main`

**Files:**
- Modify: `generate_trip_video.py`
- Modify: `tests/test_generate_trip_video.py`

**Interfaces:**
- Consumes: `slugify_title`, `points_to_timeline_json` (Task 2), `fetch_points`, `DawarichApiError` (Task 3).
- Produces: `resolve_setting(flag_value: str | None, env_var_name: str) -> str | None`, `build_argument_parser() -> argparse.ArgumentParser`, `run_visualizer(timeline_path, start_date, end_date, title, output_path, camera_movement, compression) -> int`, `main(argv=None) -> int`. `main` is the module's `if __name__ == '__main__'` entrypoint and is what the Dockerfile's `ENTRYPOINT` runs (Task 5).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_generate_trip_video.py`:

```python
import generate_trip_video as gtv


def test_resolve_setting_prefers_flag_over_env(monkeypatch):
    monkeypatch.setenv('DAWARICH_URL', 'http://env-value:3000')
    assert gtv.resolve_setting('http://flag-value:3000', 'DAWARICH_URL') == 'http://flag-value:3000'


def test_resolve_setting_falls_back_to_env(monkeypatch):
    monkeypatch.setenv('DAWARICH_URL', 'http://env-value:3000')
    assert gtv.resolve_setting(None, 'DAWARICH_URL') == 'http://env-value:3000'


def test_resolve_setting_returns_none_when_neither_is_set(monkeypatch):
    monkeypatch.delenv('DAWARICH_URL', raising=False)
    assert gtv.resolve_setting(None, 'DAWARICH_URL') is None


@patch('generate_trip_video.run_visualizer', return_value=0)
@patch('generate_trip_video.fetch_points')
def test_main_happy_path_invokes_visualizer(mock_fetch, mock_run, monkeypatch):
    monkeypatch.setenv('DAWARICH_URL', 'http://localhost:3000')
    monkeypatch.setenv('DAWARICH_API_KEY', 'test-key')
    mock_fetch.return_value = [{'latitude': '1', 'longitude': '2', 'timestamp': 100}]

    exit_code = gtv.main(['--start', '2026-07-12', '--end', '2026-07-26', '--title', 'Ceara 2026'])

    assert exit_code == 0
    mock_run.assert_called_once()
    mock_fetch.assert_called_once()
    assert mock_fetch.call_args[0][0] == 'http://localhost:3000'
    assert mock_fetch.call_args[0][1] == 'test-key'


def test_main_exits_when_dawarich_url_missing(monkeypatch, capsys):
    monkeypatch.delenv('DAWARICH_URL', raising=False)
    monkeypatch.setenv('DAWARICH_API_KEY', 'test-key')

    exit_code = gtv.main(['--start', '2026-07-12', '--end', '2026-07-26', '--title', 'Ceara 2026'])

    assert exit_code == 1
    assert 'dawarich-url' in capsys.readouterr().err.lower()


def test_main_exits_when_api_key_missing(monkeypatch, capsys):
    monkeypatch.setenv('DAWARICH_URL', 'http://localhost:3000')
    monkeypatch.delenv('DAWARICH_API_KEY', raising=False)

    exit_code = gtv.main(['--start', '2026-07-12', '--end', '2026-07-26', '--title', 'Ceara 2026'])

    assert exit_code == 1
    assert 'api-key' in capsys.readouterr().err.lower()


@patch('generate_trip_video.fetch_points')
def test_main_exits_when_no_points_found(mock_fetch, monkeypatch, capsys):
    monkeypatch.setenv('DAWARICH_URL', 'http://localhost:3000')
    monkeypatch.setenv('DAWARICH_API_KEY', 'test-key')
    mock_fetch.return_value = []

    exit_code = gtv.main(['--start', '2026-07-12', '--end', '2026-07-26', '--title', 'Ceara 2026'])

    assert exit_code == 1
    assert 'No points found' in capsys.readouterr().err


@patch('generate_trip_video.fetch_points')
def test_main_surfaces_dawarich_api_errors(mock_fetch, monkeypatch, capsys):
    monkeypatch.setenv('DAWARICH_URL', 'http://localhost:3000')
    monkeypatch.setenv('DAWARICH_API_KEY', 'test-key')
    mock_fetch.side_effect = gtv.DawarichApiError("Dawarich returned 500")

    exit_code = gtv.main(['--start', '2026-07-12', '--end', '2026-07-26', '--title', 'Ceara 2026'])

    assert exit_code == 1
    assert 'Dawarich returned 500' in capsys.readouterr().err
```

Note: this file already has `from unittest.mock import Mock, patch` from Task 3 — no new import needed for `patch`.

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_generate_trip_video.py -v
```
Expected: the 8 new tests FAIL with `AttributeError: module 'generate_trip_video' has no attribute 'resolve_setting'` (or `'main'`).

- [ ] **Step 3: Write the minimal implementation**

Add to `generate_trip_video.py` (imports at top, functions after `fetch_points`):

```python
import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

VENDOR_SCRIPT = Path(__file__).parent / "vendor" / "visualizer.py"


def resolve_setting(flag_value, env_var_name):
    if flag_value:
        return flag_value
    return os.environ.get(env_var_name)


def build_argument_parser():
    parser = argparse.ArgumentParser(description="Render a Dawarich trip as an MP4 travel video")
    parser.add_argument('--dawarich-url', default=None,
                         help="Dawarich base URL, e.g. http://localhost:3000 (or set DAWARICH_URL)")
    parser.add_argument('--api-key', default=None,
                         help="Dawarich API key, from Settings > API Keys (or set DAWARICH_API_KEY)")
    parser.add_argument('--start', required=True, type=date.fromisoformat,
                         help="Trip start date, YYYY-MM-DD (as shown on the Dawarich trip page)")
    parser.add_argument('--end', required=True, type=date.fromisoformat,
                         help="Trip end date, YYYY-MM-DD, inclusive")
    parser.add_argument('--title', required=True, help="Title overlay for the video")
    parser.add_argument('--output', default=None,
                         help="Output filename under /output (default: derived from --title)")
    parser.add_argument('--camera-movement', choices=['fixed', 'steady', 'dynamic'], default='steady')
    parser.add_argument('--long-trip-compression', choices=['off', 'gentle', 'balanced', 'strong'],
                         default='balanced')
    return parser


def run_visualizer(timeline_path, start_date, end_date, title, output_path, camera_movement, compression):
    command = [
        sys.executable, str(VENDOR_SCRIPT),
        '--input', str(timeline_path),
        '--start-date', start_date.isoformat(),
        '--end-date', end_date.isoformat(),
        '--title', title,
        '--output', str(output_path),
        '--camera-movement', camera_movement,
        '--long-trip-compression', compression,
    ]
    result = subprocess.run(command)
    return result.returncode


def main(argv=None):
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    dawarich_url = resolve_setting(args.dawarich_url, 'DAWARICH_URL')
    api_key = resolve_setting(args.api_key, 'DAWARICH_API_KEY')
    missing = [
        label for label, value in (
            ('--dawarich-url / DAWARICH_URL', dawarich_url),
            ('--api-key / DAWARICH_API_KEY', api_key),
        ) if not value
    ]
    if missing:
        print(f"Error: missing required setting(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    try:
        points = fetch_points(dawarich_url, api_key, args.start, args.end)
    except DawarichApiError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if not points:
        print(
            f"Error: No points found for {args.start}..{args.end}. "
            "Check the date range or that Dawarich has data for this period.",
            file=sys.stderr,
        )
        return 1

    timeline_data = points_to_timeline_json(points)

    output_name = args.output or f"{slugify_title(args.title)}.mp4"
    output_path = Path('/output') / output_name

    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as tmp_file:
        json.dump(timeline_data, tmp_file)
        timeline_path = Path(tmp_file.name)

    try:
        return run_visualizer(
            timeline_path, args.start, args.end, args.title, output_path,
            args.camera_movement, args.long_trip_compression,
        )
    finally:
        timeline_path.unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_generate_trip_video.py -v
```
Expected: PASS (16 tests total).

- [ ] **Step 5: Commit**

```bash
git add generate_trip_video.py tests/test_generate_trip_video.py
git commit -m "feat: wire CLI entrypoint with flag/env config resolution"
```

---

## Task 5: Docker image

**Files:**
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `Dockerfile`

**Interfaces:**
- Consumes: `generate_trip_video.py` and `vendor/visualizer.py` (Tasks 1–4), copied into the image.
- Produces: a buildable image whose `ENTRYPOINT` is `python generate_trip_video.py`.

- [ ] **Step 1: Write `requirements.txt`**

```
numpy>=1.20.0
matplotlib>=3.5.0
pillow>=9.0.0
python-dateutil>=2.8.0
requests>=2.31.0
```

- [ ] **Step 2: Write `requirements-dev.txt`**

```
pytest>=7.0.0
```

- [ ] **Step 3: Write the Dockerfile**

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY generate_trip_video.py .
COPY vendor/ vendor/

ENTRYPOINT ["python", "generate_trip_video.py"]
```

- [ ] **Step 4: Build the image**

```bash
cd /home/roger/dawarich-trip-video
docker build -t dawarich-trip-video .
```
Expected: build succeeds (this installs `matplotlib`/`numpy`/etc., so it takes a few minutes the first time).

- [ ] **Step 5: Verify the entrypoint runs and shows the expected flags**

```bash
docker run --rm dawarich-trip-video --help
```
Expected: argparse help output listing `--dawarich-url`, `--api-key`, `--start`, `--end`, `--title`, `--output`, `--camera-movement`, `--long-trip-compression` — no `--year`.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt requirements-dev.txt Dockerfile
git commit -m "feat: add Dockerfile for the trip video generator image"
```

---

## Task 6: LICENSE and README

**Files:**
- Create: `LICENSE`
- Create: `README.md`

**Interfaces:**
- Consumes: the finished CLI/image behavior from Tasks 1–5 (documents its real flags and behavior).
- Produces: nothing consumed by other tasks — this is the public-facing documentation.

- [ ] **Step 1: Write the LICENSE**

Create `LICENSE`:

```
MIT License

Copyright (c) 2026 the dawarich-trip-video contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 2: Write the README**

Create `README.md`:

```markdown
# dawarich-trip-video

Render a [Dawarich](https://dawarich.app) trip's date window as an animated
MP4 travel video, using [google-timeline-visualizer](https://github.com/mahlernim/google-timeline-visualizer)
under the hood.

Dawarich lets you cut a date range out of your location history into a
"Trip" and view it on a map. This tool fetches that same date range's points
straight from Dawarich's API and renders them as a video — no manual
Google Takeout export needed.

## How it works

1. You give it a Dawarich URL, API key, and a date range (the same range as
   a Dawarich trip).
2. It pulls all points in that range from `GET /api/v1/points`.
3. It converts them into the minimal Timeline JSON shape
   google-timeline-visualizer expects, and renders an MP4 with it.

## Why a date range instead of a trip ID

Dawarich's `Trips` feature is only available through its web UI (session/
cookie authentication), not through the versioned, API-key-authenticated
`/api/v1/...` endpoints. Rather than log in as your user or scrape HTML —
which would break on any Dawarich UI change — this tool sticks to the
official API and asks you to copy the trip's start/end dates from the
Dawarich UI instead.

## Getting a Dawarich API key

In Dawarich, go to your account settings and open the API keys page to
generate one.

## Usage

```bash
mkdir -p output
docker run --rm -v "$(pwd)/output:/output" dawarich-trip-video \
  --dawarich-url http://localhost:3000 \
  --api-key "$DAWARICH_API_KEY" \
  --start 2026-07-12 --end 2026-07-26 \
  --title "Ceara 2026"
```

The video lands in `./output/Ceara_2026.mp4`.

Both `--dawarich-url` and `--api-key` can instead be set as environment
variables (`DAWARICH_URL`, `DAWARICH_API_KEY`) if you'd rather not pass the
key as a CLI argument:

```bash
docker run --rm -v "$(pwd)/output:/output" \
  -e DAWARICH_URL=http://localhost:3000 \
  -e DAWARICH_API_KEY="$DAWARICH_API_KEY" \
  dawarich-trip-video --start 2026-07-12 --end 2026-07-26 --title "Ceara 2026"
```

### Pointing `--dawarich-url` at your Dawarich instance

Where Dawarich is reachable from *inside the container* depends on how
you've deployed it:

- **Dawarich runs on the same host, exposed on a port** (e.g. via
  `docker-compose.yml` with `ports: ["8086:3000"]`): use
  `http://host.docker.internal:8086` (Docker Desktop) or your host's LAN IP.
- **Dawarich runs in Docker and you attach this container to its network**:
  add `--network <dawarich-network-name>` to the `docker run` command above,
  then use `http://<dawarich-service-name>:3000`.
- **Dawarich is reachable over your LAN or the internet**: use that URL
  directly, e.g. `https://dawarich.example.com`.

### All options

| Flag | Required | Description |
| --- | --- | --- |
| `--dawarich-url` | yes (flag or `DAWARICH_URL`) | Dawarich base URL |
| `--api-key` | yes (flag or `DAWARICH_API_KEY`) | Dawarich API key |
| `--start` | yes | Trip start date, `YYYY-MM-DD` |
| `--end` | yes | Trip end date, `YYYY-MM-DD`, inclusive |
| `--title` | yes | Title overlay shown on the video |
| `--output` | no | Output filename under `/output` (default: derived from `--title`) |
| `--camera-movement` | no | `fixed`, `steady` (default), or `dynamic` |
| `--long-trip-compression` | no | `off`, `gentle`, `balanced` (default), or `strong` |

## Building locally

```bash
docker build -t dawarich-trip-video .
```

## Credits

The rendering engine is a vendored, patched copy of
[google-timeline-visualizer](https://github.com/mahlernim/google-timeline-visualizer)
by [@mahlernim](https://github.com/mahlernim) (MIT license), pinned to
upstream commit
[`676ee3d`](https://github.com/mahlernim/google-timeline-visualizer/commit/676ee3de8a7c0b50f4f7c84337500d9708570903).
The only change: its `--year` (calendar-year-only) filter is replaced with
an explicit `--start-date`/`--end-date` range, to match Dawarich trip
windows that don't necessarily align to a calendar year. See
`vendor/visualizer.py` for the exact patch (search for `PATCHED`).

## License

MIT — see `LICENSE`.
```

- [ ] **Step 3: Commit**

```bash
git add LICENSE README.md
git commit -m "docs: add README and LICENSE"
```

---

## Task 7: End-to-end validation against a real trip

**Files:** none (manual verification only).

**Interfaces:**
- Consumes: everything from Tasks 1–6.
- Produces: a working MP4 in a local `output/` directory, confirming the whole pipeline works exactly as a real user (following only the README) would run it.

- [ ] **Step 1: Pick a small real trip**

In the Dawarich UI, open (or create) a trip with a short date range (a few days) that you know has GPS data, and note its start/end dates exactly as shown.

- [ ] **Step 2: Run the generator exactly as documented in the README**

```bash
cd /home/roger/dawarich-trip-video
mkdir -p output
docker run --rm -v "$(pwd)/output:/output" dawarich-trip-video \
  --dawarich-url <your-dawarich-url> \
  --api-key <your-dawarich-api-key> \
  --start <trip-start-date> --end <trip-end-date> --title "<trip title>"
```

- [ ] **Step 3: Verify the output**

```bash
ls -la output/
```
Expected: an `.mp4` file named after the slugified title. Open it and confirm it shows the expected route, date range, and title overlay.

- [ ] **Step 4: Verify the error paths manually**

Run once with a date range that has no data, once with a wrong `--api-key`, and once with neither `--dawarich-url` nor `DAWARICH_URL` set, confirming the specific error messages from Task 4 appear and the container exits non-zero (`echo $?` after each run).

- [ ] **Step 5: Commit any fixes found during validation**

If Step 3 or Step 4 surfaces a bug, fix it, re-run the relevant task's tests, and commit as a normal fix commit (not folded silently into an earlier task's commit).

---

## Publishing (manual, not automated)

Once Task 7 passes, the repo is ready to go public — but pushing it and
publishing the image are visible, public actions, so they're deliberately
left as manual steps for you to run yourself when ready, not something
executed automatically as part of this plan:

```bash
# Create the GitHub repo first (via the GitHub UI or `gh repo create`),
# then:
git remote add origin git@github.com:<your-username>/dawarich-trip-video.git
git push -u origin main

# Publish the image (pick a registry/tag you control):
docker build -t ghcr.io/<your-username>/dawarich-trip-video:latest .
docker push ghcr.io/<your-username>/dawarich-trip-video:latest
```
