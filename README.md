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

To run the test suite locally, install development dependencies with `pip install -r requirements-dev.txt && python -m pytest`. Note that `requirements-dev.txt` is not needed for the Docker image. (Use `python -m pytest`, not a bare `pytest`, so the repo root is on `sys.path`.)

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
