# Image Update Manager (ium)

Docker image auto-updater: tracks version tags via regex, compares manifest digests, recreates containers with rollback.

## Key Files
- `ium.py`: Core — `DockerImageUpdater`, registry API, container management
- `webui.py`: Flask-SocketIO web interface (gunicorn/gevent)
- `static/js/app.js`: Frontend — Socket.IO, config editor, regex validation
- `docker-compose.yml`: Services `ium` (Web UI, default) and `ium-cli` (CLI, `cli` profile)
- `config/config.json`: Image definitions (gitignored) | `state/image_update_state.json`: Runtime state (gitignored)

## Architecture
- Direct HTTP to registries via `requests` — no Docker SDK
- Auto-discovery via `docker ps -a --format json`, matches by image name with registry/tag normalization
- `ImageState` dataclass → JSON with atomic writes (temp+rename), cross-platform file locking
- Registry auth: Docker Hub, ghcr.io, gcr.io, private registries
- Manifest digest comparison via HEAD requests, parallel tag fetching (ThreadPoolExecutor)
- Web UI: vanilla JS + Socket.IO CDN, no build process

## Code Conventions
- Type hints throughout, dataclasses for structured data, logging not print()
- `or []` for API responses that may return `null` (registry tags, Docker arrays: CapAdd, Devices, Env, Mounts)
- Regex patterns compiled once at config load in `compiled_patterns` dict
- `socketio.emit()` from background threads must include `namespace='/'`
- State updated on any new version detection (regardless of `auto_update`) to prevent re-reporting; disk persistence skipped in dry-run
- Config booleans `auto_update`/`cleanup_old_images` always saved explicitly, never omitted
- `__version__` in `ium.py`, semver, CI publishes Docker images on `v*` tags

## Dependencies
- **Core**: `requests`, `jsonschema` | **Web UI**: adds `flask`, `flask-socketio`, `gunicorn`, `gevent`, `gevent-websocket`
- Python 3.8+, tested on 3.11+
