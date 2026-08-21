#!/usr/bin/env python3
# --- VENDORED COPY ---
# Source: https://github.com/mahlernim/google-timeline-visualizer
# Upstream commit: 676ee3de8a7c0b50f4f7c84337500d9708570903
# License: MIT
# Local patch: year filter (calendar-year) replaced with date-range filter
#              (--start-date/--end-date), to match Dawarich trip windows that can
#              span or fall outside a single calendar year. See patches below,
#              search this file for "PATCHED".
# --- END VENDORED COPY HEADER ---
"""
Google Timeline Visualizer
Author: @mahlernim
Description:
    Analyzes your Google Location History (Timeline.json) and generates a beautiful
    animated video of your travels for a specific year.
    Features:
    - Distance-based animation speed (majestic long trips, fast commutes)
    - Dynamic Camera (Smart Zoom & Smoothing)
    - Web Mercator Projection for perfect map alignment
    - Privacy-friendly (Month-only timestamps)
"""

import json
import argparse
import math
import sys
import io
import urllib.request
import bisect
import statistics
from datetime import datetime, timedelta, date  # PATCHED: date range support
from pathlib import Path

# Third-party imports
try:
    import numpy as np
    import matplotlib
    # Set non-interactive backend
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.animation as animation
    import matplotlib.font_manager as fm
    from PIL import Image
    import dateutil.parser
except ImportError as e:
    print(f"Error: Missing dependency {e.name}. Please run: pip install -r requirements.txt")
    sys.exit(1)

# --- CONFIGURATION DEFAULTS ---
DEFAULT_FPS = 30
DEFAULT_DURATION = 90
DEFAULT_TAIL_KM = 500
THEME_COLOR = '#ff0055' # Pink/Red
TILE_URL = "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"


class TimelineCliError(Exception):
    """Base class for expected command-line failures."""


class TimelineParseError(TimelineCliError):
    """Raised when a Timeline export cannot be decoded."""


class NoDataFoundError(TimelineCliError):
    """Raised when an export contains no usable points for the requested year."""


class FfmpegUnavailableError(TimelineCliError):
    """Raised when Matplotlib cannot find a configured ffmpeg executable."""

# Camera behavior matches the Android renderer defaults and options.
CAMERA_MOVEMENTS = {
    'fixed': dict(context_fraction=0.10, minimum_context_km=25.0, maximum_context_km=350.0,
                  padding=2.6, minimum_span=0.00060, zoom_out_alpha=0.0,
                  zoom_in_alpha=0.0, leg_aware=False, fixed_zoom=True),
    'steady': dict(context_fraction=1.00, minimum_context_km=650.0, maximum_context_km=650.0,
                   padding=2.8, minimum_span=0.00060, zoom_out_alpha=0.14,
                   zoom_in_alpha=0.035, leg_aware=False, fixed_zoom=False),
    'dynamic': dict(context_fraction=0.10, minimum_context_km=100.0, maximum_context_km=350.0,
                    padding=2.2, minimum_span=0.00045, zoom_out_alpha=0.24,
                    zoom_in_alpha=0.06, leg_aware=True, fixed_zoom=False),
}
COMPRESSION_EXPONENTS = {'off': 1.00, 'gentle': 0.92, 'balanced': 0.85, 'strong': 0.75}
TRANSFER_PADDING = 2.8
CAMERA_TRACK_SAMPLES = 480
CAMERA_DEAD_ZONE_HALF = 0.20
FIXED_ZOOM_PERCENTILE = 0.80
MIN_TRANSFER_THRESHOLD_KM = 60.0
MAX_TRANSFER_THRESHOLD_KM = 120.0
TRANSFER_TO_TYPICAL_RATIO = 3.0
DEVIATION_MULTIPLIER = 6.0

# Web Mercator Constants
R_EARTH = 6378137.0
MAX_EXTENT = 20037508.342789244

