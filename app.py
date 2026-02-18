"""
Mist Cisco Config Converter - Flask Application

A web interface for converting Cisco configurations to Juniper Mist format.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename
import mistapi

from parser.cisco_parser import CiscoConfigParser

# Load environment variables
load_dotenv()

# Data directory for logs (mounted volume)
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
LOG_FILE = DATA_DIR / "app.log"


# Mist API Configuration
MIST_APITOKEN = os.environ.get("MIST_APITOKEN", "")
MIST_ORG_ID = os.environ.get("org_id", "")
MIST_HOST = os.environ.get("MIST_HOST", "api.mist.com")

# Global Mist API session (initialized lazily)
_mist_session = None


def get_mist_session():
    """Get or create Mist API session."""
    global _mist_session
    if _mist_session is None and MIST_APITOKEN:
        try:
            _mist_session = mistapi.APISession(
                host=MIST_HOST,
                apitoken=MIST_APITOKEN
            )
            logging.getLogger(__name__).info("Mist API session initialized")
        except Exception as error:
            logging.getLogger(__name__).error(f"Failed to initialize Mist API session: {error}")
            _mist_session = None
    return _mist_session


def check_mist_connection():
    """Check Mist API connectivity and return status info."""
    result = {
        "connected": False,
        "org_name": None,
        "org_id": MIST_ORG_ID,
        "api_configured": bool(MIST_APITOKEN and MIST_ORG_ID),
        "error": None
    }
    
    if not MIST_APITOKEN:
        result["error"] = "MIST_APITOKEN not configured"
        return result
    
    if not MIST_ORG_ID:
        result["error"] = "org_id not configured"
        return result
    
    session = get_mist_session()
    if not session:
        result["error"] = "Failed to create API session"
        return result
    
    try:
        # Get org info to verify connectivity
        response = mistapi.api.v1.orgs.orgs.getOrg(session, MIST_ORG_ID)
        if response.status_code == 200:
            org_data = response.data
            result["connected"] = True
            result["org_name"] = org_data.get("name", "Unknown")
            logging.getLogger(__name__).debug(f"Mist API connected: {result['org_name']}")
        else:
            result["error"] = f"API returned status {response.status_code}"
    except Exception as error:
        result["error"] = str(error)
        logging.getLogger(__name__).error(f"Mist API connection check failed: {error}")
    
    return result


def find_site_by_name(site_name: str) -> dict | None:
    """Search for an existing site by name in the Mist org.
    
    Returns:
        Site dict if found, None if not found or error.
    """
    session = get_mist_session()
    if not session:
        return None
    
    try:
        response = mistapi.api.v1.orgs.sites.listOrgSites(session, MIST_ORG_ID)
        if response.status_code == 200:
            sites = response.data
            for site in sites:
                if site.get("name", "").lower() == site_name.lower():
                    return site
        return None
    except Exception as error:
        logging.getLogger(__name__).error(f"Error searching for site: {error}")
        return None


def create_site(site_name: str, address: str = "") -> dict | None:
    """Create a new site in the Mist org.
    
    Args:
        site_name: Name for the new site (typically device hostname)
        address: Physical address/location (from SNMP location)
    
    Returns:
        Created site dict if successful, None on error.
    """
    session = get_mist_session()
    if not session:
        return None
    
    site_data = {
        "name": site_name
    }
    
    if address:
        site_data["address"] = address
    
    try:
        response = mistapi.api.v1.orgs.sites.createOrgSite(session, MIST_ORG_ID, site_data)
        if response.status_code in [200, 201]:
            logging.getLogger(__name__).info(f"Created site: {site_name}")
            return response.data
        else:
            logging.getLogger(__name__).error(f"Failed to create site: {response.status_code}")
            return None
    except Exception as error:
        logging.getLogger(__name__).error(f"Error creating site: {error}")
        return None

# Configure logging with file and console handlers
def setup_logging():
    """Configure logging with syslog-style levels to file and console."""
    log_level_str = os.environ.get("LOG_LEVEL", "INFO").upper()
    
    # Support both string (DEBUG, INFO) and numeric (10, 20) log levels
    if log_level_str.isdigit():
        log_level = int(log_level_str)
    else:
        log_level = getattr(logging, log_level_str, logging.INFO)
    
    # Create formatter with syslog-style format
    formatter = logging.Formatter(
        fmt="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Clear any existing handlers
    root_logger.handlers.clear()
    
    # Console handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # File handler with rotation (10MB max, keep 5 backups)
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    
    return logging.getLogger(__name__)


# Initialize logging
logger = setup_logging()
logger.info(f"Logging initialized. Log file: {LOG_FILE}")

# Valid themes
VALID_THEMES = ["tmobile", "verizon", "hackers", "matrix"]


def validate_input_filename(filename: str) -> str | None:
    """Validate filename from input directory - prevent path traversal.
    
    Returns the filename if valid, None if invalid.
    Unlike secure_filename, this preserves spaces and special chars.
    """
    if not filename:
        return None
    # Reject path traversal attempts
    if ".." in filename or "/" in filename or "\\" in filename:
        return None
    # Reject empty or whitespace-only
    if not filename.strip():
        return None
    return filename

# Allowed file extensions for config uploads
ALLOWED_EXTENSIONS = {".txt", ".conf", ".cfg", ".ios", ".config", ".log"}

# Initialize Flask app
app = Flask(__name__, static_folder="static")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", os.urandom(32).hex())
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB max upload

# Input directory for config files
INPUT_DIR = Path(__file__).parent / "input"
INPUT_DIR.mkdir(exist_ok=True)

# Output directory for converted configs
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


def allowed_file(filename):
    """Check if file extension is allowed."""
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def get_input_files():
    """Get list of config files in the input directory."""
    files = []
    for filepath in INPUT_DIR.iterdir():
        if filepath.is_file() and allowed_file(filepath.name):
            files.append({
                "name": filepath.name,
                "size": filepath.stat().st_size,
                "modified": filepath.stat().st_mtime
            })
    return sorted(files, key=lambda x: x["name"].lower())


def get_theme():
    """Get the current theme from environment, with validation."""
    theme = os.environ.get("THEME", "tmobile").lower().strip()
    if theme not in VALID_THEMES:
        logger.warning(f"Invalid theme '{theme}', defaulting to 'tmobile'")
        return "tmobile"
    return theme


@app.route("/")
def index():
    """Main page - config upload interface."""
    theme = get_theme()
    return render_template("index.html", theme=theme)


@app.route("/health")
def health():
    """Health check endpoint for container orchestration."""
    return jsonify({"status": "healthy", "service": "mist-cisco-converter"})


@app.route("/api/mist-status", methods=["GET"])
def mist_status():
    """Check Mist API connectivity status."""
    status = check_mist_connection()
    return jsonify(status)


@app.route("/api/files", methods=["GET"])
def list_files():
    """List available config files in the input directory."""
    files = get_input_files()
    return jsonify({"files": files})


@app.route("/api/upload", methods=["POST"])
def upload_file():
    """Upload a config file to the input directory."""
    if "config_file" not in request.files:
        return jsonify({"error": "No config file provided"}), 400
    
    config_file = request.files["config_file"]
    if not config_file.filename:
        return jsonify({"error": "No file selected"}), 400
    
    if not allowed_file(config_file.filename):
        return jsonify({"error": f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"}), 400
    
    # Secure the filename to prevent path traversal
    filename = secure_filename(config_file.filename)
    filepath = INPUT_DIR / filename
    
    # Save the file
    config_file.save(filepath)
    logger.info(f"Uploaded config file: {filename} ({filepath.stat().st_size} bytes)")
    
    return jsonify({
        "status": "uploaded",
        "filename": filename,
        "size": filepath.stat().st_size
    })


@app.route("/api/convert", methods=["POST"])
def convert_config():
    """API endpoint to preview conversion - returns proposed changes for confirmation.
    
    Accepts:
    - selected_file: filename from input directory
    - gateway_type: 'branch' or 'hub'
    
    Returns proposed changes including site lookup/creation plan.
    """
    config_content = None
    filename = None
    
    # Get gateway type
    gateway_type = request.form.get("gateway_type", "").lower()
    if gateway_type not in ["branch", "hub"]:
        return jsonify({"error": "Gateway type must be 'branch' or 'hub'"}), 400
    
    # Method 1: File selected from input directory
    if request.form.get("selected_file"):
        filename = validate_input_filename(request.form["selected_file"])
        if not filename:
            return jsonify({"error": "Invalid filename"}), 400
        
        filepath = INPUT_DIR / filename
        
        if not filepath.exists():
            return jsonify({"error": f"File not found: {filename}"}), 404
        
        if not filepath.is_file():
            return jsonify({"error": "Invalid file path"}), 400
        
        config_content = filepath.read_text(encoding="utf-8")
        logger.info(f"Processing config from input folder: {filename} ({len(config_content)} bytes)")
    
    # Method 2: Direct file upload
    elif "config_file" in request.files and request.files["config_file"].filename:
        config_file = request.files["config_file"]
        raw_filename = config_file.filename or ""
        filename = secure_filename(raw_filename)
        config_content = config_file.read().decode("utf-8")
        logger.info(f"Processing uploaded config: {filename} ({len(config_content)} bytes)")
    
    else:
        return jsonify({"error": "No config file provided. Select a file or upload one."}), 400
    
    # Parse the config
    parser = CiscoConfigParser(config_content)
    parsed_data = parser.parse()
    result = parser.to_dict()
    
    logger.info(f"Parsed config: {result['summary']['total_lines']} lines")
    
    # Extract key fields for site management
    device_name = result.get("system", {}).get("hostname", "")
    snmp_location = result.get("snmp", {}).get("location", "")
    
    if not device_name:
        return jsonify({"error": "Could not extract hostname from config"}), 400
    
    # Check if site exists
    existing_site = find_site_by_name(device_name)
    
    # Build proposed changes
    proposed = {
        "gateway_type": gateway_type,
        "device_name": device_name,
        "site": {
            "exists": existing_site is not None,
            "name": device_name,
            "address": snmp_location,
            "site_id": existing_site.get("id") if existing_site else None
        },
        "config_summary": {
            "ntp_servers": len(result.get("ntp", {}).get("servers", [])),
            "logging_hosts": len(result.get("logging", {}).get("hosts", [])),
            "interfaces": result.get("summary", {}).get("interface_count", 0),
            "static_routes": result.get("summary", {}).get("route_count", 0),
            "vlans": result.get("summary", {}).get("vlan_count", 0)
        },
        "parsed_data": result
    }
    
    return jsonify({
        "status": "preview",
        "filename": filename,
        "proposed": proposed
    })


@app.route("/api/apply", methods=["POST"])
def apply_config():
    """API endpoint to apply the conversion - creates/updates site in Mist.
    
    Accepts JSON body with:
    - device_name: hostname from config
    - gateway_type: 'branch' or 'hub'
    - site_name: name for the site
    - site_address: physical address
    - create_site: boolean - whether to create new site
    - site_id: existing site ID (if not creating)
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON data provided"}), 400
    
    device_name = data.get("device_name", "")
    gateway_type = data.get("gateway_type", "")
    site_name = data.get("site_name", device_name)
    site_address = data.get("site_address", "")
    create_site_flag = data.get("create_site", False)
    site_id = data.get("site_id")
    
    if not device_name:
        return jsonify({"error": "device_name is required"}), 400
    
    if gateway_type not in ["branch", "hub"]:
        return jsonify({"error": "gateway_type must be 'branch' or 'hub'"}), 400
    
    # Check Mist connectivity
    mist_status = check_mist_connection()
    if not mist_status["connected"]:
        return jsonify({"error": f"Mist API not connected: {mist_status.get('error', 'Unknown')}"}), 503
    
    result_site = None
    
    if create_site_flag:
        # Create new site
        result_site = create_site(site_name, site_address)
        if not result_site:
            return jsonify({"error": "Failed to create site"}), 500
        site_id = result_site.get("id")
        logger.info(f"Created new site: {site_name} (ID: {site_id})")
    else:
        # Use existing site
        if not site_id:
            return jsonify({"error": "site_id required when not creating new site"}), 400
        logger.info(f"Using existing site ID: {site_id}")
    
    # TODO: Apply site settings (logging, NTP, DNS, etc.)
    # This will be implemented in the next phase
    
    return jsonify({
        "status": "success",
        "message": f"Site '{site_name}' {'created' if create_site_flag else 'selected'}",
        "site_id": site_id,
        "site_name": site_name,
        "gateway_type": gateway_type
    })


