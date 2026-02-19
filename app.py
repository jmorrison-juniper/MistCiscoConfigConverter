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

from parser.cisco_parser import CiscoConfigParser
from parser.address_parser import parse_snmp_location, ParsedAddress
from mist import MistConnection, MistSiteManager, MistTemplateManager, MistProfileManager, MistAuditManager
from mist.profile_manager import sanitize_hub_profile_name
from mist.template_manager import BRANCH_GATEWAY_TEMPLATE_NAME

# Load environment variables
load_dotenv()

# Data directory for logs (mounted volume)
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
LOG_FILE = DATA_DIR / "app.log"


def get_mist_connection() -> MistConnection:
    """Get fresh MistConnection instance.
    
    Creates a new connection each time to avoid stale TCP connections
    with gevent workers.
    """
    return MistConnection()


def get_site_manager() -> MistSiteManager:
    """Get MistSiteManager instance."""
    return MistSiteManager(get_mist_connection())


def get_template_manager() -> MistTemplateManager:
    """Get MistTemplateManager instance."""
    return MistTemplateManager(get_mist_connection())


def get_profile_manager() -> MistProfileManager:
    """Get MistProfileManager instance."""
    return MistProfileManager(get_mist_connection())


def get_audit_manager() -> MistAuditManager:
    """Get MistAuditManager instance."""
    return MistAuditManager(get_mist_connection())


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
    status = get_mist_connection().check_connection()
    return jsonify(status)


@app.route("/api/site-gateways/<site_id>", methods=["GET"])
def get_site_gateways(site_id: str):
    """Get gateway devices assigned to a specific site.
    
    Used to populate device selection UI when configuring hub gateways.
    
    Returns:
        JSON with list of gateway devices at the site.
    """
    gateways = get_site_manager().get_site_gateways(site_id)
    
    # Format response with relevant fields for UI
    gateway_list = []
    for gw in gateways:
        gateway_list.append({
            "mac": gw.get("mac", ""),
            "name": gw.get("name", "Unnamed"),
            "model": gw.get("model", "Unknown"),
            "serial": gw.get("serial", ""),
            "deviceprofile_id": gw.get("deviceprofile_id"),
            "ha_state": gw.get("ha", {}).get("state") if gw.get("ha") else None
        })
    
    return jsonify({
        "site_id": site_id,
        "gateway_count": len(gateway_list),
        "gateways": gateway_list
    })