# --- PROJECTION LOGIC ---

def latlon_to_meters(lat, lon):
    x = R_EARTH * math.radians(lon)
    y = R_EARTH * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x, y

def meters_to_latlon(x, y):
    lon = math.degrees(x / R_EARTH)
    lat = math.degrees(2 * math.atan(math.exp(y / R_EARTH)) - math.pi / 2)
    return lat, lon

def haversine_dist(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def interpolate_latlon(lat1, lon1, lat2, lon2, fraction):
    """Return a great-circle position so long hops sweep instead of teleporting."""
    if fraction <= 0:
        return lat1, lon1
    if fraction >= 1:
        return lat2, lon2
    p1, l1 = math.radians(lat1), math.radians(lon1)
    p2, l2 = math.radians(lat2), math.radians(lon2)
    ax, ay, az = math.cos(p1) * math.cos(l1), math.cos(p1) * math.sin(l1), math.sin(p1)
    bx, by, bz = math.cos(p2) * math.cos(l2), math.cos(p2) * math.sin(l2), math.sin(p2)
    dot = max(-1.0, min(1.0, ax * bx + ay * by + az * bz))
    omega = math.acos(dot)
    if math.sin(omega) < 1e-8:
        left, right = 1 - fraction, fraction
    else:
        left = math.sin((1 - fraction) * omega) / math.sin(omega)
        right = math.sin(fraction * omega) / math.sin(omega)
    x, y, z = left * ax + right * bx, left * ay + right * by, left * az + right * bz
    return math.degrees(math.atan2(z, math.sqrt(x * x + y * y))), math.degrees(math.atan2(y, x))


def position_at_distance(cum_dist, lats, lons, distance_km):
    if not cum_dist:
        raise ValueError('A route needs at least one point')
    if len(cum_dist) == 1 or cum_dist[-1] <= 0:
        return lats[0], lons[0]
    distance = max(0.0, min(cum_dist[-1], distance_km))
    to_index = min(max(bisect.bisect_left(cum_dist, distance), 1), len(cum_dist) - 1)
    segment = cum_dist[to_index] - cum_dist[to_index - 1]
    fraction = 0.0 if segment <= 0 else (distance - cum_dist[to_index - 1]) / segment
    return interpolate_latlon(
        lats[to_index - 1], lons[to_index - 1], lats[to_index], lons[to_index], fraction,
    )


def transfer_threshold_km(cum_dist):
    hops = [after - before for before, after in zip(cum_dist, cum_dist[1:])]
    ordinary = sorted(hop for hop in hops if 0 < hop < MAX_TRANSFER_THRESHOLD_KM)
    if not ordinary:
        return MAX_TRANSFER_THRESHOLD_KM
    typical = statistics.median(ordinary)
    deviation = statistics.median(sorted(abs(hop - typical) for hop in ordinary))
    threshold = max(
        MIN_TRANSFER_THRESHOLD_KM,
        typical * TRANSFER_TO_TYPICAL_RATIO,
        typical + deviation * DEVIATION_MULTIPLIER,
    )
    return min(MAX_TRANSFER_THRESHOLD_KM, threshold)


def build_legs(cum_dist, threshold_km=None):
    if len(cum_dist) < 2 or cum_dist[-1] <= 0:
        return []
    threshold = threshold_km if threshold_km is not None else transfer_threshold_km(cum_dist)
    legs = []
    local_start = 0.0
    for before, after in zip(cum_dist, cum_dist[1:]):
        if after - before < max(1.0, threshold):
            continue
        if before > local_start:
            legs.append((local_start, before, False))
        legs.append((before, after, True))
        local_start = after
    if cum_dist[-1] > local_start:
        legs.append((local_start, cum_dist[-1], False))
    return legs


def leg_at(legs, distance_km, total_km):
    if not legs:
        return 0.0, total_km, False
    starts = [leg[0] for leg in legs]
    index = bisect.bisect_right(starts, max(0.0, min(total_km, distance_km))) - 1
    return legs[max(0, min(index, len(legs) - 1))]


def _endpoint_slope(first_width, second_width, first, second):
    slope = ((2 * first_width + second_width) * first - first_width * second) / (first_width + second_width)
    if slope <= 0:
        return 0.0
    return min(slope, 3 * first)


def _monotone_slopes(x_values, y_values):
    count = len(x_values) - 1
    delta = [(y_values[i + 1] - y_values[i]) / (x_values[i + 1] - x_values[i]) for i in range(count)]
    if count == 1:
        return [delta[0], delta[0]]
    slopes = [0.0] * len(x_values)
    slopes[0] = _endpoint_slope(x_values[1] - x_values[0], x_values[2] - x_values[1], delta[0], delta[1])
    for index in range(1, len(x_values) - 1):
        before_width = x_values[index] - x_values[index - 1]
        after_width = x_values[index + 1] - x_values[index]
        weight_before = 2 * after_width + before_width
        weight_after = after_width + 2 * before_width
        slopes[index] = (weight_before + weight_after) / (
            weight_before / delta[index - 1] + weight_after / delta[index]
        )
    slopes[-1] = _endpoint_slope(
        x_values[-1] - x_values[-2], x_values[-2] - x_values[-3], delta[-1], delta[-2],
    )
    return slopes


def build_journey_timing(cum_dist, compression):
    """Return a progress-to-raw-distance mapper without changing route geometry."""
    total_km = cum_dist[-1] if cum_dist else 0.0
    exponent = COMPRESSION_EXPONENTS[compression]
    if compression == 'off' or len(cum_dist) < 2:
        return lambda progress: total_km * max(0.0, min(1.0, progress))
    distances = [0.0]
    effective = [0.0]
    effective_total = 0.0
    for before, after in zip(cum_dist, cum_dist[1:]):
        segment = after - before
        if segment <= 0:
            continue
        effective_total += segment ** exponent
        distances.append(after)
        effective.append(effective_total)
    if effective_total <= 0 or len(distances) < 2:
        return lambda progress: total_km * max(0.0, min(1.0, progress))
    x_values = [value / effective_total for value in effective]
    slopes = _monotone_slopes(x_values, distances)

    def distance_at(progress):
        elapsed = max(0.0, min(1.0, progress))
        to_index = min(max(bisect.bisect_left(x_values, elapsed), 1), len(x_values) - 1)
        from_index = to_index - 1
        width = x_values[to_index] - x_values[from_index]
        t = 0.0 if width <= 0 else (elapsed - x_values[from_index]) / width
        t2, t3 = t * t, t * t * t
        return ((2 * t3 - 3 * t2 + 1) * distances[from_index]
                + (t3 - 2 * t2 + t) * width * slopes[from_index]
                + (-2 * t3 + 3 * t2) * distances[to_index]
                + (t3 - t2) * width * slopes[to_index])

    return distance_at

# --- MAP TILES (Web Mercator) ---

def meters_to_tile(mx, my, zoom):
    n = 2.0 ** zoom
    norm_x = (mx + MAX_EXTENT) / (2 * MAX_EXTENT)
    norm_y = 1.0 - (my + MAX_EXTENT) / (2 * MAX_EXTENT)
    xtile = int(norm_x * n)
    ytile = int(norm_y * n)
    return xtile, ytile

def tile_to_bounds_meters(xtile, ytile, zoom):
    n = 2.0 ** zoom
    tile_size = (2 * MAX_EXTENT) / n
    min_x = -MAX_EXTENT + xtile * tile_size
    max_x = min_x + tile_size
    max_y = MAX_EXTENT - ytile * tile_size
    min_y = max_y - tile_size
    return min_x, max_x, min_y, max_y

# Rendering is a single short-lived process, so retaining decoded tiles avoids repeated downloads.
TILE_CACHE = {}


def fetch_tile_img(x, y, z):
    key = (x, y, z)
    if key in TILE_CACHE: return TILE_CACHE[key]
    url = TILE_URL.format(z=z, x=x, y=y)
    try:
        req = urllib.request.Request(url, headers={'User-Agent': "Mozilla/5.0"})
        with urllib.request.urlopen(req) as response:
            img = Image.open(io.BytesIO(response.read())).convert('RGB')
            TILE_CACHE[key] = img
            return img
    except Exception:
        # Return fallback tile (gray)
        return Image.new('RGB', (256, 256), (240, 240, 240))

def get_map_image(x_center, y_center, span, width_px=800):
    # Calculate target zoom
    # Resolution (m/px) needed = span / width_px
    # Base resolution (z=0) = (2 * MAX_EXTENT) / 256
    # Res at z = Base / 2^z
    # 2^z = (Base / Res_needed) = (2*MAX / 256) / (span / width)
    # 2^z = (2 * MAX * width) / (256 * span)
    
    target_val = (2 * MAX_EXTENT * width_px) / (256 * max(span, 1.0))
    zoom = int(math.log2(target_val)) if target_val > 0 else 2
    zoom = max(2, min(15, zoom)) # Limit zoom range
    
    min_x = x_center - span/2
    max_x = x_center + span/2
    min_y = y_center - span/2
    max_y = y_center + span/2
    
    xt_min, yt_min = meters_to_tile(min_x, max_y, zoom)
    xt_max, yt_max = meters_to_tile(max_x, min_y, zoom)
    
    if yt_min > yt_max: yt_min, yt_max = yt_max, yt_min
    
    x_tiles = xt_max - xt_min + 1
    y_tiles = yt_max - yt_min + 1
    
    # Safety clamp: if view is too huge, reduce zoom
    while x_tiles * y_tiles > 25 and zoom > 2:
        zoom -= 1
        xt_min, yt_min = meters_to_tile(min_x, max_y, zoom)
        xt_max, yt_max = meters_to_tile(max_x, min_y, zoom)
        if yt_min > yt_max: yt_min, yt_max = yt_max, yt_min
        x_tiles = xt_max - xt_min + 1
        y_tiles = yt_max - yt_min + 1

    tile_w, tile_h = 256, 256
    stitched = Image.new('RGB', (x_tiles * tile_w, y_tiles * tile_h))
    
    # Calculate exact bounds of the stitched image
    tl_min_x, tl_max_x, tl_min_y, tl_max_y = tile_to_bounds_meters(xt_min, yt_min, zoom)
    br_min_x, br_max_x, br_min_y, br_max_y = tile_to_bounds_meters(xt_max, yt_max, zoom)
    
    final_min_x = tl_min_x
    final_max_x = br_max_x # technically bounds of xt_max tile
    final_max_y = tl_max_y
    final_min_y = br_min_y
    
    for x in range(x_tiles):
        for y in range(y_tiles):
            img = fetch_tile_img(xt_min + x, yt_min + y, zoom)
            stitched.paste(img, (x * tile_w, y * tile_h))
            
    return stitched, (final_min_x, final_max_x, final_min_y, final_max_y)

# --- DATA PROCESSING ---

def parse_coordinate(value):
    """Parse coordinate values used by Android, iOS, and Takeout exports."""
    if isinstance(value, dict):
        value = value.get('latLng') or value.get('point')
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = value.strip().removeprefix('geo:').split('?', 1)[0].replace('°', '').replace(' ', '')
    parts = cleaned.split(',')
    if len(parts) < 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if abs(lat) > 1_000_000 or abs(lon) > 1_000_000:
        lat /= 10_000_000
        lon /= 10_000_000
    if not (-85.05112878 <= lat <= 85.05112878 and -180 <= lon <= 180):
        return None
    return lat, lon


def extract_timeline_points(data, start_date, end_date):  # PATCHED
    """Return normalized points within [start_date, end_date] from supported Timeline JSON roots."""
    if isinstance(data, list):
        segments = data
    elif isinstance(data, dict):
        segments = data.get('semanticSegments', [])
    else:
        raise ValueError('Timeline JSON must start with an object or array')

    canonical_points = []
    standalone_path_points = []
    semantic_intervals = []

    def parse_timestamp(time_value):
        if isinstance(time_value, datetime):
            return time_value
        if not time_value:
            return None
        try:
            return dateutil.parser.parse(time_value)
        except (TypeError, ValueError, OverflowError):
            return None

    def path_timestamp(path_point, start_value, end_value):
        absolute = parse_timestamp(path_point.get('time'))
        if absolute is not None:
            return absolute
        offset_value = path_point.get('durationMinutesOffsetFromStartTime')
        if isinstance(offset_value, bool):
            return None
        try:
            offset = int(offset_value)
        except (TypeError, ValueError, OverflowError):
            return None
        if offset < 0:
            return None
        start = parse_timestamp(start_value)
        if start is None:
            return None
        try:
            timestamp = start + timedelta(minutes=offset)
        except OverflowError:
            return None
        end = parse_timestamp(end_value)
        if end is not None and timestamp > end + timedelta(minutes=1):
            return None
        return timestamp

    def add_point(output, timestamp, coordinate):  # PATCHED
        if timestamp is None or coordinate is None:
            return False
        if start_date <= timestamp.date() <= end_date:
            output.append({'dt': timestamp, 'lat': coordinate[0], 'lon': coordinate[1]})
        return True

    for seg in segments:
        if not isinstance(seg, dict):
            continue
        start_time = seg.get('startTime')
        end_time = seg.get('endTime')

        activity = seg.get('activity')
        visit = seg.get('visit')
        candidate = visit.get('topCandidate') if isinstance(visit, dict) else None
        activity_start = activity.get('start') if isinstance(activity, dict) else None
        activity_end = activity.get('end') if isinstance(activity, dict) else None
        visit_location = candidate.get('placeLocation') if isinstance(candidate, dict) else None
        activity_start_coordinate = parse_coordinate(activity_start)
        activity_end_coordinate = parse_coordinate(activity_end)
        visit_coordinate = parse_coordinate(visit_location)
        has_usable_semantic_record = any(
            coordinate is not None
            for coordinate in (activity_start_coordinate, activity_end_coordinate, visit_coordinate)
        )
        path_output = canonical_points if has_usable_semantic_record else standalone_path_points

        for path_point in seg.get('timelinePath', []):
            if isinstance(path_point, dict):
                add_point(
                    path_output,
                    path_timestamp(path_point, start_time, end_time),
                    parse_coordinate(path_point.get('point')),
                )

        start = parse_timestamp(start_time) if has_usable_semantic_record else None
        end = parse_timestamp(end_time) if has_usable_semantic_record else None
        if isinstance(activity, dict):
            add_point(canonical_points, start, activity_start_coordinate)
            add_point(canonical_points, end, activity_end_coordinate)

        if isinstance(candidate, dict):
            add_point(canonical_points, start, visit_coordinate)

        if has_usable_semantic_record:
            if (
                start is not None
                and end is not None
                and start.utcoffset() is not None
                and end.utcoffset() is not None
                and end >= start
            ):
                semantic_intervals.append((start, end))

    semantic_intervals.sort(key=lambda interval: interval[0])
    merged_intervals = []
    for start, end in semantic_intervals:
        if not merged_intervals or start > merged_intervals[-1][1]:
            merged_intervals.append([start, end])
        elif end > merged_intervals[-1][1]:
            merged_intervals[-1][1] = end

    # Some direct-array exports append a second path-only history for periods already
    # represented by activities and visits. Same-segment path detail stays canonical.
    interval_index = 0
    for point in sorted(standalone_path_points, key=lambda item: item['dt']):
        timestamp = point['dt']
        if timestamp.utcoffset() is None:
            canonical_points.append(point)
            continue
        while interval_index < len(merged_intervals) and merged_intervals[interval_index][1] < timestamp:
            interval_index += 1
        covered = (
            interval_index < len(merged_intervals)
            and merged_intervals[interval_index][0] <= timestamp <= merged_intervals[interval_index][1]
        )
        if not covered:
            canonical_points.append(point)

    unique = {
        (point['dt'], point['lat'], point['lon']): point
        for point in canonical_points
    }
    return sorted(unique.values(), key=lambda point: point['dt'])

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
        
    print(f"Found {len(points)} valid points.")
    
    # Process into arrays & Project
    timestamps = []
    lats = []
    lons = []
    xs = []
    ys = []
    
    for p in points:
        timestamps.append(p['dt'])
        lats.append(p['lat'])
        lons.append(p['lon'])
        x, y = latlon_to_meters(p['lat'], p['lon'])
        xs.append(x)
        ys.append(y)
        
    # Calculate Distances (Haversine) for animation timing
    cum_dist = [0.0]
    total = 0.0
    for i in range(1, len(lats)):
        d = haversine_dist(lats[i-1], lons[i-1], lats[i], lons[i])
        total += d
        cum_dist.append(total)
        
    return timestamps, xs, ys, cum_dist, lats, lons


def raw_camera_sample(cum_dist, xs, ys, lats, lons, distance_km, movement_name, legs):
    movement = CAMERA_MOVEMENTS[movement_name]
    total_km = cum_dist[-1]
    latitude, longitude = position_at_distance(cum_dist, lats, lons, distance_km)
    center_x, center_y = latlon_to_meters(latitude, longitude)
    context = max(
        movement['minimum_context_km'],
        min(movement['maximum_context_km'], total_km * movement['context_fraction']),
    )
    leg = leg_at(legs, distance_km, total_km) if movement['leg_aware'] else None
    if leg is not None and leg[2]:
        context = leg[1] - leg[0]
        padding = TRANSFER_PADDING
        range_start = leg[0]
        lookahead_limit = leg[1]
    else:
        padding = movement['padding']
        range_start = leg[0] if leg is not None else 0.0
        lookahead_limit = total_km
    tail_distance = max(range_start, distance_km - context)
    lookahead_distance = min(lookahead_limit, distance_km + context)
    start_index = bisect.bisect_left(cum_dist, tail_distance)
    end_index = bisect.bisect_right(cum_dist, lookahead_distance)
    focus_x = list(xs[start_index:end_index])
    focus_y = list(ys[start_index:end_index])
    for edge in (tail_distance, distance_km, lookahead_distance):
        edge_lat, edge_lon = position_at_distance(cum_dist, lats, lons, edge)
        edge_x, edge_y = latlon_to_meters(edge_lat, edge_lon)
        focus_x.append(edge_x)
        focus_y.append(edge_y)
    minimum_span = movement['minimum_span'] * (2 * MAX_EXTENT)
    span = max(
        (max(focus_x) - min(focus_x)) * padding,
        (max(focus_y) - min(focus_y)) * padding,
        minimum_span,
    )
    return center_x, center_y, min(span, 0.72 * 2 * MAX_EXTENT)


def build_camera_track(cum_dist, xs, ys, lats, lons, movement_name, distance_at):
    movement = CAMERA_MOVEMENTS[movement_name]
    legs = build_legs(cum_dist)
    raw = [
        raw_camera_sample(
            cum_dist, xs, ys, lats, lons,
            distance_at(sample / CAMERA_TRACK_SAMPLES), movement_name, legs,
        )
        for sample in range(CAMERA_TRACK_SAMPLES + 1)
    ]
    fixed_span = None
    if movement['fixed_zoom']:
        spans = sorted(sample[2] for sample in raw)
        fixed_span = spans[int((len(spans) - 1) * FIXED_ZOOM_PERCENTILE)]
    track = []
    for raw_x, raw_y, raw_span in raw:
        target_span = fixed_span if fixed_span is not None else raw_span
        if not track:
            track.append((raw_x, raw_y, target_span))
            continue
        center_x, center_y, previous_span = track[-1]
        alpha = movement['zoom_out_alpha'] if target_span > previous_span else movement['zoom_in_alpha']
        span = target_span if movement['fixed_zoom'] else math.exp(
            math.log(previous_span) + (math.log(target_span) - math.log(previous_span)) * alpha,
        )
        dead_half = span * CAMERA_DEAD_ZONE_HALF
        if raw_x < center_x - dead_half:
            center_x = raw_x + dead_half
        elif raw_x > center_x + dead_half:
            center_x = raw_x - dead_half
        if raw_y < center_y - dead_half:
            center_y = raw_y + dead_half
        elif raw_y > center_y + dead_half:
            center_y = raw_y - dead_half
        track.append((center_x, center_y, span))
    return track


def camera_at(track, progress):
    position = max(0.0, min(1.0, progress)) * (len(track) - 1)
    from_index = int(math.floor(position))
    to_index = min(from_index + 1, len(track) - 1)
    fraction = position - from_index
    before, after = track[from_index], track[to_index]
    center_x = before[0] + (after[0] - before[0]) * fraction
    center_y = before[1] + (after[1] - before[1]) * fraction
    span = math.exp(math.log(before[2]) + (math.log(after[2]) - math.log(before[2])) * fraction)
    return center_x, center_y, span

def existing_file(value):
    path = Path(value)
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"input file does not exist or is not a file: {value}")
    return path


