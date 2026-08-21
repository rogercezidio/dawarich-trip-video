import json
from datetime import date
from unittest.mock import Mock, patch

import pytest

import generate_trip_video as gtv
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
def test_fetch_points_strips_trailing_slash_from_base_url(mock_get):
    mock_get.return_value = _mock_response([{'latitude': '1', 'longitude': '2', 'timestamp': 100}])

    fetch_points('http://localhost:3000/', 'key', date(2026, 7, 12), date(2026, 7, 12))

    requested_url = mock_get.call_args[0][0]
    assert requested_url == 'http://localhost:3000/api/v1/points'


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


@patch('generate_trip_video.requests.get')
def test_fetch_points_raises_dawarich_api_error_on_invalid_json(mock_get):
    response = Mock()
    response.status_code = 200
    response.json.side_effect = json.JSONDecodeError('Expecting value', '<html>not json</html>', 0)
    response.headers = {'X-Total-Pages': '1'}
    response.text = '<html>not json</html>'
    mock_get.return_value = response

    with pytest.raises(DawarichApiError):
        fetch_points('http://localhost:3000', 'key', date(2026, 7, 12), date(2026, 7, 12))


@patch('generate_trip_video.requests.get')
def test_fetch_points_raises_dawarich_api_error_on_non_numeric_total_pages(mock_get):
    mock_get.return_value = _mock_response(
        [{'latitude': '1', 'longitude': '2', 'timestamp': 100}], total_pages='not-a-number',
    )

    with pytest.raises(DawarichApiError):
        fetch_points('http://localhost:3000', 'key', date(2026, 7, 12), date(2026, 7, 12))


def test_resolve_setting_prefers_flag_over_env(monkeypatch):
    monkeypatch.setenv('DAWARICH_URL', 'http://env-value:3000')
    assert gtv.resolve_setting('http://flag-value:3000', 'DAWARICH_URL') == 'http://flag-value:3000'


def test_resolve_setting_falls_back_to_env(monkeypatch):
    monkeypatch.setenv('DAWARICH_URL', 'http://env-value:3000')
    assert gtv.resolve_setting(None, 'DAWARICH_URL') == 'http://env-value:3000'


def test_resolve_setting_returns_none_when_neither_is_set(monkeypatch):
    monkeypatch.delenv('DAWARICH_URL', raising=False)
    assert gtv.resolve_setting(None, 'DAWARICH_URL') is None


def test_main_exits_when_start_is_after_end(monkeypatch, capsys):
    monkeypatch.setenv('DAWARICH_URL', 'http://localhost:3000')
    monkeypatch.setenv('DAWARICH_API_KEY', 'test-key')

    exit_code = gtv.main(['--start', '2026-07-26', '--end', '2026-07-12', '--title', 'Ceara 2026'])

    assert exit_code == 1
    assert '--start' in capsys.readouterr().err


@patch('generate_trip_video.output_dir_is_mounted', return_value=True)
@patch('generate_trip_video.run_visualizer', return_value=0)
@patch('generate_trip_video.fetch_points')
def test_main_happy_path_invokes_visualizer(mock_fetch, mock_run, mock_output_dir, monkeypatch):
    monkeypatch.setenv('DAWARICH_URL', 'http://localhost:3000')
    monkeypatch.setenv('DAWARICH_API_KEY', 'test-key')
    mock_fetch.return_value = [{'latitude': '1', 'longitude': '2', 'timestamp': 100}]

    exit_code = gtv.main(['--start', '2026-07-12', '--end', '2026-07-26', '--title', 'Ceara 2026'])

    assert exit_code == 0
    mock_run.assert_called_once()
    mock_fetch.assert_called_once()
    assert mock_fetch.call_args[0][0] == 'http://localhost:3000'
    assert mock_fetch.call_args[0][1] == 'test-key'


@patch('generate_trip_video.output_dir_is_mounted', return_value=True)
@patch('generate_trip_video.run_visualizer', return_value=0)
@patch('generate_trip_video.fetch_points')
def test_main_confines_output_to_output_dir(mock_fetch, mock_run, mock_output_dir, monkeypatch):
    monkeypatch.setenv('DAWARICH_URL', 'http://localhost:3000')
    monkeypatch.setenv('DAWARICH_API_KEY', 'test-key')
    mock_fetch.return_value = [{'latitude': '1', 'longitude': '2', 'timestamp': 100}]

    exit_code = gtv.main([
        '--start', '2026-07-12', '--end', '2026-07-26', '--title', 'Ceara 2026',
        '--output', '../../etc/passwd',
    ])

    assert exit_code == 0
    output_path = mock_run.call_args[0][4]
    assert output_path == gtv.Path('/output/passwd')


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


@patch('generate_trip_video.output_dir_is_mounted', return_value=True)
@patch('generate_trip_video.fetch_points')
def test_main_exits_when_no_points_found(mock_fetch, mock_output_dir, monkeypatch, capsys):
    monkeypatch.setenv('DAWARICH_URL', 'http://localhost:3000')
    monkeypatch.setenv('DAWARICH_API_KEY', 'test-key')
    mock_fetch.return_value = []

    exit_code = gtv.main(['--start', '2026-07-12', '--end', '2026-07-26', '--title', 'Ceara 2026'])

    assert exit_code == 1
    assert 'No points found' in capsys.readouterr().err


@patch('generate_trip_video.output_dir_is_mounted', return_value=True)
@patch('generate_trip_video.fetch_points')
def test_main_surfaces_dawarich_api_errors(mock_fetch, mock_output_dir, monkeypatch, capsys):
    monkeypatch.setenv('DAWARICH_URL', 'http://localhost:3000')
    monkeypatch.setenv('DAWARICH_API_KEY', 'test-key')
    mock_fetch.side_effect = gtv.DawarichApiError("Dawarich returned 500")

    exit_code = gtv.main(['--start', '2026-07-12', '--end', '2026-07-26', '--title', 'Ceara 2026'])

    assert exit_code == 1
    assert 'Dawarich returned 500' in capsys.readouterr().err


@patch('generate_trip_video.output_dir_is_mounted', return_value=False)
def test_main_exits_when_output_not_mounted(mock_output_dir, monkeypatch, capsys):
    monkeypatch.setenv('DAWARICH_URL', 'http://localhost:3000')
    monkeypatch.setenv('DAWARICH_API_KEY', 'test-key')

    exit_code = gtv.main(['--start', '2026-07-12', '--end', '2026-07-26', '--title', 'Ceara 2026'])

    assert exit_code == 1
    assert '/output' in capsys.readouterr().err
