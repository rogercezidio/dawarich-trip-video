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