def ensure_ffmpeg_available():
    if not animation.writers.is_available('ffmpeg'):
        raise FfmpegUnavailableError(
            "ffmpeg is required to create MP4 video. Install ffmpeg or configure Matplotlib's animation.ffmpeg_path.",
        )


def build_argument_parser():
    parser = argparse.ArgumentParser(description="Google Timeline Visualizer")
    parser.add_argument('--input', '-i', required=True, type=existing_file, help="Path to Timeline.json")
    parser.add_argument('--start-date', required=True, type=date.fromisoformat,  # PATCHED
                        help="Start of the date range to visualize (YYYY-MM-DD)")
    parser.add_argument('--end-date', required=True, type=date.fromisoformat,  # PATCHED
                        help="End of the date range to visualize (YYYY-MM-DD, inclusive)")
    parser.add_argument('--output', '-o', default='travel_history.mp4', help="Output video path")
    parser.add_argument('--title', '-t', default="My Trips", help="Title displayed on video")
    parser.add_argument('--camera-movement', choices=CAMERA_MOVEMENTS, default='steady',
                        help="Camera behavior: fixed, steady, or dynamic")
    parser.add_argument('--long-trip-compression', choices=COMPRESSION_EXPONENTS, default='balanced',
                        help="Timing compression: off, gentle, balanced, or strong")
    return parser


