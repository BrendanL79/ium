# Image Update Manager Web UI

Web-based interface for the Image Update Manager with real-time updates via Socket.IO.

## Features

- **Real-time Status**: See which containers have updates available
- **Manual Checks**: Trigger update checks with one click
- **Apply Updates**: Apply individual pending updates from the UI
- **Daemon Control**: Start/stop automatic checking with configurable interval
- **Configuration Editor**: Edit image config, add from 19 built-in presets, auto-detect regex patterns
- **Notifications**: ntfy.sh and webhook support with test buttons
- **Update History**: Track all applied and pending updates with timestamps
- **Live Activity Log**: Real-time log streaming via WebSocket
- **Authentication**: Auto-generated credentials on first run, or set via environment variables
- **Dark Mode**: Three-mode theme cycling (system/light/dark)

## Quick Start

```bash
# Using Docker Compose (default service)
docker-compose up -d

# Access at http://your-server-ip:5050
# Credentials are auto-generated — check container logs on first run
```

## API Endpoints

- `GET /api/status` - System status (updater loaded, dry-run mode, daemon state)
- `GET /api/config` - Current configuration
- `POST /api/config` - Update configuration (validates schema + regex ReDoS)
- `GET /api/state` - Current image states
- `POST /api/check` - Trigger manual update check
- `GET /api/updates` - Last check results
- `POST /api/apply-update` - Apply a specific pending update
- `GET /api/history` - Update history (paginated, max 500 entries)
- `POST /api/daemon` - Start/stop daemon mode (min interval: 60s)
- `POST /api/detect-patterns` - Auto-detect regex patterns from registry tags
- `POST /api/notifications/test` - Test ntfy/webhook notifications
- `GET /health` - Unauthenticated liveness probe

## WebSocket Events

Real-time updates via Socket.IO:

- `status_update` - Daemon state, checking status, last check time
- `check_progress` - Real-time progress during checks
- `check_complete` - Results with full update list
- `check_error` - Error with traceback
- `connected` - Server handshake

## Environment Variables

- `CONFIG_FILE` - Path to config.json (default: `/config/config.json`)
- `STATE_FILE` - Path to state file (default: `/state/image_update_state.json`)
- `DRY_RUN` - Enable dry-run mode (default: `true`)
- `LOG_LEVEL` - Logging level (default: `INFO`)
- `WEBUI_USER` - Override username (default: auto-generated, stored in `/state/.auth.json`)
- `WEBUI_PASSWORD` - Override password (default: auto-generated, stored in `/state/.auth.json`)

## Security

- **Authentication**: Basic auth with constant-time credential comparison; auto-generated on first run with secure random passwords, stored in `/state/.auth.json` (mode 0600)
- **CSRF Protection**: State-changing endpoints require `X-Requested-With: XMLHttpRequest` header
- **Docker Socket**: Required for container management — binding `/var/run/docker.sock` grants equivalent-to-root access; use dry-run mode for read-only testing
- **Dry-Run Default**: Enabled by default; set `DRY_RUN=false` explicitly to allow updates

## Development

```bash
pip install -r requirements.txt -r requirements-webui.txt

export CONFIG_FILE=config/config.json
export STATE_FILE=state/image_update_state.json

python webui.py
```

## Customization

The web UI uses vanilla HTML/CSS/JavaScript with no build process:

- `templates/index.html` - HTML structure
- `static/css/style.css` - Styling (CSS variables for theming)
- `static/js/app.js` - Frontend logic + Socket.IO client
