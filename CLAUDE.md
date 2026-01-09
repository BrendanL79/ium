# Docker Auto-Updater Context

## Project Overview
Python-based Docker image auto-updater that tracks version-specific tags matching regex patterns alongside base tags (e.g., "latest"). Originally created by Claude Opus 4.1 (bebfe84), enhanced with security fixes and web UI.

## Original Requirements (2025-09-28)
- Auto-update mechanism beyond simple "latest" tag pulling
- Track version-specific tags (e.g., v8.11.1-ls358) that match regex patterns
- Support different base tags per image (not just "latest")
- Architecture-agnostic solution
- Dry-run mode for testing

## Core Functionality
- Monitors Docker images for updates by comparing digests between base tag and regex-matched version tags
- Example: `linuxserver/calibre:latest` → `v8.11.1-ls358` via regex `^v[0-9]+\.[0-9]+\.[0-9]+-ls[0-9]+$`
- Supports any base tag (not just "latest"): PostgreSQL uses base_tag="14"
- Multi-registry support: Docker Hub, gcr.io, private registries
- Architecture-aware via manifest lists
- State tracking prevents duplicate updates
- Container recreation preserves ALL settings with rollback on failure

## Key Files
- `dum.py`: Main updater logic with DockerImageUpdater class
- `webui.py`: Flask web interface with real-time WebSocket updates (branch: webui)
- `config/config.json`: Image definitions with regex patterns
- `state/docker_update_state.json`: Tracks current versions/digests
- `docker-compose.yml`: Three modes - dry-run (default), prod, webui

## Configuration Schema
```json
{
  "images": [{
    "image": "linuxserver/calibre",
    "regex": "^v[0-9]+\\.[0-9]+\\.[0-9]+-ls[0-9]+$",
    "base_tag": "latest",
    "auto_update": false,
    "container_name": "calibre",
    "cleanup_old_images": true,
    "registry": "optional-custom-registry"
  }]
}
```

## Security Improvements (commit bb79953)
- Fixed command injection in container updates via subprocess lists
- Added JSON schema validation, file locking, request timeouts
- Proper exception handling with logging instead of print/sys.exit
- Cross-platform support (Unix fcntl, Windows msvcrt)

## Usage Modes
1. **CLI Dry-run**: `docker-compose up -d` (default, safe)
2. **CLI Production**: `docker-compose --profile prod up -d`
3. **Web UI**: `docker-compose --profile webui up -d` (port 5000)
4. **Portainer**: Deploy stack, edit YAML to remove profiles

## Web UI Features (branch: webui)
- Real-time status via Socket.IO
- Configuration editor with validation
- Manual checks, daemon control
- Update history tracking
- Responsive design

## Critical Implementation Details
- Docker API via HTTP not CLI where possible
- Manifest digest comparison for update detection
- Full container config preservation during updates
- Atomic state file writes with platform-specific locking
- Dry-run mode shows all operations without executing

## Common Patterns
- LinuxServer.io: `^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+-ls[0-9]+$`
- Semantic: `^v?[0-9]+\.[0-9]+\.[0-9]+$`
- PostgreSQL: `^[0-9]+\.[0-9]+$`

## Environment Variables
- `CONFIG_FILE=/config/config.json`
- `STATE_FILE=/state/docker_update_state.json`
- `DRY_RUN=true` (safety default)
- `LOG_LEVEL=INFO`
- `CHECK_INTERVAL=3600`

## NAS Deployment
See `nas-setup.md` for Synology/QNAP setup. Mount paths:
- `/var/run/docker.sock:/var/run/docker.sock:ro` (read-only in dry-run)
- `./config:/config`
- `./state:/state`

## Development Notes
- No external dependencies except requests, jsonschema, flask (webui)
- Designed for Python 3.11+ but compatible with 3.8+
- Docker socket required, no Docker Python SDK used
- State format uses dataclasses for validation
- Web UI uses vanilla JS, no build process

## Commit History
- bebfe84: Initial output from Claude Opus 4.1
- 63ddb3d: Make base tag configurable
- f01fb76: Update README.md with AI warning
- fe869c9: Set up CLAUDE.md
- 47f1a10: First pass at dry run mode
- bb79953: Fix critical security vulnerabilities and improve code quality
- (webui branch): Add web UI for Docker updater