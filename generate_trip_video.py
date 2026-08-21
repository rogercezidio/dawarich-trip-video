#!/usr/bin/env python3
"""Fetch a Dawarich trip's points and render them as an MP4 via the vendored visualizer."""

import re
from datetime import datetime, timezone

import requests

DEFAULT_PER_PAGE = 500


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