@app.route("/api/display", methods=["POST"])
def display_config():
    """API endpoint to parse and display Cisco config sections.
    
    Parses the config into logical sections and extracts variables.
    
    Accepts either:
    - config_file: uploaded file
    - selected_file: filename from input directory
    """
    logger.debug("API /api/display called")
    config_content = None
    filename = None
    
    # Method 1: File selected from input directory
    if request.form.get("selected_file"):
        raw_filename = request.form["selected_file"]
        logger.debug(f"Selected file from input folder: {raw_filename}")
        
        filename = validate_input_filename(raw_filename)
        if not filename:
            logger.warning(f"Invalid filename rejected: {raw_filename}")
            return jsonify({"error": "Invalid filename"}), 400
        
        filepath = INPUT_DIR / filename
        logger.debug(f"Looking for file at: {filepath}")
        
        if not filepath.exists():
            logger.error(f"File not found: {filepath}")
            return jsonify({"error": f"File not found: {filename}"}), 404
        
        if not filepath.is_file():
            logger.error(f"Path is not a file: {filepath}")
            return jsonify({"error": "Invalid file path"}), 400
        
        config_content = filepath.read_text(encoding="utf-8")
        logger.info(f"Parsing config from input folder: {filename} ({len(config_content)} bytes)")
    
    # Method 2: Direct file upload
    elif "config_file" in request.files and request.files["config_file"].filename:
        config_file = request.files["config_file"]
        raw_filename = config_file.filename or ""
        logger.debug(f"Uploaded file: {raw_filename}")
        filename = secure_filename(raw_filename)
        config_content = config_file.read().decode("utf-8")
        logger.info(f"Parsing uploaded config: {filename} ({len(config_content)} bytes)")
    
    else:
        logger.warning("No config file provided in request")
        return jsonify({"error": "No config file provided. Select a file or upload one."}), 400
    
    # Parse the configuration
    logger.debug("Starting config parsing")
    try:
        parser = CiscoConfigParser(config_content)
        parsed_data = parser.parse()
        result = parser.to_dict()
        
        logger.info(
            f"Parsed {filename}: {result['summary']['total_lines']} lines, "
            f"{result['summary']['interface_count']} interfaces, "
            f"{result['summary']['vlan_count']} VLANs"
        )
        logger.debug(f"Parse summary: {result['summary']}")
        
        return jsonify({
            "status": "success",
            "filename": filename,
            "parsed_data": result
        })
    except Exception as error:
        logger.exception(f"Error parsing config {filename}: {error}")
        return jsonify({"error": f"Parse error: {str(error)}"}), 500


if __name__ == "__main__":
    # Development server only - use Gunicorn in production
    app.run(host="0.0.0.0", port=8000, debug=True)
