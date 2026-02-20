# Mist Cisco Config Converter

A containerized web application for converting Cisco IOS configurations to Juniper Mist format.

## Requirements

- Python 3.13+
- Container runtime: Podman or Docker
- mistapi 0.59.x+

## Quick Start

### Using Podman (Recommended)

```bash
# Build the container (--format docker enables HEALTHCHECK)
podman build --format docker -t mist-cisco-converter .

# Run with compose
podman-compose up -d

# Or run directly
podman run -d -p 8000:8000 --env-file .env -v ./data:/app/data:Z mist-cisco-converter
```

### Using Docker

```bash
# Build the container
docker build -t mist-cisco-converter -f Containerfile .

# Run with compose
docker compose up -d

# Or run directly
docker run -d -p 8000:8000 --env-file .env -v ./data:/app/data mist-cisco-converter
```

### Access the Web Interface

Open your browser to: <http://localhost:8000>

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
MIST_APITOKEN=your_api_token_here
MIST_HOST=api.mist.com
SECRET_KEY=your_secret_key_here
THEME=tmobile
```

## Themes

Four dark themes are available, configurable via the `THEME` environment variable:

| Theme | Description |
| ----- | ----------- |
| `tmobile` | Magenta/pink corporate dark (default) |
| `verizon` | Red professional dark |
| `hackers` | 90s neon cyberpunk (green/cyan/magenta) |
| `matrix` | Green digital rain on black |

Set in `.env`:

```bash
THEME=matrix
```

## Project Structure

```text
MistCiscoConfigConverter/
├── app.py                 # Flask application
├── Containerfile          # Container build file (Podman/Docker)
├── compose.yml            # Container orchestration
├── gunicorn.conf.py       # Production WSGI config
├── requirements.txt       # Python dependencies
├── static/
│   └── css/
│       └── themes.css     # Theme stylesheets (4 themes)
├── templates/
│   └── index.html         # Web interface
├── parser/                # Cisco config parsing module
├── documentation/         # API specs and mapping references
├── input/                 # Cisco config files to convert (mounted volume)
├── output/                # Converted Mist configs (mounted volume)
├── data/                  # Application data (mounted volume)
├── .env                   # Environment config (git-ignored)
└── README.md
```

## Documentation

- [Cisco to Mist Configuration Mapping](documentation/CISCO_TO_MIST_MAPPING.md) - Terminology conversions and configuration mappings
- [Mist OpenAPI 3.0 Spec](documentation/mist-api-openapi3yaml.yaml) - Full API reference
- [Mist OpenAPI 3.1 Spec](documentation/mist-api-openapi31yaml.yaml) - Latest API reference

## File Input Methods

Two ways to provide Cisco config files for conversion:

### Method 1: Pre-populate INPUT folder

Place config files directly in the `input/` folder. They will appear in the dropdown on the web interface.

```bash
# Copy config files to input folder
cp switch-config.txt input/
cp router-config.cfg input/
```

Supported extensions: `.txt`, `.conf`, `.cfg`, `.ios`, `.config`

### Method 2: Upload via web interface

Use the drag-and-drop upload area to add files directly from the browser. Uploaded files are saved to the `input/` folder.

## API Endpoints

| Endpoint       | Method | Description                                              |
| -------------- | ------ | -------------------------------------------------------- |
| `/`            | GET    | Web interface                                            |
| `/health`      | GET    | Health check for orchestration                           |
| `/api/files`   | GET    | List available config files in input folder              |
| `/api/upload`  | POST   | Upload config file to input folder                       |
| `/api/convert` | POST   | Convert config file (supports file selection and upload) |

## Development

### Local Development (without container)

```bash
# Create virtual environment
python -m venv .venv

# Activate (choose one)
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Windows Command Prompt:
.venv\Scripts\activate.bat
# Linux/macOS/Git Bash:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment config
copy .env.example .env  # Windows
cp .env.example .env    # Linux/macOS

# Edit .env with your API credentials and theme preference

# Run Flask development server (with auto-reload)
flask run --debug

# Or run directly
python app.py
```

**Note**: The development server runs on `http://127.0.0.1:5000` by default. Use the containerized version for production testing on port 8000.

### Production Deployment

The container uses Gunicorn with gevent workers for production. Configuration is in `gunicorn.conf.py`.

Environment variables for tuning:

| Variable             | Default       | Description                |
| -------------------- | ------------- | -------------------------- |
| `GUNICORN_WORKERS`   | CPU * 2 + 1   | Number of worker processes |
| `GUNICORN_BIND`      | 0.0.0.0:8000  | Bind address               |
| `GUNICORN_LOG_LEVEL` | info          | Logging level              |

## Power User Mode

Enable administrative features by setting `POWERUSER=true` in `.env`:

```bash
POWERUSER=true
```

This enables bulk backup, restore, and delete operations for:

| Object           | Backup | Restore | Delete All |
| ---------------- | ------ | ------- | ---------- |
| Service Policies | Yes    | Yes     | -          |
| Services         | Yes    | Yes     | Yes        |
| Networks         | Yes    | Yes     | Yes        |

Backup files are stored in `output/` with timestamped filenames.

**WARNING**: Destructive operations require double confirmation (confirm dialog + type CONFIRM).

## License

CC BY-NC 4.0 - See [LICENSE](LICENSE) for details.

## Changelog

### v26.02.19

- Power user backup/restore/delete functionality for:
  - Service Policies (backup, restore)
  - Services (backup, restore, delete all)
  - Networks (backup, restore, delete all)
- Backup files stored in `output/` folder with timestamps
- Restore matches by name: updates existing, creates new
- All destructive operations require CONFIRM confirmation
- UI groups power user buttons with separators

### v26.02.18

- Initial project structure
- Flask web interface with file upload
- Containerfile for Podman/Docker
- Gunicorn production configuration with gevent workers
- Health check endpoint
- Added `--format docker` flag for Podman HEALTHCHECK support
- External CSS stylesheet with 4 dark themes:
  - T-Mobile (magenta)
  - Verizon (red)
  - Hackers (90s cyberpunk)
  - The Matrix (green digital rain)
- Theme configurable via `THEME` environment variable
- Responsive mobile CSS for iPhone SE through iPad Pro 12.9"
- Touch device optimizations and safe-area inset support
- Enhanced venv development instructions
- Dual input methods: file picker from INPUT folder or drag-and-drop upload
- API endpoints for file listing (`/api/files`) and upload (`/api/upload`)