@app.route("/api/unassigned-gateways", methods=["GET"])
def get_unassigned_gateways():
    """Get unassigned gateway devices from org inventory.
    
    Used when a site has no gateways and user needs to select from inventory.
    
    Returns:
        JSON with list of unassigned gateway devices in org.
    """
    gateways = get_site_manager().get_unassigned_gateways()
    
    # Format response with relevant fields for UI
    gateway_list = []
    for gw in gateways:
        gateway_list.append({
            "mac": gw.get("mac", ""),
            "name": gw.get("name", "Unnamed"),
            "model": gw.get("model", "Unknown"),
            "serial": gw.get("serial", "")
        })
    
    return jsonify({
        "gateway_count": len(gateway_list),
        "gateways": gateway_list
    })


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
    - gateway_type: 'branch', 'hub', or 'standalone'
    
    Returns proposed changes including site lookup/creation plan.
    """
    config_content = None
    filename = None
    
    # Get gateway type
    gateway_type = request.form.get("gateway_type", "").lower()
    if gateway_type not in ["branch", "hub", "standalone"]:
        return jsonify({"error": "Gateway type must be 'branch', 'hub', or 'standalone'"}), 400
    
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
    snmp_contact = result.get("snmp", {}).get("contact", "")
    
    if not device_name:
        return jsonify({"error": "Could not extract hostname from config"}), 400
    
    # Parse SNMP location into address components
    parsed_address = parse_snmp_location(snmp_location)
    parsed_address.contact = snmp_contact
    
    # Check if site exists
    existing_site = get_site_manager().find_by_name(device_name)
    
    # Extract NTP/DNS/syslog from parsed config
    cisco_ntp_servers = result.get("ntp", {}).get("servers", [])
    cisco_syslog_hosts = result.get("logging", {}).get("hosts", [])
    cisco_dns_servers = result.get("dns", {}).get("servers", [])
    
    # Check for existing template/profile and compare config
    template_comparison = None
    template_info = None
    
    if gateway_type == "branch":
        template = get_template_manager().find_by_name(
            get_template_manager().branch_template_name
        )
        if template:
            template_info = {
                "name": template.get("name"),
                "id": template.get("id"),
                "exists": True
            }
            template_comparison = get_template_manager().compare_with_cisco_config(
                template, cisco_ntp_servers, cisco_dns_servers, cisco_syslog_hosts
            )
        else:
            template_info = {
                "name": get_template_manager().branch_template_name,
                "exists": False
            }
    elif gateway_type == "standalone":
        template = get_template_manager().find_by_name(
            get_template_manager().standalone_template_name
        )
        if template:
            template_info = {
                "name": template.get("name"),
                "id": template.get("id"),
                "exists": True
            }
            template_comparison = get_template_manager().compare_with_cisco_config(
                template, cisco_ntp_servers, cisco_dns_servers, cisco_syslog_hosts
            )
        else:
            template_info = {
                "name": get_template_manager().standalone_template_name,
                "exists": False
            }
    elif gateway_type == "hub":
        from mist.profile_manager import sanitize_hub_profile_name
        hub_profile_name = sanitize_hub_profile_name(device_name)
        profile = get_profile_manager().find_by_name(hub_profile_name)
        if profile:
            template_info = {
                "name": profile.get("name"),
                "id": profile.get("id"),
                "exists": True
            }
            template_comparison = get_profile_manager().compare_with_cisco_config(
                profile, cisco_ntp_servers, cisco_dns_servers, cisco_syslog_hosts
            )
        else:
            template_info = {
                "name": hub_profile_name,
                "exists": False
            }
    
    # Build proposed changes
    proposed = {
        "gateway_type": gateway_type,
        "device_name": device_name,
        "site": {
            "exists": existing_site is not None,
            "name": device_name,
            "address": {
                "raw": snmp_location,
                "street": parsed_address.street,
                "city": parsed_address.city,
                "state": parsed_address.state_code,
                "zip_code": parsed_address.zip_code,
                "country_code": parsed_address.country_code,
                "timezone": parsed_address.timezone,
                "room_info": parsed_address.room_info,
                "contact": parsed_address.contact
            },
            "site_id": existing_site.get("id") if existing_site else None
        },
        "config_summary": {
            "ntp_servers": len(result.get("ntp", {}).get("servers", [])),
            "logging_hosts": len(result.get("logging", {}).get("hosts", [])),
            "interfaces": result.get("summary", {}).get("interface_count", 0),
            "static_routes": result.get("summary", {}).get("route_count", 0),
            "vlans": result.get("summary", {}).get("vlan_count", 0)
        },
        "template_info": template_info,
        "template_comparison": template_comparison,
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
    - gateway_type: 'branch', 'hub', or 'standalone'
    - site_name: name for the site
    - address: object with street, city, state, zip_code, country_code, timezone
    - create_site: boolean - whether to create new site
    - site_id: existing site ID (if not creating)
    - selected_devices: list of device MACs for hub mode (optional)
    - configure_ha: boolean - whether to create HA cluster (requires 2 selected devices)
    - assign_to_site: boolean - whether to assign unassigned devices to site first
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON data provided"}), 400
    
    device_name = data.get("device_name", "")
    gateway_type = data.get("gateway_type", "")
    site_name = data.get("site_name", device_name)
    address_data = data.get("address", {})
    create_site_flag = data.get("create_site", False)
    site_id = data.get("site_id")
    selected_devices = data.get("selected_devices", [])  # User-selected device MACs
    configure_ha = data.get("configure_ha", False)  # Create HA cluster
    assign_to_site = data.get("assign_to_site", False)  # Assign devices from unassigned inventory
    override_ntp = data.get("override_ntp", False)  # Override template NTP at device level
    override_dns = data.get("override_dns", False)  # Override template DNS at device level
    override_syslog = data.get("override_syslog", False)  # Override template syslog at device level
    
    if not device_name:
        return jsonify({"error": "device_name is required"}), 400
    
    if gateway_type not in ["branch", "hub", "standalone"]:
        return jsonify({"error": "gateway_type must be 'branch', 'hub', or 'standalone'"}), 400
    
    # Validate HA configuration
    if configure_ha and len(selected_devices) != 2:
        return jsonify({"error": "HA cluster requires exactly 2 selected devices"}), 400
    
    # Check Mist connectivity
    mist_conn_status = get_mist_connection().check_connection()
    if not mist_conn_status["connected"]:
        return jsonify({"error": f"Mist API not connected: {mist_conn_status.get('error', 'Unknown')}"}), 503
    
    # Determine template/profile IDs based on gateway type
    gatewaytemplate_id = None
    hub_profile_id = None
    hub_profile_name = None
    hub_profile_created = False
    clear_gatewaytemplate = False
    
    # Extract NTP/DNS/syslog from parsed_data for template creation
    parsed_data = data.get("parsed_data", {})
    raw_ntp_servers = parsed_data.get("ntp", {}).get("servers", [])
    raw_syslog_servers = parsed_data.get("logging", {}).get("hosts", [])
    
    # Normalize NTP servers - may be strings or dicts with "server" key
    ntp_servers: list[str] = []
    for server in raw_ntp_servers:
        if isinstance(server, dict):
            ntp_servers.append(server.get("server", ""))
        else:
            ntp_servers.append(str(server))
    ntp_servers = [s for s in ntp_servers if s]  # Remove empty strings
    
    # Normalize syslog servers - may be strings or dicts
    syslog_servers: list[str] = []
    for host in raw_syslog_servers:
        if isinstance(host, dict):
            syslog_servers.append(host.get("host", ""))
        else:
            syslog_servers.append(str(host))
    syslog_servers = [s for s in syslog_servers if s]  # Remove empty strings
    
    dns_servers: list[str] = parsed_data.get("dns", {}).get("servers", [])
    
    if gateway_type == "branch":
        # Branch gateways use gateway template (type=spoke) attached to site
        branch_template, branch_template_created = get_template_manager().get_or_create_branch_template(
            ntp_servers=ntp_servers,
            dns_servers=dns_servers,
            syslog_servers=syslog_servers
        )
        if branch_template:
            gatewaytemplate_id = branch_template.get("id")
            logger.info(f"Using branch gateway template: {branch_template.get('name')} (ID: {gatewaytemplate_id}) {'(newly created)' if branch_template_created else '(existing)'}")
            
            # If existing template has empty values but Cisco config has values, update template
            if not branch_template_created:
                template_ntp = branch_template.get("ntp_servers") or []
                template_dns = branch_template.get("dns_servers") or []
                template_syslog_cfg = branch_template.get("remote_syslog") or {}
                template_syslog = template_syslog_cfg.get("servers") or []
                
                update_ntp = ntp_servers if not template_ntp and ntp_servers else None
                update_dns = dns_servers if not template_dns and dns_servers else None
                update_syslog = syslog_servers if not template_syslog and syslog_servers else None
                
                if update_ntp or update_dns or update_syslog:
                    update_types = []
                    if update_ntp:
                        update_types.append("NTP")
                    if update_dns:
                        update_types.append("DNS")
                    if update_syslog:
                        update_types.append("Syslog")
                    logger.info(f"Updating branch template with Cisco {', '.join(update_types)} values")
                    if gatewaytemplate_id:
                        get_template_manager().update(
                            gatewaytemplate_id,
                            ntp_servers=update_ntp,
                            dns_servers=update_dns,
                            syslog_servers=update_syslog
                        )
        else:
            logger.warning("Could not get/create branch gateway template, continuing without it")
    
    elif gateway_type == "hub":
        # Hub gateways use device profile (type=gateway) - device-level, not site-level
        # Also clear any existing branch gateway template from the site
        clear_gatewaytemplate = True
        hub_profile, hub_profile_created = get_profile_manager().get_or_create_hub_profile(
            device_name,
            ntp_servers=ntp_servers,
            dns_servers=dns_servers,
            syslog_servers=syslog_servers
        )
        if hub_profile:
            hub_profile_id = hub_profile.get("id")
            hub_profile_name = sanitize_hub_profile_name(device_name)
            logger.info(f"Using hub device profile: {hub_profile_name} (ID: {hub_profile_id}) {'(newly created)' if hub_profile_created else '(existing)'}")
            logger.info("Hub mode: will also clear any existing branch gateway template")
            
            # If existing profile has empty values but Cisco config has values, update profile
            if not hub_profile_created:
                profile_ntp = hub_profile.get("ntp_servers") or []
                profile_dns = hub_profile.get("dns_servers") or []
                profile_syslog_cfg = hub_profile.get("remote_syslog") or {}
                profile_syslog = profile_syslog_cfg.get("servers") or []
                
                update_ntp = ntp_servers if not profile_ntp and ntp_servers else None
                update_dns = dns_servers if not profile_dns and dns_servers else None
                update_syslog = syslog_servers if not profile_syslog and syslog_servers else None
                
                if update_ntp or update_dns or update_syslog:
                    update_types = []
                    if update_ntp:
                        update_types.append("NTP")
                    if update_dns:
                        update_types.append("DNS")
                    if update_syslog:
                        update_types.append("Syslog")
                    logger.info(f"Updating hub profile with Cisco {', '.join(update_types)} values")
                    if hub_profile_id:
                        get_profile_manager().update(
                            hub_profile_id,
                            ntp_servers=update_ntp,
                            dns_servers=update_dns,
                            syslog_servers=update_syslog
                        )
        else:
            hub_profile_created = False
            logger.warning("Could not get/create hub device profile, continuing without it")
    
    elif gateway_type == "standalone":
        # Standalone gateways use gateway template (type=standalone) attached to site
        standalone_template, standalone_template_created = get_template_manager().get_or_create_standalone_template(
            ntp_servers=ntp_servers,
            dns_servers=dns_servers,
            syslog_servers=syslog_servers
        )
        if standalone_template:
            gatewaytemplate_id = standalone_template.get("id")
            logger.info(f"Using standalone gateway template: {standalone_template.get('name')} (ID: {gatewaytemplate_id}) {'(newly created)' if standalone_template_created else '(existing)'}")
            
            # If existing template has empty values but Cisco config has values, update template
            if not standalone_template_created:
                template_ntp = standalone_template.get("ntp_servers") or []
                template_dns = standalone_template.get("dns_servers") or []
                template_syslog_cfg = standalone_template.get("remote_syslog") or {}
                template_syslog = template_syslog_cfg.get("servers") or []
                
                update_ntp = ntp_servers if not template_ntp and ntp_servers else None
                update_dns = dns_servers if not template_dns and dns_servers else None
                update_syslog = syslog_servers if not template_syslog and syslog_servers else None
                
                if update_ntp or update_dns or update_syslog:
                    update_types = []
                    if update_ntp:
                        update_types.append("NTP")
                    if update_dns:
                        update_types.append("DNS")
                    if update_syslog:
                        update_types.append("Syslog")
                    logger.info(f"Updating standalone template with Cisco {', '.join(update_types)} values")
                    if gatewaytemplate_id:
                        get_template_manager().update(
                            gatewaytemplate_id,
                            ntp_servers=update_ntp,
                            dns_servers=update_dns,
                            syslog_servers=update_syslog
                        )
        else:
            logger.warning("Could not get/create standalone gateway template, continuing without it")
    
    result_site = None
    
    if create_site_flag:
        # Build ParsedAddress from submitted data
        parsed_address = ParsedAddress(
            raw=address_data.get("raw", ""),
            street=address_data.get("street", ""),
            city=address_data.get("city", ""),
            state=address_data.get("state", ""),
            state_code=address_data.get("state", ""),
            zip_code=address_data.get("zip_code", ""),
            country_code=address_data.get("country_code", ""),
            timezone=address_data.get("timezone", ""),
            room_info=address_data.get("room_info", ""),
            contact=address_data.get("contact", "")
        )
        
        # Create new site with full address data and gateway template (for branch)
        result_site = get_site_manager().create(
            site_name,
            parsed_address,
            gatewaytemplate_id=gatewaytemplate_id
        )
        if not result_site:
            return jsonify({"error": "Failed to create site"}), 500
        site_id = result_site.get("id")
        logger.info(f"Created new site: {site_name} (ID: {site_id})")
    else:
        # Use existing site - but update with address data and template if available
        if not site_id:
            return jsonify({"error": "site_id required when not creating new site"}), 400
        logger.info(f"Using existing site ID: {site_id}")
        
        # Update existing site with address data and template
        parsed_address = ParsedAddress(
            raw=address_data.get("raw", ""),
            street=address_data.get("street", ""),
            city=address_data.get("city", ""),
            state=address_data.get("state", ""),
            state_code=address_data.get("state", ""),
            zip_code=address_data.get("zip_code", ""),
            country_code=address_data.get("country_code", ""),
            timezone=address_data.get("timezone", ""),
            room_info=address_data.get("room_info", ""),
            contact=address_data.get("contact", "")
        )
        update_result = get_site_manager().update(
            site_id,
            parsed_address,
            gatewaytemplate_id=gatewaytemplate_id,
            clear_gatewaytemplate=clear_gatewaytemplate
        )
        if update_result:
            logger.info(f"Updated existing site with address data and template")
        else:
            logger.warning(f"Could not update site")
    
    # For standalone and branch modes: unassign devices from any existing hub profile
    # This ensures clean slate when switching away from hub mode
    devices_unassigned_from_hub = False
    if gateway_type in ("standalone", "branch") and selected_devices:
        # Check if a hub profile exists for this device name
        hub_profile_name_check = sanitize_hub_profile_name(device_name)
        existing_hub_profile = get_profile_manager().find_by_name(hub_profile_name_check)
        if existing_hub_profile:
            hub_profile_id_to_unassign = existing_hub_profile.get("id")
            if hub_profile_id_to_unassign:
                logger.info(f"Unassigning {len(selected_devices)} device(s) from hub profile {hub_profile_name_check}")
                unassign_result = get_profile_manager().unassign_devices(hub_profile_id_to_unassign, selected_devices)
                if unassign_result:
                    unassigned_count = len(unassign_result.get("success", []))
                    if unassigned_count > 0:
                        devices_unassigned_from_hub = True
                        logger.info(f"Successfully unassigned {unassigned_count} device(s) from hub profile")
                    else:
                        logger.debug("Devices were not assigned to hub profile (no changes needed)")
                else:
                    logger.warning("Failed to unassign devices from hub profile")
    
    # For hub mode: assign selected gateway devices to the hub profile
    # Or create HA cluster if requested
    assigned_devices = []
    ha_cluster_created = False
    devices_assigned_to_site = False
    
    if gateway_type == "hub" and hub_profile_id and site_id:
        # Get device MACs to work with
        mac_addresses: list[str] = []
        
        if selected_devices:
            # Use user-selected devices
            mac_addresses = [mac for mac in selected_devices if mac]
            logger.info(f"User selected {len(mac_addresses)} device(s) for hub assignment")
            
            # If devices need to be assigned to site first (from unassigned inventory)
            if assign_to_site and mac_addresses:
                logger.info(f"Assigning {len(mac_addresses)} device(s) from unassigned inventory to site {site_id}")
                assign_result = get_site_manager().assign_devices_to_site(site_id, mac_addresses)
                if assign_result:
                    success_count = len(assign_result.get("success", []))
                    error_list = assign_result.get("error", [])
                    if success_count > 0:
                        devices_assigned_to_site = True
                        logger.info(f"Successfully assigned {success_count} device(s) to site")
                        
                        # Name devices from inventory assignment
                        is_ha_pair = configure_ha and len(mac_addresses) == 2
                        name_result = get_site_manager().name_devices_from_inventory(
                            site_id, mac_addresses, device_name, is_ha_pair
                        )
                        if name_result.get("success"):
                            logger.info(f"Named {len(name_result['success'])} device(s)")
                        if name_result.get("error"):
                            logger.warning(f"Failed to name devices: {name_result['error']}")
                    if error_list:
                        logger.warning(f"Failed to assign devices: {error_list}")
                        failed_reasons = assign_result.get("reason", [])
                        if failed_reasons:
                            logger.warning(f"Failure reasons: {failed_reasons}")
                else:
                    logger.error("Failed to assign devices to site")
                    return jsonify({"error": "Failed to assign devices to site from inventory"}), 500
        else:
            # Fall back to all gateways at site (shouldn't happen with new UI)
            site_gateways = get_site_manager().get_site_gateways(site_id)
            mac_addresses = [
                str(gw.get("mac")) for gw in site_gateways 
                if gw.get("mac") is not None
            ]
            logger.info(f"No devices selected, using all {len(mac_addresses)} gateway(s) at site")
        
        if mac_addresses:
            if configure_ha and len(mac_addresses) == 2:
                # Create HA cluster
                logger.info(f"Creating HA cluster with devices: {mac_addresses}")
                ha_result = get_profile_manager().create_ha_cluster(
                    site_id, mac_addresses, managed=True
                )
                if ha_result and ha_result.get("success"):
                    ha_cluster_created = True
                    assigned_devices = ha_result.get("nodes", [])
                    logger.info(f"HA cluster created successfully")
                else:
                    logger.warning("Failed to create HA cluster")
            else:
                # Assign devices to hub profile (non-HA)
                logger.info(f"Assigning {len(mac_addresses)} gateway(s) to hub profile {hub_profile_name}")
                assign_result = get_profile_manager().assign_devices(hub_profile_id, mac_addresses)
                if assign_result is not None:
                    # API call succeeded - check what was assigned
                    newly_assigned = assign_result.get("success", [])
                    if newly_assigned:
                        assigned_devices = newly_assigned
                        logger.info(f"Newly assigned devices: {assigned_devices}")
                    else:
                        # Empty success list = devices already on this profile
                        assigned_devices = mac_addresses  # Use selected devices for reporting
                        logger.info(f"Device(s) already using hub profile: {mac_addresses}")
                else:
                    logger.warning("Failed to assign gateway devices to hub profile")
        else:
            logger.info("No gateway devices to assign to hub profile")
    
    # For branch mode: assign devices from unassigned inventory if requested
    # and configure HA cluster if 2 devices selected
    if gateway_type == "branch" and site_id:
        mac_addresses: list[str] = []
        
        if selected_devices:
            mac_addresses = [mac for mac in selected_devices if mac]
            logger.info(f"User selected {len(mac_addresses)} device(s) for branch site")
            
            # Assign devices to site from unassigned inventory
            if assign_to_site and mac_addresses:
                logger.info(f"Assigning {len(mac_addresses)} device(s) from unassigned inventory to branch site {site_id}")
                assign_result = get_site_manager().assign_devices_to_site(site_id, mac_addresses)
                if assign_result:
                    success_count = len(assign_result.get("success", []))
                    error_list = assign_result.get("error", [])
                    if success_count > 0:
                        devices_assigned_to_site = True
                        assigned_devices = assign_result.get("success", [])
                        logger.info(f"Successfully assigned {success_count} device(s) to branch site")
                        
                        # Name devices from inventory assignment
                        is_ha_pair = configure_ha and len(mac_addresses) == 2
                        name_result = get_site_manager().name_devices_from_inventory(
                            site_id, mac_addresses, device_name, is_ha_pair
                        )
                        if name_result.get("success"):
                            logger.info(f"Named {len(name_result['success'])} device(s)")
                        if name_result.get("error"):
                            logger.warning(f"Failed to name devices: {name_result['error']}")
                    if error_list:
                        logger.warning(f"Failed to assign devices: {error_list}")
                        failed_reasons = assign_result.get("reason", [])
                        if failed_reasons:
                            logger.warning(f"Failure reasons: {failed_reasons}")
                else:
                    logger.error("Failed to assign devices to branch site")
                    return jsonify({"error": "Failed to assign devices to branch site from inventory"}), 500
            
            # Create HA cluster if requested and 2 devices selected
            if configure_ha and len(mac_addresses) == 2:
                logger.info(f"Creating HA cluster for branch with devices: {mac_addresses}")
                ha_result = get_profile_manager().create_ha_cluster(
                    site_id, mac_addresses, managed=True
                )
                if ha_result and ha_result.get("success"):
                    ha_cluster_created = True
                    assigned_devices = ha_result.get("nodes", [])
                    logger.info(f"Branch HA cluster created successfully")
                else:
                    logger.warning("Failed to create HA cluster for branch")
    
    # For standalone mode: assign devices from unassigned inventory if requested
    # and configure HA cluster if 2 devices selected
    if gateway_type == "standalone" and site_id:
        mac_addresses: list[str] = []
        
        if selected_devices:
            mac_addresses = [mac for mac in selected_devices if mac]
            logger.info(f"User selected {len(mac_addresses)} device(s) for standalone site")
            
            # Assign devices to site from unassigned inventory
            if assign_to_site and mac_addresses:
                logger.info(f"Assigning {len(mac_addresses)} device(s) from unassigned inventory to standalone site {site_id}")
                assign_result = get_site_manager().assign_devices_to_site(site_id, mac_addresses)
                if assign_result:
                    success_count = len(assign_result.get("success", []))
                    error_list = assign_result.get("error", [])
                    if success_count > 0:
                        devices_assigned_to_site = True
                        assigned_devices = assign_result.get("success", [])
                        logger.info(f"Successfully assigned {success_count} device(s) to standalone site")
                        
                        # Name devices from inventory assignment
                        is_ha_pair = configure_ha and len(mac_addresses) == 2
                        name_result = get_site_manager().name_devices_from_inventory(
                            site_id, mac_addresses, device_name, is_ha_pair
                        )
                        if name_result.get("success"):
                            logger.info(f"Named {len(name_result['success'])} device(s)")
                        if name_result.get("error"):
                            logger.warning(f"Failed to name devices: {name_result['error']}")
                    if error_list:
                        logger.warning(f"Failed to assign devices: {error_list}")
                        failed_reasons = assign_result.get("reason", [])
                        if failed_reasons:
                            logger.warning(f"Failure reasons: {failed_reasons}")
                else:
                    logger.error("Failed to assign devices to standalone site")
                    return jsonify({"error": "Failed to assign devices to standalone site from inventory"}), 500
            
            # Create HA cluster if requested and 2 devices selected
            if configure_ha and len(mac_addresses) == 2:
                logger.info(f"Creating HA cluster for standalone with devices: {mac_addresses}")
                ha_result = get_profile_manager().create_ha_cluster(
                    site_id, mac_addresses, managed=True
                )
                if ha_result and ha_result.get("success"):
                    ha_cluster_created = True
                    assigned_devices = ha_result.get("nodes", [])
                    logger.info(f"Standalone HA cluster created successfully")
                else:
                    logger.warning("Failed to create HA cluster for standalone")
    
    # Apply device-level config overrides if requested
    # This overrides template/profile settings at the device level
    config_overrides_applied = False
    if site_id and selected_devices and (override_ntp or override_dns or override_syslog):
        override_ntp_servers = ntp_servers if override_ntp else None
        override_dns_servers = dns_servers if override_dns else None
        override_syslog_servers = syslog_servers if override_syslog else None
        
        override_types = []
        if override_ntp:
            override_types.append("NTP")
        if override_dns:
            override_types.append("DNS")
        if override_syslog:
            override_types.append("Syslog")
        
        logger.info(
            f"Applying device-level config overrides ({', '.join(override_types)}) "
            f"to {len(selected_devices)} device(s)"
        )
        
        override_result = get_site_manager().apply_config_overrides_to_devices(
            site_id,
            selected_devices,
            ntp_servers=override_ntp_servers,
            dns_servers=override_dns_servers,
            syslog_servers=override_syslog_servers
        )
        
        if override_result.get("success"):
            config_overrides_applied = True
            logger.info(
                f"Config overrides applied to {len(override_result['success'])} device(s)"
            )
        if override_result.get("error"):
            logger.warning(
                f"Failed to apply overrides to devices: {override_result['error']}"
            )
    
    # Verify changes in audit log (only if we have a site_id)
    verification_data = None
    if site_id:
        expected_actions = []
        if create_site_flag:
            expected_actions.append("Add Site")  # Mist uses "Add Site" not "Create Site"
        else:
            expected_actions.append("Update Site")
        
        # For hub mode, also expect device profile creation (only if newly created)
        if gateway_type == "hub" and hub_profile_id and hub_profile_created:
            expected_actions.append("Add Device Profile")
        
        # Don't filter by site_id since device profile entries may not have site_id
        verification = get_audit_manager().verify_changes(
            site_id=None,  # Query all org logs, filter client-side
            site_name=site_name,
            expected_actions=expected_actions,
            wait_seconds=2,
            max_retries=3
        )
        verification_data = {
            "verified": verification.verified,
            "expected_actions": verification.expected_actions,
            "found_actions": verification.found_actions,
            "missing_actions": verification.missing_actions,
            "message": verification.message
        }
    
    # Build response with template info
    response_data = {
        "status": "success",
        "message": f"Site '{site_name}' {'created' if create_site_flag else 'updated'}",
        "site_id": site_id,
        "site_name": site_name,
        "gateway_type": gateway_type
    }
    
    # Add verification if available
    if verification_data:
        response_data["verification"] = verification_data
    
    if gatewaytemplate_id:
        response_data["gatewaytemplate_id"] = gatewaytemplate_id
        response_data["gatewaytemplate_name"] = BRANCH_GATEWAY_TEMPLATE_NAME
        
        # For branch mode: report device assignment and HA cluster
        if devices_assigned_to_site:
            response_data["devices_assigned_to_site"] = True
            response_data["message"] += f" ({len(assigned_devices)} device(s) assigned to site from inventory)"
        
        if ha_cluster_created:
            response_data["ha_cluster"] = True
            response_data["assigned_devices"] = assigned_devices
            response_data["message"] += f" (HA cluster created with {len(assigned_devices)} devices)"
    
    if hub_profile_id:
        response_data["hub_profile_id"] = hub_profile_id
        response_data["hub_profile_name"] = hub_profile_name
        
        # Report device assignment to site (from unassigned inventory)
        if devices_assigned_to_site:
            response_data["devices_assigned_to_site"] = True
            response_data["message"] += f" ({len(selected_devices)} device(s) assigned to site from inventory)"
        
        if ha_cluster_created:
            response_data["ha_cluster"] = True
            response_data["assigned_devices"] = assigned_devices
            response_data["message"] += f" (HA cluster created with {len(assigned_devices)} devices)"
        elif assigned_devices:
            response_data["assigned_devices"] = assigned_devices
            response_data["message"] += f" ({len(assigned_devices)} device(s) using hub profile)"
        elif not selected_devices:
            response_data["message"] += " (hub profile ready - no devices selected)"
    if clear_gatewaytemplate:
        response_data["template_cleared"] = True
        response_data["message"] += " (gateway template membership removed)"
        
        # Report hub profile unassignment if it happened
        if devices_unassigned_from_hub:
            response_data["hub_profile_unassigned"] = True
            response_data["message"] += " (devices removed from hub profile)"
        
        # For standalone mode: report device assignment and HA cluster
        if gateway_type == "standalone":
            if devices_assigned_to_site:
                response_data["devices_assigned_to_site"] = True
                response_data["message"] += f" ({len(assigned_devices)} device(s) assigned to site from inventory)"
            
            if ha_cluster_created:
                response_data["ha_cluster"] = True
                response_data["assigned_devices"] = assigned_devices
                response_data["message"] += f" (HA cluster created with {len(assigned_devices)} devices)"
    
    # Report config overrides if applied
    if config_overrides_applied:
        override_types = []
        if override_ntp:
            override_types.append("NTP")
        if override_dns:
            override_types.append("DNS")
        if override_syslog:
            override_types.append("Syslog")
        response_data["config_overrides"] = override_types
        response_data["message"] += f" ({', '.join(override_types)} overrides applied)"
    
    return jsonify(response_data)


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
