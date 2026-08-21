#!/usr/bin/env python3
"""Fetch a Dawarich trip's points and render them as an MP4 via the vendored visualizer."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import requests

DEFAULT_PER_PAGE = 500
VENDOR_SCRIPT = Path(__file__).parent / "vendor" / "visualizer.py"


class DawarichApiError(Exception):
    """Raised when the Dawarich API call fails in a way the user must fix."""


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


def fetch_points(base_url, api_key, start_date, end_date, per_page=DEFAULT_PER_PAGE):
    """Fetch all points in [start_date, end_date] from the Dawarich points API, paginated."""
    base_url = base_url.rstrip('/')
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

        request_url = f'{base_url}/api/v1/points'
        try:
            page_points = response.json()
        except json.JSONDecodeError as error:
            raise DawarichApiError(
                f"Dawarich returned a non-JSON response for {request_url} "
                "(check that --dawarich-url actually points at Dawarich, not a login page "
                f"or reverse proxy): {error}",
            ) from error
        points.extend(page_points)

        try:
            total_pages = int(response.headers.get('X-Total-Pages', '1') or '1')
        except ValueError as error:
            raise DawarichApiError(
                f"Dawarich returned a non-numeric X-Total-Pages header for {request_url}: {error}",
            ) from error
        if params['page'] >= total_pages or not page_points:
            break
        params['page'] += 1

    return points


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


def output_dir_is_mounted():
    return Path('/output').is_dir()


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

    if args.start > args.end:
        print("Error: --start must not be after --end.", file=sys.stderr)
        return 1

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

    if not output_dir_is_mounted():
        print(
            "Error: /output is not mounted — pass -v <host-dir>:/output to `docker run`.",
            file=sys.stderr,
        )
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

    output_name = Path(args.output).name if args.output else f"{slugify_title(args.title)}.mp4"
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
