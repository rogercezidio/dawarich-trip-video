import json
from datetime import date
from unittest.mock import Mock, patch

import pytest

from generate_trip_video import slugify_title, points_to_timeline_json, fetch_points, DawarichApiError


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
