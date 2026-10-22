import importlib.util
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("matplotlib")
from PIL import Image

VISUALIZER_PATH = Path(__file__).parent.parent / "vendor" / "visualizer.py"


@pytest.fixture
def visualizer():
    spec = importlib.util.spec_from_file_location("visualizer", VISUALIZER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _png_bytes():
    buf = io.BytesIO()
    Image.new('RGB', (256, 256), (10, 20, 30)).save(buf, format='PNG')
    return buf.getvalue()


def test_fetch_tile_img_requests_osm_with_identifying_user_agent(visualizer):
    response = MagicMock()
    response.__enter__.return_value.read.return_value = _png_bytes()

    with patch.object(visualizer.urllib.request, 'urlopen', return_value=response) as mock_urlopen:
        img = visualizer.fetch_tile_img(11, 18, 5)

    request = mock_urlopen.call_args.args[0]
    assert request.full_url == "https://tile.openstreetmap.org/5/11/18.png"
    # OSM's tile usage policy blocks generic/faked User-Agents like "Mozilla/5.0".
    user_agent = request.get_header('User-agent')
    assert "dawarich-trip-video" in user_agent
    assert img.getpixel((0, 0)) == (10, 20, 30)
