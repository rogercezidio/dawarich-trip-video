# dawarich-trip-video — Design Spec

**Date:** 2026-08-21
**Status:** Approved, pending implementation plan

## Problem

Dawarich (self-hosted location history) lets a user cut arbitrary date
windows out of their location history as "Trips" (name + start/end date
+ notes, viewable on an interactive map). Separately, the third-party
project [google-timeline-visualizer](https://github.com/mahlernim/google-timeline-visualizer)
renders an animated MP4 travel video from a `Timeline.json` export, but
has no notion of trips and no API — it's a standalone script.

Goal: a small, standalone, publicly-usable open-source tool that reads
a Dawarich trip's date window and Points data and renders it as an MP4
via google-timeline-visualizer — usable by any self-hosted Dawarich
user, not tied to one person's deployment.

This supersedes an earlier draft
(see the sibling project's `docs/superpowers/specs/2026-08-21-trip-video-generator-design.md`)
that wired the same idea directly into one person's private
`docker-compose.yml`. The technical findings below (Dawarich's API
contract, google-timeline-visualizer's input format and `--year`
limitation) carry over unchanged; what changes is packaging, config,
and audience.

## Constraints discovered during research

- Dawarich's `Trips` resource (`app/controllers/trips_controller.rb`)
  is **not** part of the versioned, token-authenticated API
  (`/api/v1/...`). It's a session-cookie (Devise) web controller only.
- Dawarich's `Points` resource **is** part of the versioned API:
  `GET /api/v1/points?start_at=&end_at=&per_page=` (paginated, headers
  `X-Current-Page` / `X-Total-Pages`), authenticated via
  `Authorization: Bearer <api_key>`. With `slim=true`, each point
  serializes as `{latitude: str, longitude: str, timestamp: int (unix
  epoch seconds), ...}`.
- google-timeline-visualizer's Python script (`visualizer.py`) has no
  isolated rendering function — parsing and rendering are coupled
  inside `main()`. It cannot be imported and called as a library
  without refactoring upstream code; it must be invoked as a
  subprocess.
- Its minimum valid input is a small `semanticSegments` JSON structure:
  ```json
  {"semanticSegments": [{"startTime": "...", "endTime": "...",
    "timelinePath": [{"point": "lat,lon", "time": "..."}]}]}
  ```
- Its date filter is `--year` (a whole calendar year only). A Dawarich
  trip can span an arbitrary window, including across a year boundary,
  so the upstream filter doesn't match Dawarich's trip semantics.

## Decisions (confirmed with user)

1. **Audience:** a standalone open-source tool, not a PR into either
   upstream project. No dependency on either maintainer's review or
   release cadence.
2. **Date-range filtering:** vendor a copy of `visualizer.py` and patch
   its year filter to accept an explicit `--start-date`/`--end-date`
   range instead of `--year`. A header comment records the exact
   upstream commit SHA vendored, to ease re-syncing later.
3. **Config:** fully generic — `--dawarich-url` and `--api-key` CLI
   flags, each falling back to `DAWARICH_URL` / `DAWARICH_API_KEY`
   environment variables if the flag is omitted (env var lets a user
   avoid putting the key in shell history). Neither has a code default:
   the tool runs inside a container, so a default like
   `http://localhost:3000` would silently mean "this container", not
   "the user's Dawarich host" — a footgun. Both are required; missing
   either produces a clear error before any network call. The README
   documents the right value for common setups (same host via
   `host.docker.internal`, a shared Docker network via `--network`, a
   LAN/remote URL).
4. **Trigger:** manual CLI invocation with an explicit date range and
   title — not automatic/webhook-driven.
5. **Trip window source:** the user reads the trip's start/end dates
   off the Dawarich UI and passes them as `--start`/`--end` CLI flags.
   The tool does **not** call Dawarich's `Trips` web routes (no
   session-login, no HTML scraping) — only the official `Points` API
   with a scoped API key. This trades one manual copy-paste step for
   not depending on unversioned, cookie-authenticated internals — true
   regardless of who's running the tool.
6. **Distribution:** a single published Docker image (e.g.
   `ghcr.io/<user>/dawarich-trip-video`), run via `docker run`. No
   docker-compose file ships with the tool — it's a one-shot CLI, not
   a long-running service, and every user's Dawarich deployment/network
   topology differs. No PyPI package for v1 (YAGNI — add later if
   requested).
7. **Output:** the container writes to `/output`; the user bind-mounts
   their own host directory there (`-v $(pwd)/output:/output`).
8. **Repo location:** a new, independent git repository at
   `~/dawarich-trip-video`, with no reference to any specific person's
   deployment. `git init` and local commits happen as part of building
   it; pushing to a public GitHub remote and publishing the Docker
   image are actions the user triggers explicitly later — not
   automated as part of the build.
9. **Licensing:** MIT (compatible with the vendored MIT-licensed
   google-timeline-visualizer code; LICENSE file credits upstream).
10. **CI:** out of scope for v1. No GitHub Actions auto-publish
    pipeline — image is built and pushed manually until that becomes a
    real pain point.

## Architecture

```
docker run --rm -v $(pwd)/output:/output ghcr.io/<user>/dawarich-trip-video \
  --dawarich-url http://localhost:3000 --api-key $DAWARICH_API_KEY \
  --start 2026-07-12 --end 2026-07-26 --title "Ceara 2026"
        │
        ▼
generate_trip_video.py (entrypoint)
        │  1. resolve --dawarich-url / --api-key (flag > env > default)
        │  2. paginate GET {dawarich_url}/api/v1/points?start_at=&end_at=
        │  3. build minimal semanticSegments JSON from the points
        │  4. write it to a temp file
        │  5. invoke vendor/visualizer.py as a subprocess with
        │     --input <tmp.json> --start-date --end-date --title
        │     --camera-movement --long-trip-compression --output
        ▼
vendor/visualizer.py (vendored + patched: --year → date range)
        │  parses JSON → renders frames → ffmpeg encode
        ▼
/output/Ceara_2026.mp4  (bind-mounted to the user's chosen host dir)
```

## Components

### Repo layout (`~/dawarich-trip-video`, repo root)

```
dawarich-trip-video/
├── generate_trip_video.py
├── vendor/
│   └── visualizer.py
├── tests/
│   └── test_generate_trip_video.py
├── Dockerfile
├── requirements.txt
├── requirements-dev.txt
├── README.md
└── LICENSE
```

- **`Dockerfile`** — `python:3.11-slim` base, installs `ffmpeg` via
  apt, installs `requirements.txt` (upstream visualizer deps +
  `requests`). `ENTRYPOINT ["python", "generate_trip_video.py"]`.
- **`requirements.txt`** — upstream visualizer's Python deps plus
  `requests`.
- **`vendor/visualizer.py`** — vendored copy of upstream `visualizer.py`,
  MIT-licensed. Patch: replace `--year`/`type=int` argparse option and
  the `timestamp.year == year` check with `--start-date`/`--end-date`
  (`type=date.fromisoformat`) and a `start_date <= timestamp.date() <=
  end_date` check. Header comment records the upstream commit SHA.
- **`generate_trip_video.py`** — entrypoint script:
  - argparse: `--dawarich-url` and `--api-key` (each optional as a
    flag, falling back to `DAWARICH_URL`/`DAWARICH_API_KEY` env vars,
    but with no code default — an error if neither flag nor env var
    resolves either one), `--start`, `--end` (ISO dates, required),
    `--title` (required), `--output` (optional filename override),
    plus pass-through `--camera-movement` /
    `--long-trip-compression` with the same defaults/choices as
    upstream.
  - Paginates `/api/v1/points` for the given range (respects
    `X-Total-Pages`; requests are ordered `asc`, `slim=true`).
  - If zero points are returned, exits immediately with a clear error
    instead of letting the vendored script fail deeper in the
    pipeline.
  - Builds one `semanticSegments` entry from the points (`point`/`time`
    per point, `startTime`/`endTime` spanning the whole set), writes to
    a temp JSON file.
  - Invokes `vendor/visualizer.py` as a subprocess, streaming its
    stdout/stderr through directly (no swallowing errors), and exits
    with its return code.
- **`README.md`** — install/usage instructions, worked `docker run`
  examples for the common deployment shapes (Dawarich on the same
  host, Dawarich reachable on a LAN/remote URL, Dawarich on a Docker
  network the container can be attached to via `--network`), how to
  generate a Dawarich API key, the `Trips`-API limitation and why
  `--start`/`--end` are manual, and credit + the exact vendored commit
  SHA for google-timeline-visualizer.
- **`LICENSE`** — MIT.

## Error handling

- No points in range → exits early with a clear message, never reaches
  the vendored script.
- Dawarich API returns 401 → message pointing at `--api-key`/
  `DAWARICH_API_KEY`.
- Dawarich API unreachable (network/DNS) → surfaces the connection
  error together with the URL it tried (helps users debug their
  `--dawarich-url`/network setup, which varies per deployment).
- `--dawarich-url` or `--api-key` unresolved (no flag, no env var for
  either) → clear error before any network call.
- ffmpeg/rendering failure inside `vendor/visualizer.py` → its stderr
  is not swallowed; the subprocess call streams output live and
  propagates the exit code.

## Testing

- The point-list → `semanticSegments` JSON builder and the Dawarich API
  client are pure/mockable; unit-tested with `pytest` and mocked HTTP,
  no Docker or live Dawarich needed.
- End-to-end validation is manual: run the built image against one
  real, small Dawarich trip window, inspect the resulting MP4.
- No CI pipeline for v1 (decision 10).

## Out of scope (for this iteration)

- Automatic triggering (webhooks/polling) on trip creation.
- Calling Dawarich's `Trips` resource directly (blocked on it not
  being part of the versioned API).
- Upstreaming the date-range patch to google-timeline-visualizer.
- PyPI package.
- CI/CD auto-publish pipeline.
- Actually pushing the git repo to GitHub or the image to a registry —
  those are explicit, separate user-triggered actions outside this
  plan's scope.