def main(argv=None):
    parser = build_argument_parser()

    args = parser.parse_args(argv)

    try:
        ensure_ffmpeg_available()
        timestamps, xs, ys, cum_dist, lats, lons = parse_timeline(args.input, args.start_date, args.end_date)  # PATCHED
    except TimelineCliError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    
    total_km = cum_dist[-1]
    print(f"Total distance: {total_km:.1f} km")
    
    # Prepare frame positions from softened distance timing while retaining raw route geometry.
    total_frames = DEFAULT_FPS * DEFAULT_DURATION
    distance_at = build_journey_timing(cum_dist, args.long_trip_compression)
    print(f"Target: {DEFAULT_DURATION}s @ {DEFAULT_FPS}fps. Compression: {args.long_trip_compression}")

    frame_progress = [i / total_frames for i in range(total_frames)]
    frames_dist = [distance_at(progress) for progress in frame_progress]
    frame_indices = []
    frame_points = []
    for d in frames_dist:
        idx = min(max(bisect.bisect_right(cum_dist, d) - 1, 0), len(cum_dist)-1)
        frame_indices.append(idx)
        frame_points.append(latlon_to_meters(*position_at_distance(cum_dist, lats, lons, d)))

    # Camera Calculation
    print(f"Calculating {args.camera_movement} camera path...")
    camera_track = build_camera_track(cum_dist, xs, ys, lats, lons, args.camera_movement, distance_at)
    cameras = [camera_at(camera_track, progress) for progress in frame_progress]
    cam_centers = [(camera[0], camera[1]) for camera in cameras]
    cam_spans = [camera[2] for camera in cameras]
        
    # Visualization
    print("Setting up animation...")
    fig, ax = plt.subplots(figsize=(10,10))
    # Remove whitespace
    fig.subplots_adjust(left=0, bottom=0, right=1, top=1, wspace=None, hspace=None)
    ax.axis('off')
    
    # Init Layers
    # Initial Map
    init_cx, init_cy = cam_centers[0]
    init_span = cam_spans[0]
    init_img, init_ext = get_map_image(init_cx, init_cy, init_span)
    
    map_layer = ax.imshow(init_img, extent=init_ext, aspect='equal')
    
    path_line, = ax.plot([], [], color=THEME_COLOR, alpha=0.5, linewidth=2)
    tail_line, = ax.plot([], [], color=THEME_COLOR, linewidth=4, alpha=1.0)
    head_point, = ax.plot([], [], color='black', marker='o', markersize=8, markeredgecolor=THEME_COLOR)
    
    # UI Elements
    title_text = ax.text(0.5, 0.95, args.title, transform=ax.transAxes, 
                         color='black', fontsize=16, fontweight='bold', ha='center', va='top',
                         bbox=dict(facecolor='white', alpha=0.5, edgecolor='none', pad=5))
                         
    date_text = ax.text(0.5, 0.90, '', transform=ax.transAxes, 
                        color='gray', fontsize=14, ha='center', va='top',
                        bbox=dict(facecolor='white', alpha=0.5, edgecolor='none', pad=3))

    def update(i):
        frame_idx = frame_indices[i]
        cx, cy = cam_centers[i]
        span = cam_spans[i]
        
        ax.set_xlim(cx - span/2, cx + span/2)
        ax.set_ylim(cy - span/2, cy + span/2)
        
        # dynamic map update (every 5 frames)
        if i % 5 == 0:
            img, ext = get_map_image(cx, cy, span)
            map_layer.set_data(img)
            map_layer.set_extent(ext)
            
        # Path
        head_x, head_y = frame_points[i]
        _xs = list(xs[:frame_idx+1]) + [head_x]
        _ys = list(ys[:frame_idx+1]) + [head_y]
        path_line.set_data(_xs, _ys)
        
        # Tail
        curr_km = frames_dist[i]
        start_km = max(0, curr_km - DEFAULT_TAIL_KM)
        start_idx = bisect.bisect_left(cum_dist, start_km)
        
        txs = list(xs[start_idx : frame_idx+1]) + [head_x]
        tys = list(ys[start_idx : frame_idx+1]) + [head_y]
        tail_line.set_data(txs, tys)
        
        head_point.set_data([head_x], [head_y])
            
        if timestamps:
            date_text.set_text(timestamps[frame_idx].strftime('%B %Y'))
            
        return map_layer, path_line, tail_line, head_point, date_text,

    print(f"Generating {len(frame_indices)} frames...")
    ani = animation.FuncAnimation(fig, update, frames=len(frame_indices), blit=False)
    
    print(f"Saving to {args.output}...")
    ani.save(args.output, writer='ffmpeg', fps=DEFAULT_FPS, dpi=100)
    print("Done!")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
