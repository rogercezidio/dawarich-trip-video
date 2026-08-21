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
