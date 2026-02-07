# Image Update Manager (ium)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Docker image auto-updater that tracks version-specific tags via regex patterns, compares manifest digests to detect updates, and recreates containers preserving all settings with rollback on failure.

## Quick Start

```bash
git clone https://github.com/BrendanL79/ium.git && cd ium
mkdir -p config state
echo '{"images": []}' > config/config.json
docker-compose up -d --build     # Web UI at http://localhost:5050
```

Use the Web UI to add images via **Add from Preset** (19 built-in) or **+ Add Image** (auto-detects patterns from registry).

## How It Works

1. Queries registry APIs directly (no images pulled for checking)
2. Compares manifest digest of your `base_tag` (e.g., `latest`, `stable`, `lts`) against saved state
3. Finds the version-specific tag matching your regex with the same digest
4. If `auto_update: true`: pulls image, recreates containers, rolls back on failure

## Configuration

```json
{
  "images": [{
    "image": "linuxserver/sonarr",
    "regex": "^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+-ls[0-9]+$",
    "base_tag": "latest",
    "auto_update": false,
    "cleanup_old_images": true,
    "keep_versions": 3,
    "registry": "ghcr.io"
  }]
}
```

Only `image` and `regex` are required. Containers are auto-detected — no need to specify container names.

### Common Patterns

| Pattern | Regex | Examples |
|---------|-------|---------|
| Semver | `^[0-9]+\.[0-9]+\.[0-9]+$` | portainer-ce, jellyfin, n8n |
| Semver (v-prefix) | `^v[0-9]+\.[0-9]+\.[0-9]+$` | homarr, mealie |
| LinuxServer | `^v[0-9]+\.[0-9]+\.[0-9]+-ls[0-9]+$` | bazarr, calibre, tautulli |
| LinuxServer 4-part | `^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+-ls[0-9]+$` | sonarr, radarr, prowlarr |

## Deployment

| Mode | Command |
|------|---------|
| Web UI (default) | `docker-compose up -d` |
| CLI daemon | `docker-compose --profile cli up -d ium-cli` |
| Both | `docker-compose --profile cli up -d` |
| Standalone | `python ium.py config/config.json --dry-run` |

All modes default to `auto_update: false` per image.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CONFIG_FILE` | `/config/config.json` | Config path |
| `STATE_FILE` | `/state/image_update_state.json` | State path |
| `DRY_RUN` | `false` | Dry-run mode |
| `DAEMON` / `CHECK_INTERVAL` | `true` / `3600` | CLI daemon settings |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `WEBUI_USER` / `WEBUI_PASSWORD` | (disabled) | Optional basic auth |

## Security

- Docker socket access required (same as Portainer, Watchtower, Diun)
- Set `WEBUI_USER`/`WEBUI_PASSWORD` to enable basic auth; unset = unrestricted (suitable for trusted LANs)
- Always test with `DRY_RUN=true` first

## Testing

```bash
pip install -r requirements.txt && pytest tests/
```

## NAS Deployment

See [nas-setup.md](nas-setup.md).
