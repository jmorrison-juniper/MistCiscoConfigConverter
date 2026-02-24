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

from parser.cisco_parser import CiscoConfigParser, classify_interface, subnet_mask_to_cidr
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


def is_poweruser():
    """Check if poweruser mode is enabled via environment variable."""
    return os.environ.get("POWERUSER", "").lower().strip() == "true"


def get_interface_classification_thresholds() -> tuple[float, float]:
    """Get WAN and LAN classification thresholds from environment.
    
    Returns:
        Tuple of (wan_threshold, lan_threshold)
        - wan_threshold: Score above this = WAN (default 0.3)
        - lan_threshold: Score below this = LAN (default -0.3)
    """
    wan_threshold = float(os.environ.get("INTERFACE_WAN_THRESHOLD", "0.3"))
    lan_threshold = float(os.environ.get("INTERFACE_LAN_THRESHOLD", "-0.3"))
    return wan_threshold, lan_threshold


def extract_wan_interfaces(parsed_data: dict, min_confidence: float = 0.7) -> list[dict]:
    """Extract detected WAN interfaces from parsed config with cellular expansion.
    
    Filters interfaces classified as WAN with confidence above threshold.
    Cellular interfaces generate TWO reservations each:
      1. A modem GigE slot (for external 3rd-party cellular modem)
      2. An LTE slot (for built-in Juniper LTE interface)
    
    Ordering:
      1. GigE WAN interfaces (regular WAN)
      2. (Future: GigE LAN interfaces)
      3. Cellular modem GigE slots
      4. Cellular LTE slots (with _lte suffix)
    
    Args:
        parsed_data: Parsed config dictionary with interfaces list
        min_confidence: Minimum classification_confidence to include (default 0.7)
    
    Returns:
        List of WAN interface dicts sorted by category, containing:
        - name: Original Cisco interface name
        - description: Interface description if set
        - wan_score: Classification score
        - classification_confidence: Confidence level
        - classification_indicators: List of factors contributing to classification
        - ip_address: IP address if configured
        - subnet_mask: Subnet mask for static IPs
        - ip_config_type: static, dhcp, pppoe, or negotiated
        - encap_vlan_id: VLAN ID for subinterface encapsulation
        - default_gateway: Next-hop gateway for this interface
        - shutdown: True if interface is administratively down
        - port_type: Interface port type (routed, cellular, etc.)
        - upload_kbps: Upload bandwidth for traffic shaping (from config, tunnel, or description)
        - download_kbps: Download bandwidth for traffic shaping
        - bandwidth_source: Where bandwidth was derived from (config, tunnel:X, tunnel_desc:X, description)
        - wan_var_name: Variable name for site vars (e.g., "wan1", "wan2_lte")
        - wan_type: "broadband" or "lte"
        - is_cellular: True if this is a cellular-derived interface
        - cellular_slot: For cellular, indicates "modem_gig" or "lte"
        - cellular_profile_id: ID of cellular profile attached to this interface
        - lte_apn: APN name from cellular profile (if applicable)
        - lte_auth: Authentication type (none, chap, pap)
        - lte_username: APN username
        - lte_password: APN password (decoded)
    """
    interfaces = parsed_data.get("interfaces", [])
    static_routes = parsed_data.get("static_routes", [])
    cellular_profiles = parsed_data.get("cellular_profiles", [])
    
    # Build lookup of profile_id -> profile data
    profile_lookup: dict[int, dict] = {}
    for profile in cellular_profiles:
        profile_id = profile.get("profile_id", 0)
        if profile_id > 0:
            profile_lookup[profile_id] = profile
    
    # Build a lookup of interface -> default gateway from static routes
    # Default route (0.0.0.0/0) with interface hint maps gateway to that interface
    interface_gateways: dict[str, str] = {}
    for route in static_routes:
        if route.get("destination") == "0.0.0.0":
            next_hop = route.get("next_hop", "")
            route_interface = route.get("interface", "")
            if route_interface and next_hop:
                interface_gateways[route_interface] = next_hop
            elif next_hop:
                # Default route without explicit interface - try to match by subnet
                # Store for later matching
                interface_gateways["_default"] = next_hop
    
    # Separate GigE WAN from Cellular
    gige_wan = []
    cellular_wan = []
    
    for interface in interfaces:
        if interface.get("interface_role") == "wan":
            confidence = interface.get("classification_confidence", 0)
            if confidence >= min_confidence:
                name = interface.get("name", "")
                port_type = interface.get("port_type", "")
                
                # Determine default gateway for this interface
                gateway = interface_gateways.get(name, "")
                if not gateway and "_default" in interface_gateways:
                    gateway = interface_gateways["_default"]
                
                # Get cellular profile data if applicable
                cellular_profile_id = interface.get("cellular_profile_id", 0)
                lte_apn = ""
                lte_auth = "none"
                lte_username = ""
                lte_password = ""
                
                if cellular_profile_id > 0 and cellular_profile_id in profile_lookup:
                    profile = profile_lookup[cellular_profile_id]
                    lte_apn = profile.get("apn", "")
                    lte_auth = profile.get("authentication", "none") or "none"
                    lte_username = profile.get("username", "")
                    lte_password = profile.get("password", "")
                
                # Get effective bandwidth (prefer explicit, fall back to derived)
                # Use upload speed for traffic shaping (max_tx_kbps)
                upload_kbps = interface.get("upload_kbps", 0)
                download_kbps = interface.get("download_kbps", 0)
                derived_upload_kbps = interface.get("derived_upload_kbps", 0)
                derived_download_kbps = interface.get("derived_download_kbps", 0)
                
                # Effective speeds: prefer explicit, fall back to derived
                effective_upload = upload_kbps if upload_kbps > 0 else derived_upload_kbps
                effective_download = download_kbps if download_kbps > 0 else derived_download_kbps
                bandwidth_source = interface.get("bandwidth_source", "")
                
                entry = {
                    "name": name,
                    "description": interface.get("description", ""),
                    "wan_score": interface.get("wan_score", 0),
                    "classification_confidence": confidence,
                    "classification_indicators": interface.get("classification_indicators", []),
                    "ip_address": interface.get("ip_address", ""),
                    "subnet_mask": interface.get("subnet_mask", ""),
                    "ip_config_type": interface.get("ip_config_type", "") or "static",
                    "encap_vlan_id": interface.get("encap_vlan_id", 0),
                    "default_gateway": gateway,
                    "shutdown": interface.get("shutdown", False),
                    "port_type": port_type,
                    # Bandwidth for traffic shaping (effective = explicit or derived)
                    "upload_kbps": effective_upload,
                    "download_kbps": effective_download,
                    "bandwidth_source": bandwidth_source,
                    # Cellular profile data
                    "cellular_profile_id": cellular_profile_id,
                    "lte_apn": lte_apn,
                    "lte_auth": lte_auth,
                    "lte_username": lte_username,
                    "lte_password": lte_password
                }
                
                if port_type == "cellular" or name.lower().startswith("cellular"):
                    cellular_wan.append(entry)
                else:
                    gige_wan.append(entry)
    
    # Sort GigE WAN by wan_score descending
    gige_wan.sort(key=lambda x: x.get("wan_score", 0), reverse=True)
    
    # Sort Cellular by name (to keep 0/2/0 before 0/2/1)
    cellular_wan.sort(key=lambda x: x.get("name", ""))
    
    # Build final ordered list with WAN variable assignments
    # Order: GigE WAN -> (future LAN) -> Cellular modem GigE -> Cellular LTE
    result = []
    wan_index = 1
    
    # 1. GigE WAN interfaces
    for interface in gige_wan:
        interface["wan_var_name"] = f"wan{wan_index}"
        interface["wan_type"] = "broadband"
        interface["is_cellular"] = False
        interface["cellular_slot"] = None
        result.append(interface)
        wan_index += 1
    
    # (Future: LAN interfaces would go here)
    
    # 3. Cellular modem GigE slots (external 3rd-party modem)
    for interface in cellular_wan:
        modem_entry = interface.copy()
        modem_entry["wan_var_name"] = f"wan{wan_index}"
        modem_entry["wan_type"] = "broadband"  # External modem uses GigE
        modem_entry["is_cellular"] = True
        modem_entry["cellular_slot"] = "modem_gig"
        modem_entry["description"] = f"{interface.get('description', '')} (External Modem GigE)".strip()
        result.append(modem_entry)
        wan_index += 1
    
    # 4. Cellular LTE slots (built-in Juniper LTE)
    for interface in cellular_wan:
        lte_entry = interface.copy()
        lte_entry["wan_var_name"] = f"wan{wan_index}_lte"
        lte_entry["wan_type"] = "lte"
        lte_entry["is_cellular"] = True
        lte_entry["cellular_slot"] = "lte"
        lte_entry["description"] = f"{interface.get('description', '')} (Built-in LTE)".strip()
        result.append(lte_entry)
        wan_index += 1
    
    return result


def check_template_wan_variables(template: dict) -> dict:
    """Check if gateway template uses WAN interface variables.
    
    Examines port_config keys for {{wan1}}, {{wan2}}, etc. variable references.
    
    Args:
        template: Gateway template dictionary from Mist API
    
    Returns:
        Dict with:
        - uses_wan_vars: bool - True if template has {{wanN}} variables
        - wan_var_names: list - Variable names found (e.g., ["wan1", "wan2"])
        - port_config_keys: list - All port_config keys in template
    """
    port_config = template.get("port_config", {})
    port_keys = list(port_config.keys())
    
    # Find {{wanN}} patterns in port config keys
    import re
    wan_pattern = re.compile(r"\{\{(wan\d+(?:_lte)?)\}\}")
    wan_var_names = []
    
    for key in port_keys:
        matches = wan_pattern.findall(key)
        wan_var_names.extend(matches)
    
    # Also check inside port config values for WAN variable references
    for port_key, port_value in port_config.items():
        if isinstance(port_value, dict):
            # Check description, ip_config fields, etc.
            for field_key, field_value in port_value.items():
                if isinstance(field_value, str):
                    matches = wan_pattern.findall(field_value)
                    wan_var_names.extend(matches)
    
    # Remove duplicates and sort
    wan_var_names = sorted(set(wan_var_names))
    
    return {
        "uses_wan_vars": len(wan_var_names) > 0,
        "wan_var_names": wan_var_names,
        "port_config_keys": port_keys
    }



def extract_static_routes(parsed_data: dict) -> list[dict]:
    """Extract static routes from parsed config with variable naming.
    
    Converts destination/mask to CIDR format and assigns variable names
    for use as site variables and template references.
    
    Routes are grouped by VRF:
    - Default VRF routes -> extra_routes
    - Named VRF routes -> vrf_instances.<name>.extra_routes
    
    Args:
        parsed_data: Parsed config dictionary with static_routes list
    
    Returns:
        List of route dicts with variable names, sorted by VRF then destination.
    """
    static_routes = parsed_data.get("static_routes", [])
    if not static_routes:
        return []
    
    result = []
    route_index = 1
    
    for route in static_routes:
        destination = route.get("destination", "")
        mask = route.get("mask", "")
        next_hop = route.get("next_hop", "")
        
        if not destination or not next_hop:
            continue
        
        # Convert mask to CIDR prefix
        cidr_prefix = subnet_mask_to_cidr(mask) if mask else 0
        cidr = f"{destination}/{cidr_prefix}"
        
        entry = {
            "destination": destination,
            "mask": mask,
            "cidr": cidr,
            "next_hop": next_hop,
            "interface": route.get("interface", ""),
            "vrf": route.get("vrf", ""),
            "name": route.get("name", ""),
            "distance": route.get("distance", 1),
            "route_var_name": f"route{route_index}"
        }
        result.append(entry)
        route_index += 1
    
    # Sort by VRF (default first) then by destination
    result.sort(key=lambda route: (route["vrf"], route["cidr"]))
    
    # Reassign variable names after sorting
    for index, route in enumerate(result, 1):
        route["route_var_name"] = f"route{index}"
    
    return result


def extract_lan_interfaces(parsed_data: dict, min_confidence: float = 0.5) -> list[dict]:
    """Extract detected LAN interfaces from parsed config for network/VLAN creation.
    
    Filters interfaces classified as LAN with confidence above threshold.
    Cross-references with VLAN definitions for names. Includes HSRP VIP
    and DHCP helper addresses for each interface.
    
    Args:
        parsed_data: Parsed config dictionary with classified interfaces
        min_confidence: Minimum classification_confidence to include (default 0.5)
    
    Returns:
        List of LAN interface dicts with variable names, containing:
        - name: Original Cisco interface name (e.g., "Vlan200")
        - description: Interface description
        - ip_address: Interface IP address
        - subnet_mask: Subnet mask
        - cidr_prefix: CIDR prefix length (e.g., "24")
        - vlan_id: VLAN ID (from interface name, encap, or switchport)
        - vlan_name: VLAN name from VLAN definitions
        - vrf: VRF if any
        - helper_addresses: DHCP relay server list
        - shutdown: Admin state
        - hsrp_vip: HSRP virtual IP (first group VIP if present)
        - hsrp_priority: HSRP priority
        - hsrp_groups: All HSRP groups
        - lan_var_name: Variable name (e.g., "lan1")
    """
    interfaces = parsed_data.get("interfaces", [])
    vlans = parsed_data.get("vlans", [])
    
    # Build VLAN name lookup
    vlan_lookup = {}
    for vlan in vlans:
        vlan_id = vlan.get("vlan_id", 0)
        if vlan_id > 0:
            vlan_lookup[vlan_id] = vlan.get("name", "")
    
    result = []
    
    for interface in interfaces:
        role = interface.get("interface_role", "")
        if role != "lan":
            continue
        
        confidence = interface.get("classification_confidence", 0)
        if confidence < min_confidence:
            continue
        
        name = interface.get("name", "")
        ip_address = interface.get("ip_address", "")
        
        # Skip interfaces without IP addresses (pure L2)
        if not ip_address:
            continue
        
        # Skip shutdown interfaces
        if interface.get("shutdown", False):
            continue
        
        # Determine VLAN ID from multiple sources
        vlan_id = 0
        name_lower = name.lower()
        if name_lower.startswith("vlan"):
            # Extract VLAN ID from interface name (e.g., "Vlan200" -> 200)
            try:
                vlan_id = int(name[4:])
            except ValueError:
                pass
        if vlan_id == 0:
            vlan_id = interface.get("encap_vlan_id", 0)
        if vlan_id == 0:
            vlan_id = interface.get("switchport_access_vlan", 0)
        
        # Get VLAN name from definitions
        vlan_name = vlan_lookup.get(vlan_id, "")
        if not vlan_name:
            vlan_name = interface.get("description", "")
        
        # Get HSRP info (use first group with a VIP)
        hsrp_groups = interface.get("hsrp_groups", [])
        hsrp_vip = ""
        hsrp_priority = 100
        for group in hsrp_groups:
            if group.get("vip"):
                hsrp_vip = group["vip"]
                hsrp_priority = group.get("priority", 100)
                break
        
        # Convert subnet mask to CIDR
        subnet_mask = interface.get("subnet_mask", "")
        cidr_prefix = str(subnet_mask_to_cidr(subnet_mask)) if subnet_mask else ""
        
        entry = {
            "name": name,
            "description": interface.get("description", ""),
            "ip_address": ip_address,
            "subnet_mask": subnet_mask,
            "cidr_prefix": cidr_prefix,
            "vlan_id": vlan_id,
            "vlan_name": vlan_name,
            "vrf": interface.get("vrf", ""),
            "helper_addresses": interface.get("helper_addresses", []),
            "shutdown": interface.get("shutdown", False),
            "hsrp_vip": hsrp_vip,
            "hsrp_priority": hsrp_priority,
            "hsrp_groups": hsrp_groups,
            "lan_var_name": ""  # Assigned below
        }
        result.append(entry)
    
    # Sort by VLAN ID for consistent ordering
    result.sort(key=lambda interface: interface.get("vlan_id", 0))
    
    # Assign variable names
    for index, interface in enumerate(result, 1):
        interface["lan_var_name"] = f"lan{index}"
    
    return result


def extract_dhcp_pools(parsed_data: dict) -> list[dict]:
    """Extract DHCP pools from parsed config with usable IP range computation.
    
    Parses excluded-address ranges to compute the first usable IP start
    and last usable IP end for each pool. Assigns variable names for
    site variables and template references.
    
    Args:
        parsed_data: Parsed config dictionary with dhcp_pools list
    
    Returns:
        List of DHCP pool dicts with computed IP ranges and variable names.
    """
    dhcp_pools = parsed_data.get("dhcp_pools", [])
    if not dhcp_pools:
        return []
    
    result = []
    pool_index = 1
    
    for pool in dhcp_pools:
        network = pool.get("network", "")
        subnet_mask = pool.get("subnet_mask", "")
        gateway = pool.get("default_router", "")
        
        if not network:
            continue
        
        # Convert subnet mask to CIDR prefix
        cidr_prefix = str(subnet_mask_to_cidr(subnet_mask)) if subnet_mask else ""
        
        # Parse excluded address ranges
        raw_excluded = pool.get("excluded_addresses", [])
        excluded_ranges = []
        for exclusion in raw_excluded:
            # Format: "IP1 - IP2" or single "IP1"
            if " - " in exclusion:
                parts = exclusion.split(" - ", 1)
                excluded_ranges.append({"start": parts[0].strip(), "end": parts[1].strip()})
            else:
                excluded_ranges.append({"start": exclusion.strip(), "end": exclusion.strip()})
        
        # Compute usable IP range (first IP after exclusions, last IP before exclusions)
        ip_start, ip_end = _compute_usable_range(network, subnet_mask, excluded_ranges)
        
        entry = {
            "name": pool.get("name", ""),
            "vrf": pool.get("vrf", ""),
            "network": network,
            "subnet_mask": subnet_mask,
            "cidr_prefix": cidr_prefix,
            "gateway": gateway,
            "dns_servers": pool.get("dns_servers", []),
            "excluded_ranges": excluded_ranges,
            "ip_start": ip_start,
            "ip_end": ip_end,
            "dhcp_var_name": f"dhcp{pool_index}"
        }
        result.append(entry)
        pool_index += 1
    
    return result


def _compute_usable_range(
    network: str,
    subnet_mask: str,
    excluded_ranges: list[dict]
) -> tuple[str, str]:
    """Compute the first and last usable IP addresses after exclusions.
    
    Args:
        network: Network address (e.g., "192.168.100.0")
        subnet_mask: Subnet mask (e.g., "255.255.255.0")
        excluded_ranges: List of {"start": ip, "end": ip} dicts
    
    Returns:
        Tuple of (ip_start, ip_end) strings. Empty strings if computation fails.
    """
    if not network or not subnet_mask:
        return ("", "")
    
    try:
        # Convert network to integer
        net_parts = [int(octet) for octet in network.split(".")]
        mask_parts = [int(octet) for octet in subnet_mask.split(".")]
        
        net_int = (net_parts[0] << 24) | (net_parts[1] << 16) | (net_parts[2] << 8) | net_parts[3]
        mask_int = (mask_parts[0] << 24) | (mask_parts[1] << 16) | (mask_parts[2] << 8) | mask_parts[3]
        
        # Network range: first host to last host
        first_host = net_int + 1
        broadcast = net_int | (~mask_int & 0xFFFFFFFF)
        last_host = broadcast - 1
        
        if first_host > last_host:
            return ("", "")
        
        # Convert excluded ranges to integer ranges
        excluded_ints = []
        for exclusion in excluded_ranges:
            start_parts = [int(octet) for octet in exclusion["start"].split(".")]
            end_parts = [int(octet) for octet in exclusion["end"].split(".")]
            start_int = (start_parts[0] << 24) | (start_parts[1] << 16) | (start_parts[2] << 8) | start_parts[3]
            end_int = (end_parts[0] << 24) | (end_parts[1] << 16) | (end_parts[2] << 8) | end_parts[3]
            excluded_ints.append((start_int, end_int))
        
        # Sort exclusions by start address
        excluded_ints.sort(key=lambda exclusion: exclusion[0])
        
        # Find first usable IP (skip past exclusions from the start)
        ip_start = first_host
        for exclusion_start, exclusion_end in excluded_ints:
            if ip_start >= exclusion_start and ip_start <= exclusion_end:
                ip_start = exclusion_end + 1
            elif exclusion_start > ip_start:
                break
        
        # Find last usable IP (skip past exclusions from the end)
        ip_end = last_host
        for exclusion_start, exclusion_end in reversed(excluded_ints):
            if ip_end >= exclusion_start and ip_end <= exclusion_end:
                ip_end = exclusion_start - 1
            elif exclusion_end < ip_end:
                break
        
        if ip_start > ip_end:
            return ("", "")
        
        # Convert back to dotted notation
        def int_to_ip(value):
            return f"{(value >> 24) & 0xFF}.{(value >> 16) & 0xFF}.{(value >> 8) & 0xFF}.{value & 0xFF}"
        
        return (int_to_ip(ip_start), int_to_ip(ip_end))
        
    except (ValueError, IndexError):
        return ("", "")


def extract_vrf_instances(parsed_data: dict) -> list[dict]:
    """Extract VRF instances with cross-referenced interfaces, routes, and DHCP pools.
    
    Cross-references VRF definitions with:
    - Interfaces bound to each VRF (vrf forwarding)
    - Static routes in each VRF (ip route vrf)
    - DHCP pools in each VRF (vrf in pool)
    
    Args:
        parsed_data: Parsed config dictionary with vrfs, interfaces, 
                     static_routes, and dhcp_pools
    
    Returns:
        List of VRF instance dicts with associated resources and variable names.
    """
    vrfs = parsed_data.get("vrfs", [])
    if not vrfs:
        return []
    
    interfaces = parsed_data.get("interfaces", [])
    static_routes = parsed_data.get("static_routes", [])
    dhcp_pools = parsed_data.get("dhcp_pools", [])
    
    result = []
    vrf_index = 1
    
    for vrf in vrfs:
        vrf_name = vrf.get("name", "")
        if not vrf_name:
            continue
        
        # Find interfaces bound to this VRF
        vrf_interfaces = [
            interface.get("name", "")
            for interface in interfaces
            if interface.get("vrf", "") == vrf_name
        ]
        
        # Find static routes in this VRF
        vrf_routes = [
            {
                "destination": route.get("destination", ""),
                "mask": route.get("mask", ""),
                "cidr": f"{route.get('destination', '')}/{subnet_mask_to_cidr(route.get('mask', ''))}",
                "next_hop": route.get("next_hop", ""),
                "interface": route.get("interface", ""),
                "name": route.get("name", "")
            }
            for route in static_routes
            if route.get("vrf", "") == vrf_name
        ]
        
        # Find DHCP pools in this VRF
        vrf_dhcp_pools = [
            {
                "name": pool.get("name", ""),
                "network": pool.get("network", ""),
                "subnet_mask": pool.get("subnet_mask", ""),
                "default_router": pool.get("default_router", "")
            }
            for pool in dhcp_pools
            if pool.get("vrf", "") == vrf_name
        ]
        
        entry = {
            "name": vrf_name,
            "route_distinguisher": vrf.get("route_distinguisher", ""),
            "address_families": vrf.get("address_families", []),
            "description": vrf.get("description", ""),
            "interfaces": vrf_interfaces,
            "static_routes": vrf_routes,
            "dhcp_pools": vrf_dhcp_pools,
            "vrf_var_name": f"vrf{vrf_index}"
        }
        result.append(entry)
        vrf_index += 1
    
    return result


def validate_wan_configuration(
    template: dict | None,
    existing_site: dict | None,
    wan_interfaces: list[dict]
) -> dict:
    """Validate WAN configuration between template, site, and parsed config.
    
    Compares:
    - What the template expects (port_config variable references)
    - What the site currently has (site variables)
    - What we're about to set from the parsed Cisco config
    
    Args:
        template: Gateway template dict (or None if not exists)
        existing_site: Site dict with vars (or None if new site)
        wan_interfaces: List of WAN interface dicts from parsed config
    
    Returns:
        Dict with validation results:
        - template_expected_vars: Variables the template references
        - site_current_vars: Current site variable values
        - proposed_vars: What we're about to set
        - discrepancies: List of {var_name, issue, current, proposed}
        - missing_in_template: Vars we need but template doesn't have
        - missing_site_vars: Vars template needs but site doesn't have
    """
    import re
    
    discrepancies = []
    missing_in_template = []
    missing_site_vars = []
    
    # Build expected template variables from port_config
    template_expected_vars = set()
    if template:
        port_config = template.get("port_config", {})
        var_pattern = re.compile(r"\{\{(\w+)\}\}")
        
        # Check port_config keys and all nested values
        def extract_vars(obj):
            if isinstance(obj, str):
                return var_pattern.findall(obj)
            elif isinstance(obj, dict):
                vars_found = []
                for key, value in obj.items():
                    vars_found.extend(var_pattern.findall(key))
                    vars_found.extend(extract_vars(value))
                return vars_found
            elif isinstance(obj, list):
                vars_found = []
                for item in obj:
                    vars_found.extend(extract_vars(item))
                return vars_found
            return []
        
        for key in port_config:
            template_expected_vars.update(var_pattern.findall(key))
            template_expected_vars.update(extract_vars(port_config[key]))
    
    # Get current site variables
    site_current_vars = {}
    if existing_site:
        site_current_vars = existing_site.get("vars", {}) or {}
    
    # Build proposed variables from parsed WAN interfaces
    proposed_vars = {}
    for interface in wan_interfaces:
        var_name = interface.get("wan_var_name", "")
        if not var_name:
            continue
        
        interface_name = interface.get("name", "")
        wan_type = interface.get("wan_type", "broadband")
        
        # Base variable: interface name
        if interface_name:
            proposed_vars[var_name] = interface_name
        
        # Vanity name
        var_name_clean = var_name.replace("_lte", "")
        port_number = "".join(c for c in var_name_clean if c.isdigit()) or "1"
        if wan_type == "lte":
            proposed_vars[f"{var_name}_name"] = f"LTE {port_number}"
        else:
            proposed_vars[f"{var_name}_name"] = f"WAN {port_number}"
        
        # Description
        cisco_desc = interface.get("description", "")
        proposed_vars[f"{var_name}_desc"] = cisco_desc if cisco_desc else interface_name
        
        # IP config details
        ip_address = interface.get("ip_address", "")
        if ip_address:
            proposed_vars[f"{var_name}_ip"] = ip_address
        
        subnet_mask = interface.get("subnet_mask", "")
        if subnet_mask:
            proposed_vars[f"{var_name}_subnet"] = subnet_mask
        
        default_gateway = interface.get("default_gateway", "")
        if default_gateway:
            proposed_vars[f"{var_name}_gateway"] = default_gateway
        
        vlan_id = interface.get("encap_vlan_id", 0)
        if vlan_id and vlan_id > 0:
            proposed_vars[f"{var_name}_vlan"] = str(vlan_id)
        
        ip_config_type = interface.get("ip_config_type", "")
        if ip_config_type:
            proposed_vars[f"{var_name}_type"] = ip_config_type
        
        # LTE-specific
        if wan_type == "lte":
            lte_apn = interface.get("lte_apn", "")
            if lte_apn:
                proposed_vars[f"{var_name}_apn"] = lte_apn
            
            lte_auth = interface.get("lte_auth", "")
            if lte_auth:
                proposed_vars[f"{var_name}_auth"] = lte_auth
            
            lte_username = interface.get("lte_username", "")
            if lte_username:
                proposed_vars[f"{var_name}_user"] = lte_username
            
            lte_password = interface.get("lte_password", "")
            if lte_password:
                proposed_vars[f"{var_name}_pass"] = lte_password
    
    # Find discrepancies between current site vars and proposed
    for var_name, proposed_value in proposed_vars.items():
        current_value = site_current_vars.get(var_name)
        if current_value is not None and str(current_value) != str(proposed_value):
            discrepancies.append({
                "variable": var_name,
                "issue": "value_mismatch",
                "current": str(current_value),
                "proposed": str(proposed_value)
            })
    
    # Check if template has ports for all our WAN interfaces
    if template:
        template_wan_vars = {v for v in template_expected_vars 
                           if v.startswith("wan") and not "_" in v}
        proposed_wan_vars = {v for v in proposed_vars.keys() 
                           if v.startswith("wan") and not "_" in v}
        
        missing_in_template = list(proposed_wan_vars - template_wan_vars)
        
        # Check if site is missing vars that template expects
        for var in template_expected_vars:
            if var not in site_current_vars and var not in proposed_vars:
                missing_site_vars.append(var)
    
    return {
        "template_expected_vars": sorted(template_expected_vars),
        "site_current_vars": site_current_vars,
        "proposed_vars": proposed_vars,
        "discrepancies": discrepancies,
        "missing_in_template": sorted(missing_in_template),
        "missing_site_vars": sorted(missing_site_vars),
        "has_issues": len(discrepancies) > 0 or len(missing_in_template) > 0
    }


@app.route("/")
def index():
    """Main page - config upload interface."""
    theme = get_theme()
    poweruser = is_poweruser()
    return render_template("index.html", theme=theme, poweruser=poweruser)


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
    - hardware_type: 'srx' or 'ssr' (default: 'srx')
    
    Returns proposed changes including site lookup/creation plan.
    """
    config_content = None
    filename = None
    
    # Get gateway type
    gateway_type = request.form.get("gateway_type", "").lower()
    if gateway_type not in ["branch", "hub", "standalone"]:
        return jsonify({"error": "Gateway type must be 'branch', 'hub', or 'standalone'"}), 400
    
    # Get hardware type (SRX or SSR)
    hardware_type = request.form.get("hardware_type", "srx").lower()
    if hardware_type not in ["srx", "ssr"]:
        hardware_type = "srx"  # Default to SRX
    
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
    
    # Classify interfaces as WAN or LAN
    wan_threshold, lan_threshold = get_interface_classification_thresholds()
    for interface in parser.interfaces:
        classify_interface(interface, wan_threshold, lan_threshold)
    
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
    template = None
    
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
    
    # Extract WAN interfaces with high confidence
    wan_interfaces = extract_wan_interfaces(result, min_confidence=0.7)
    
    # Extract LAN interfaces, static routes, DHCP pools, and VRF instances
    lan_interfaces = extract_lan_interfaces(result, min_confidence=0.5)
    static_routes = extract_static_routes(result)
    dhcp_pools = extract_dhcp_pools(result)
    vrf_instances = extract_vrf_instances(result)
    
    if lan_interfaces:
        logger.info(f"Detected {len(lan_interfaces)} LAN interfaces: {[l['name'] for l in lan_interfaces]}")
    if static_routes:
        logger.info(f"Detected {len(static_routes)} static routes")
    if dhcp_pools:
        logger.info(f"Detected {len(dhcp_pools)} DHCP pools")
    if vrf_instances:
        logger.info(f"Detected {len(vrf_instances)} VRF instances: {[v['name'] for v in vrf_instances]}")
    
    # Check template for WAN variable usage
    wan_variable_info = None
    if gateway_type in ("branch", "standalone") and template_info and template_info.get("exists"):
        # Load full template to check port_config
        template_id = template_info.get("id")
        if gateway_type == "branch":
            template = get_template_manager().find_by_name(
                get_template_manager().branch_template_name
            )
        else:
            template = get_template_manager().find_by_name(
                get_template_manager().standalone_template_name
            )
        if template:
            wan_variable_info = check_template_wan_variables(template)
            # Add detected WAN interface count to var info
            wan_variable_info["detected_wan_count"] = len(wan_interfaces)
            wan_variable_info["detected_wan_interfaces"] = [
                w.get("name") for w in wan_interfaces
            ]
            # Determine if new vars need to be created
            existing_wan_vars = len(wan_variable_info.get("wan_var_names", []))
            wan_variable_info["needs_new_vars"] = len(wan_interfaces) > existing_wan_vars
    
    # Validate WAN configuration between template, site, and parsed config
    wan_validation = None
    if gateway_type in ("branch", "standalone"):
        wan_validation = validate_wan_configuration(
            template,
            existing_site,
            wan_interfaces
        )
    
    # Collect configuration warnings for preview
    preview_warnings: list[str] = []
    
    # Check for hub gateway using DHCP (not recommended for VPN stability)
    if gateway_type == "hub" and wan_interfaces:
        dhcp_wan_interfaces = [
            w.get("name", "Unknown") for w in wan_interfaces
            if w.get("ip_config_type", "").lower() == "dhcp"
        ]
        if dhcp_wan_interfaces:
            warning_msg = (
                f"Hub gateway WAN interfaces using DHCP: {', '.join(dhcp_wan_interfaces)}. "
                "Static IP is recommended for hub gateways to ensure stable VPN endpoint for spokes."
            )
            preview_warnings.append(warning_msg)
    
    # Build proposed changes
    proposed = {
        "gateway_type": gateway_type,
        "hardware_type": hardware_type,
        "device_name": device_name,
        "config_warnings": preview_warnings,
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
            "vlans": result.get("summary", {}).get("vlan_count", 0),
            "lan_interfaces": len(lan_interfaces),
            "dhcp_pools": len(dhcp_pools),
            "vrf_instances": len(vrf_instances)
        },
        "template_info": template_info,
        "template_comparison": template_comparison,
        "wan_interfaces": wan_interfaces,
        "lan_interfaces": lan_interfaces,
        "static_routes_extracted": static_routes,
        "dhcp_pools_extracted": dhcp_pools,
        "vrf_instances_extracted": vrf_instances,
        "wan_variable_info": wan_variable_info,
        "wan_validation": wan_validation,
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
    - hardware_type: 'srx' or 'ssr' (default: srx)
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
    hardware_type = data.get("hardware_type", "srx").lower()  # SRX or SSR
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
    
    # Validate hardware_type
    if hardware_type not in ["srx", "ssr"]:
        hardware_type = "srx"  # Default to SRX
    
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
    dns_domain_name: str = parsed_data.get("dns", {}).get("domain_name", "")
    
    # Extract SNMP config
    snmp_config = parsed_data.get("snmp", {})
    snmp_community_strings: list[dict] = snmp_config.get("community_strings", [])
    snmp_contact: str = snmp_config.get("contact", "")
    snmp_trap_hosts: list[dict] = snmp_config.get("trap_hosts", [])
    # Note: snmp.location is used for address parsing, not passed to SNMP config
    
    # Extract TACACS config
    tacacs_config = parsed_data.get("tacacs", {})
    tacacs_servers: list[dict] = tacacs_config.get("servers", [])
    tacacs_global_key: str = tacacs_config.get("key", "")
    tacacs_global_timeout: int = tacacs_config.get("timeout", 5)
    
    # Extract local accounts (only those with decodable passwords)
    local_accounts: list[dict] = parsed_data.get("local_accounts", [])
    decodable_accounts = [a for a in local_accounts if a.get("is_decodable")]
    
    # Extract WAN interfaces with high confidence for site variables
    wan_interface_list = extract_wan_interfaces(parsed_data, min_confidence=0.7)
    # Build dict of wan_var_name -> interface_name for site variables
    wan_interface_vars: dict[str, str] = {
        w.get("wan_var_name", ""): w.get("name", "") 
        for w in wan_interface_list 
        if w.get("wan_var_name") and w.get("name")
    }
    if wan_interface_vars:
        logger.info(f"Detected WAN interfaces: {wan_interface_vars}")
    
    # Collect configuration warnings
    config_warnings: list[str] = []
    
    # Check for hub gateway using DHCP (not recommended for VPN stability)
    if gateway_type == "hub" and wan_interface_list:
        dhcp_wan_interfaces = [
            w.get("name", "Unknown") for w in wan_interface_list
            if w.get("ip_config_type", "").lower() == "dhcp"
        ]
        if dhcp_wan_interfaces:
            warning_msg = (
                f"Hub gateway WAN interfaces using DHCP: {', '.join(dhcp_wan_interfaces)}. "
                "Static IP is recommended for hub gateways to ensure stable VPN endpoint for spokes."
            )
            config_warnings.append(warning_msg)
            logger.warning(warning_msg)
    
    if gateway_type == "branch":
        # Branch gateways use gateway template (type=spoke) attached to site
        # NTP/DNS values go to site variables, syslog goes to site settings
        branch_template, branch_template_created = get_template_manager().get_or_create_branch_template()
        if branch_template:
            gatewaytemplate_id = branch_template.get("id")
            logger.info(f"Using branch gateway template: {branch_template.get('name')} (ID: {gatewaytemplate_id}) {'(newly created)' if branch_template_created else '(existing)'}")
            
            # Check if template needs WAN variable ports
            if wan_interface_vars and gatewaytemplate_id:
                wan_var_check = check_template_wan_variables(branch_template)
                existing_wan_vars = wan_var_check.get("wan_var_names", [])
                # Always update template port_config to ensure name/description are set
                logger.info(f"Updating template WAN ports (existing: {len(existing_wan_vars)}, detected: {len(wan_interface_vars)})")
                get_template_manager().add_wan_variable_ports(
                    gatewaytemplate_id,
                    wan_interfaces=wan_interface_list,
                    existing_wan_vars=existing_wan_vars
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
            
            # Add WAN port configuration to hub profile
            if wan_interface_vars and hub_profile_id:
                wan_var_check = get_profile_manager().check_profile_wan_variables(hub_profile)
                existing_wan_vars = wan_var_check.get("wan_var_names", [])
                logger.info(f"Updating hub profile WAN ports (existing: {len(existing_wan_vars)}, detected: {len(wan_interface_vars)})")
                get_profile_manager().add_wan_variable_ports(
                    hub_profile_id,
                    wan_interfaces=wan_interface_list,
                    existing_wan_vars=existing_wan_vars
                )
        else:
            hub_profile_created = False
            logger.warning("Could not get/create hub device profile, continuing without it")
    
    elif gateway_type == "standalone":
        # Standalone gateways use gateway template (type=standalone) attached to site
        # NTP/DNS values go to site variables, syslog goes to site settings
        standalone_template, standalone_template_created = get_template_manager().get_or_create_standalone_template()
        if standalone_template:
            gatewaytemplate_id = standalone_template.get("id")
            logger.info(f"Using standalone gateway template: {standalone_template.get('name')} (ID: {gatewaytemplate_id}) {'(newly created)' if standalone_template_created else '(existing)'}")
            
            # Check if template needs WAN variable ports
            if wan_interface_vars and gatewaytemplate_id:
                wan_var_check = check_template_wan_variables(standalone_template)
                existing_wan_vars = wan_var_check.get("wan_var_names", [])
                # Always update template port_config to ensure name/description are set
                logger.info(f"Updating template WAN ports (existing: {len(existing_wan_vars)}, detected: {len(wan_interface_vars)})")
                get_template_manager().add_wan_variable_ports(
                    gatewaytemplate_id,
                    wan_interfaces=wan_interface_list,
                    existing_wan_vars=existing_wan_vars
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
    
    # Store NTP/DNS/Syslog/WAN as site variables for branch and standalone gateways
    # Hub gateways store values in the device profile instead
    # DNS suffix only applies to SRX gateways (not SSR)
    dns_suffix: list[str] = []
    if hardware_type == "srx" and dns_domain_name:
        dns_suffix = [dns_domain_name]
    
    if gateway_type in ("branch", "standalone") and site_id:
        # Update site with NTP/DNS/Syslog/WAN variables and DNS suffix
        # Pass full wan_interface_list for detailed site variable creation
        if ntp_servers or dns_servers or syslog_servers or dns_suffix or wan_interface_list:
            vars_result = get_site_manager().update_site_variables(
                site_id,
                ntp_servers=ntp_servers,
                dns_servers=dns_servers,
                syslog_servers=syslog_servers,
                dns_suffix=dns_suffix,
                wan_interfaces=wan_interface_list
            )
            if vars_result:
                var_types = []
                if ntp_servers:
                    var_types.append("NTP")
                if dns_servers:
                    var_types.append("DNS")
                if syslog_servers:
                    var_types.append("Syslog")
                if dns_suffix:
                    var_types.append("DNS suffix")
                if wan_interface_list:
                    var_types.append(f"WAN ({len(wan_interface_list)} interfaces)")
                logger.info(f"Updated site variables: {', '.join(var_types)}")
            else:
                logger.warning("Could not update site variables")
    
    # Configure SNMP and TACACS at site level (applies to all gateway types with a site)
    if site_id:
        # Update SNMP configuration
        if snmp_community_strings or snmp_contact or snmp_trap_hosts:
            snmp_result = get_site_manager().update_site_snmp(
                site_id,
                community_strings=snmp_community_strings,
                contact=snmp_contact,
                trap_hosts=snmp_trap_hosts
            )
            if snmp_result:
                logger.info("Updated site SNMP configuration")
            else:
                logger.warning("Could not update site SNMP configuration")
        
        # Update TACACS configuration
        if tacacs_servers:
            tacacs_result = get_site_manager().update_site_tacacs(
                site_id,
                tacacs_servers=tacacs_servers,
                global_key=tacacs_global_key,
                global_timeout=tacacs_global_timeout
            )
            if tacacs_result:
                logger.info("Updated site TACACS configuration")
            else:
                logger.warning("Could not update site TACACS configuration")
        
        # Update local accounts (only those with decodable passwords)
        if decodable_accounts:
            accounts_result = get_site_manager().update_site_local_accounts(
                site_id,
                local_accounts=decodable_accounts
            )
            if accounts_result:
                logger.info(
                    f"Updated site local accounts: "
                    f"{[a.get('username') for a in decodable_accounts]}"
                )
            else:
                logger.warning("Could not update site local accounts")
    
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
    
    # Add config warnings if any
    if config_warnings:
        response_data["warnings"] = config_warnings
    
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
        
        # Classify interfaces as WAN or LAN
        wan_threshold, lan_threshold = get_interface_classification_thresholds()
        for interface in parser.interfaces:
            classify_interface(interface, wan_threshold, lan_threshold)
        
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


# =============================================================================
# POWER USER ROUTES - Destructive operations for development/testing
# =============================================================================

@app.route("/api/poweruser/unassign-all-templates", methods=["POST"])
def poweruser_unassign_all_templates():
    """Unassign all gateway templates from all sites.
    
    DESTRUCTIVE: Requires POWERUSER=true in .env
    - Clears gatewaytemplate_id from all sites
    - Unassigns all devices from all device profiles
    
    Returns:
        JSON with operation results.
    """
    if not is_poweruser():
        return jsonify({"error": "Power user mode not enabled"}), 403
    
    # Require explicit confirmation
    data = request.get_json() or {}
    if data.get("confirmation") != "CONFIRM":
        return jsonify({
            "error": "Confirmation required",
            "message": "Send {\"confirmation\": \"CONFIRM\"} to proceed"
        }), 400
    
    logger.warning("POWERUSER: Unassigning all templates from all sites")
    
    results = {
        "sites_cleared": 0,
        "profiles_cleared": 0,
        "errors": []
    }
    
    try:
        site_manager = get_site_manager()
        profile_manager = get_profile_manager()
        
        # Get all sites
        sites = site_manager.list_all()
        
        for site in sites:
            site_id = site.get("id")
            site_name = site.get("name", "unknown")
            
            # Clear gateway template from site
            if site.get("gatewaytemplate_id"):
                try:
                    update_result = site_manager.update(
                        site_id,
                        None,  # No address update
                        gatewaytemplate_id=None,
                        clear_gatewaytemplate=True
                    )
                    if update_result:
                        results["sites_cleared"] += 1
                        logger.info(f"POWERUSER: Cleared template from site {site_name}")
                except Exception as error:
                    results["errors"].append(f"Site {site_name}: {str(error)}")
        
        # Get all device profiles (hub profiles)
        session = profile_manager.connection.session
        if session:
            try:
                import mistapi
                response = mistapi.api.v1.orgs.deviceprofiles.listOrgDeviceProfiles(
                    session, profile_manager.connection.org_id, type="gateway"
                )
                if response.status_code == 200:
                    profiles = response.data or []
                    for profile in profiles:
                        profile_id = profile.get("id")
                        profile_name = profile.get("name", "unknown")
                        
                        # Unassign all devices from this profile
                        try:
                            # Get devices assigned to this profile using inventory API
                            device_response = mistapi.api.v1.orgs.inventory.getOrgInventory(
                                session, 
                                profile_manager.connection.org_id,
                                type="gateway"
                            )
                            if device_response.status_code == 200:
                                devices = device_response.data or []
                                profile_devices = [
                                    d.get("mac") for d in devices 
                                    if d.get("deviceprofile_id") == profile_id and d.get("mac")
                                ]
                                if profile_devices:
                                    unassign_result = profile_manager.unassign_devices(
                                        profile_id, profile_devices
                                    )
                                    if unassign_result:
                                        results["profiles_cleared"] += 1
                                        logger.info(
                                            f"POWERUSER: Unassigned {len(profile_devices)} device(s) "
                                            f"from profile {profile_name}"
                                        )
                        except Exception as error:
                            results["errors"].append(f"Profile {profile_name}: {str(error)}")
            except Exception as error:
                results["errors"].append(f"Profile listing: {str(error)}")
        
        logger.warning(
            f"POWERUSER: Completed unassign-all. "
            f"Sites cleared: {results['sites_cleared']}, "
            f"Profiles cleared: {results['profiles_cleared']}"
        )
        
        return jsonify({
            "status": "success",
            "message": "All templates unassigned",
            "results": results
        })
        
    except Exception as error:
        logger.exception(f"POWERUSER: Error in unassign-all: {error}")
        return jsonify({"error": str(error)}), 500


@app.route("/api/poweruser/delete-all-templates", methods=["POST"])
def poweruser_delete_all_templates():
    """Delete all gateway templates and device profiles.
    
    DESTRUCTIVE: Requires POWERUSER=true in .env
    - Deletes all gateway templates (branch/standalone)
    - Deletes all device profiles (hub)
    
    Returns:
        JSON with operation results.
    """
    if not is_poweruser():
        return jsonify({"error": "Power user mode not enabled"}), 403
    
    # Require explicit confirmation
    data = request.get_json() or {}
    if data.get("confirmation") != "CONFIRM":
        return jsonify({
            "error": "Confirmation required",
            "message": "Send {\"confirmation\": \"CONFIRM\"} to proceed"
        }), 400
    
    logger.warning("POWERUSER: Deleting all gateway templates and device profiles")
    
    results = {
        "templates_deleted": 0,
        "profiles_deleted": 0,
        "errors": []
    }
    
    try:
        import mistapi
        connection = get_mist_connection()
        session = connection.session
        org_id = connection.org_id
        
        if not session:
            return jsonify({"error": "No Mist session available"}), 500
        
        # Delete all gateway templates
        try:
            response = mistapi.api.v1.orgs.gatewaytemplates.listOrgGatewayTemplates(
                session, org_id
            )
            if response.status_code == 200:
                templates = response.data or []
                for template in templates:
                    template_id = template.get("id")
                    template_name = template.get("name", "unknown")
                    try:
                        delete_response = mistapi.api.v1.orgs.gatewaytemplates.deleteOrgGatewayTemplate(
                            session, org_id, template_id
                        )
                        if delete_response.status_code in [200, 204]:
                            results["templates_deleted"] += 1
                            logger.info(f"POWERUSER: Deleted gateway template {template_name}")
                        else:
                            results["errors"].append(
                                f"Template {template_name}: HTTP {delete_response.status_code}"
                            )
                    except Exception as error:
                        results["errors"].append(f"Template {template_name}: {str(error)}")
        except Exception as error:
            results["errors"].append(f"Template listing: {str(error)}")
        
        # Delete all device profiles (hub profiles)
        try:
            response = mistapi.api.v1.orgs.deviceprofiles.listOrgDeviceProfiles(
                session, org_id, type="gateway"
            )
            if response.status_code == 200:
                profiles = response.data or []
                for profile in profiles:
                    profile_id = profile.get("id")
                    profile_name = profile.get("name", "unknown")
                    try:
                        delete_response = mistapi.api.v1.orgs.deviceprofiles.deleteOrgDeviceProfile(
                            session, org_id, profile_id
                        )
                        if delete_response.status_code in [200, 204]:
                            results["profiles_deleted"] += 1
                            logger.info(f"POWERUSER: Deleted device profile {profile_name}")
                        else:
                            results["errors"].append(
                                f"Profile {profile_name}: HTTP {delete_response.status_code}"
                            )
                    except Exception as error:
                        results["errors"].append(f"Profile {profile_name}: {str(error)}")
        except Exception as error:
            results["errors"].append(f"Profile listing: {str(error)}")
        
        logger.warning(
            f"POWERUSER: Completed delete-all. "
            f"Templates deleted: {results['templates_deleted']}, "
            f"Profiles deleted: {results['profiles_deleted']}"
        )
        
        return jsonify({
            "status": "success",
            "message": "All templates and profiles deleted",
            "results": results
        })
        
    except Exception as error:
        logger.exception(f"POWERUSER: Error in delete-all: {error}")
        return jsonify({"error": str(error)}), 500


@app.route("/api/poweruser/unassign-templated-devices", methods=["POST"])
def poweruser_unassign_templated_devices():
    """Unassign gateway devices that have templates or hub profiles assigned.
    
    DESTRUCTIVE: Requires POWERUSER=true in .env
    - Finds all sites with gatewaytemplate_id (branch/standalone templates)
    - Gets all gateway devices at those sites
    - Also finds devices with deviceprofile_id (hub profiles)
    - Moves all templated devices to unassigned inventory
    
    Returns:
        JSON with operation results.
    """
    if not is_poweruser():
        return jsonify({"error": "Power user mode not enabled"}), 403
    
    # Require explicit confirmation
    data = request.get_json() or {}
    if data.get("confirmation") != "CONFIRM":
        return jsonify({
            "error": "Confirmation required",
            "message": "Send {\"confirmation\": \"CONFIRM\"} to proceed"
        }), 400
    
    logger.warning("POWERUSER: Unassigning gateway devices from templated sites and hub profiles")
    
    results = {
        "sites_scanned": 0,
        "templated_sites": 0,
        "hub_devices_found": 0,
        "devices_unassigned": 0,
        "errors": []
    }
    
    try:
        import mistapi
        site_manager = get_site_manager()
        connection = get_mist_connection()
        session = connection.session
        org_id = connection.org_id
        
        all_macs_to_unassign = set()  # Use set to avoid duplicates
        
        # === Part 1: Find devices at sites with gateway templates (branch/standalone) ===
        sites = site_manager.list_all()
        results["sites_scanned"] = len(sites)
        
        for site in sites:
            site_id = site.get("id")
            site_name = site.get("name", "unknown")
            
            # Check if site has a gateway template assigned
            if site.get("gatewaytemplate_id"):
                results["templated_sites"] += 1
                logger.debug(f"POWERUSER: Site {site_name} has template assigned")
                
                # Get all gateway devices at this site
                try:
                    gateways = site_manager.get_site_gateways(site_id)
                    for gateway in gateways:
                        mac = gateway.get("mac")
                        if mac:
                            all_macs_to_unassign.add(mac)
                            logger.debug(f"POWERUSER: Will unassign gateway {mac} from templated site {site_name}")
                except Exception as error:
                    results["errors"].append(f"Site {site_name} device list: {str(error)}")
        
        # === Part 2: Find devices with hub profiles (deviceprofile_id) ===
        if session:
            try:
                # Use getOrgInventory to list all gateway devices (assigned or not)
                device_response = mistapi.api.v1.orgs.inventory.getOrgInventory(
                    session, org_id, type="gateway"
                )
                if device_response.status_code == 200:
                    all_devices = device_response.data or []
                    for device in all_devices:
                        # Check if device has a device profile (hub profile) assigned
                        if device.get("deviceprofile_id"):
                            mac = device.get("mac")
                            if mac:
                                results["hub_devices_found"] += 1
                                all_macs_to_unassign.add(mac)
                                device_name = device.get("name", mac)
                                logger.debug(f"POWERUSER: Will unassign hub device {device_name} ({mac})")
            except Exception as error:
                results["errors"].append(f"Org device listing: {str(error)}")
        
        # Unassign all collected devices in one batch
        macs_list = list(all_macs_to_unassign)
        if macs_list:
            logger.info(f"POWERUSER: Unassigning {len(macs_list)} device(s) from templated sites/hub profiles")
            
            unassign_result = site_manager.unassign_devices_from_site(macs_list)
            if unassign_result:
                success_list = unassign_result.get("success", [])
                error_list = unassign_result.get("error", [])
                results["devices_unassigned"] = len(success_list)
                
                if error_list:
                    for err in error_list:
                        results["errors"].append(f"Unassign error: {err}")
            else:
                results["errors"].append("Unassign operation failed")
        
        logger.warning(
            f"POWERUSER: Completed unassign-templated-devices. "
            f"Sites scanned: {results['sites_scanned']}, "
            f"Templated sites: {results['templated_sites']}, "
            f"Hub devices found: {results['hub_devices_found']}, "
            f"Devices unassigned: {results['devices_unassigned']}"
        )
        
        return jsonify({
            "status": "success",
            "message": f"Unassigned {results['devices_unassigned']} device(s) from {results['templated_sites']} templated site(s) and {results['hub_devices_found']} hub profile(s)",
            "results": results
        })
        
    except Exception as error:
        logger.exception(f"POWERUSER: Error in unassign-templated-devices: {error}")
        return jsonify({"error": str(error)}), 500


@app.route("/api/poweruser/backup-service-policies", methods=["POST"])
def poweruser_backup_service_policies():
    """Backup all organization service policies to a JSON file.
    
    NON-DESTRUCTIVE: Requires POWERUSER=true in .env
    - Fetches all service policies from the org
    - Saves them to data/service_policies_backup_<timestamp>.json
    
    Returns:
        JSON with backup results including filename and policy count.
    """
    if not is_poweruser():
        return jsonify({"error": "Power user mode not enabled"}), 403
    
    import json
    from datetime import datetime
    
    logger.info("POWERUSER: Backing up service policies")
    
    results = {
        "policies_count": 0,
        "filename": "",
        "errors": []
    }
    
    try:
        connection = get_mist_connection()
        session = connection.session
        org_id = connection.org_id
        
        if not session:
            return jsonify({"error": "Not connected to Mist API"}), 500
        
        # Fetch all service policies
        response = mistapi.api.v1.orgs.servicepolicies.listOrgServicePolicies(
            session, org_id
        )
        
        if response.status_code != 200:
            return jsonify({
                "error": f"API error: {response.status_code}"
            }), 500
        
        policies = response.data or []
        results["policies_count"] = len(policies)
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"service_policies_backup_{timestamp}.json"
        filepath = os.path.join(OUTPUT_DIR, filename)
        
        # Save to JSON file
        with open(filepath, "w", encoding="utf-8") as file:
            json.dump(policies, file, indent=2)
        
        results["filename"] = filename
        
        logger.info(
            f"POWERUSER: Backed up {results['policies_count']} service policies to {filename}"
        )
        
        return jsonify({
            "status": "success",
            "message": f"Backed up {results['policies_count']} service policies to {filename}",
            "results": results
        })
        
    except Exception as error:
        logger.exception(f"POWERUSER: Error backing up service policies: {error}")
        return jsonify({"error": str(error)}), 500


@app.route("/api/poweruser/list-service-policy-backups", methods=["GET"])
def poweruser_list_service_policy_backups():
    """List available service policy backup files.
    
    NON-DESTRUCTIVE: Requires POWERUSER=true in .env
    - Scans output folder for service_policies_backup_*.json files
    - Returns list of files with metadata
    
    Returns:
        JSON with list of backup files.
    """
    if not is_poweruser():
        return jsonify({"error": "Power user mode not enabled"}), 403
    
    import json
    import glob
    
    try:
        # Find all service policy backup files
        pattern = os.path.join(OUTPUT_DIR, "service_policies_backup_*.json")
        backup_files = glob.glob(pattern)
        
        files = []
        for filepath in sorted(backup_files, reverse=True):  # Most recent first
            filename = os.path.basename(filepath)
            try:
                with open(filepath, "r", encoding="utf-8") as file:
                    policies = json.load(file)
                    policies_count = len(policies) if isinstance(policies, list) else 0
            except Exception:
                policies_count = 0
            
            files.append({
                "filename": filename,
                "policies_count": policies_count
            })
        
        return jsonify({
            "status": "success",
            "files": files
        })
        
    except Exception as error:
        logger.exception(f"POWERUSER: Error listing backup files: {error}")
        return jsonify({"error": str(error)}), 500


@app.route("/api/poweruser/restore-service-policies", methods=["POST"])
def poweruser_restore_service_policies():
    """Restore service policies from a backup file.
    
    DESTRUCTIVE: Requires POWERUSER=true in .env
    - Reads policies from the specified backup file
    - For each policy: updates if exists (by name), creates if not
    
    Returns:
        JSON with restore results.
    """
    if not is_poweruser():
        return jsonify({"error": "Power user mode not enabled"}), 403
    
    # Require explicit confirmation
    data = request.get_json() or {}
    if data.get("confirmation") != "CONFIRM":
        return jsonify({
            "error": "Confirmation required",
            "message": "Send {\"confirmation\": \"CONFIRM\"} to proceed"
        }), 400
    
    filename = data.get("filename")
    if not filename:
        return jsonify({"error": "No filename specified"}), 400
    
    # Validate filename (prevent path traversal)
    if ".." in filename or "/" in filename or "\\" in filename:
        return jsonify({"error": "Invalid filename"}), 400
    
    import json
    
    logger.warning(f"POWERUSER: Restoring service policies from {filename}")
    
    results = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": []
    }
    
    try:
        filepath = os.path.join(OUTPUT_DIR, filename)
        if not os.path.exists(filepath):
            return jsonify({"error": f"Backup file not found: {filename}"}), 404
        
        # Load backup data
        with open(filepath, "r", encoding="utf-8") as file:
            backup_policies = json.load(file)
        
        if not isinstance(backup_policies, list):
            return jsonify({"error": "Invalid backup file format"}), 400
        
        connection = get_mist_connection()
        session = connection.session
        org_id = connection.org_id
        
        if not session:
            return jsonify({"error": "Not connected to Mist API"}), 500
        
        # Get current policies to check for existing ones
        current_response = mistapi.api.v1.orgs.servicepolicies.listOrgServicePolicies(
            session, org_id
        )
        current_policies = current_response.data or [] if current_response.status_code == 200 else []
        
        # Build lookup by name
        current_by_name = {p.get("name"): p for p in current_policies if p.get("name")}
        
        for policy in backup_policies:
            policy_name = policy.get("name")
            if not policy_name:
                results["skipped"] += 1
                results["errors"].append("Skipped policy with no name")
                continue
            
            # Remove read-only fields before create/update
            policy_data = {k: v for k, v in policy.items() 
                         if k not in ["id", "org_id", "created_time", "modified_time", "createdBy"]}
            
            try:
                if policy_name in current_by_name:
                    # Update existing policy
                    existing_id = current_by_name[policy_name].get("id")
                    update_response = mistapi.api.v1.orgs.servicepolicies.updateOrgServicePolicy(
                        session, org_id, existing_id, policy_data
                    )
                    if update_response.status_code in [200, 201]:
                        results["updated"] += 1
                        logger.debug(f"POWERUSER: Updated service policy: {policy_name}")
                    else:
                        results["errors"].append(f"Update {policy_name}: HTTP {update_response.status_code}")
                else:
                    # Create new policy
                    create_response = mistapi.api.v1.orgs.servicepolicies.createOrgServicePolicy(
                        session, org_id, policy_data
                    )
                    if create_response.status_code in [200, 201]:
                        results["created"] += 1
                        logger.debug(f"POWERUSER: Created service policy: {policy_name}")
                    else:
                        results["errors"].append(f"Create {policy_name}: HTTP {create_response.status_code}")
            except Exception as error:
                results["errors"].append(f"{policy_name}: {str(error)}")
        
        logger.warning(
            f"POWERUSER: Completed restore. Created: {results['created']}, "
            f"Updated: {results['updated']}, Skipped: {results['skipped']}"
        )
        
        return jsonify({
            "status": "success",
            "message": f"Restored {results['created']} new, {results['updated']} updated policies",
            "results": results
        })
        
    except Exception as error:
        logger.exception(f"POWERUSER: Error restoring service policies: {error}")
        return jsonify({"error": str(error)}), 500


@app.route("/api/poweruser/backup-services", methods=["POST"])
def poweruser_backup_services():
    """Backup all organization services to a JSON file.
    
    NON-DESTRUCTIVE: Requires POWERUSER=true in .env
    - Fetches all services from the org
    - Saves them to output/services_backup_<timestamp>.json
    
    Returns:
        JSON with backup results including filename and service count.
    """
    if not is_poweruser():
        return jsonify({"error": "Power user mode not enabled"}), 403
    
    import json
    from datetime import datetime
    
    logger.info("POWERUSER: Backing up services")
    
    results = {
        "services_count": 0,
        "filename": "",
        "errors": []
    }
    
    try:
        connection = get_mist_connection()
        session = connection.session
        org_id = connection.org_id
        
        if not session:
            return jsonify({"error": "Not connected to Mist API"}), 500
        
        # Fetch all services
        response = mistapi.api.v1.orgs.services.listOrgServices(
            session, org_id
        )
        
        if response.status_code != 200:
            return jsonify({
                "error": f"API error: {response.status_code}"
            }), 500
        
        services = response.data or []
        results["services_count"] = len(services)
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"services_backup_{timestamp}.json"
        filepath = os.path.join(OUTPUT_DIR, filename)
        
        # Save to JSON file
        with open(filepath, "w", encoding="utf-8") as file:
            json.dump(services, file, indent=2)
        
        results["filename"] = filename
        
        logger.info(
            f"POWERUSER: Backed up {results['services_count']} services to {filename}"
        )
        
        return jsonify({
            "status": "success",
            "message": f"Backed up {results['services_count']} services to {filename}",
            "results": results
        })
        
    except Exception as error:
        logger.exception(f"POWERUSER: Error backing up services: {error}")
        return jsonify({"error": str(error)}), 500


@app.route("/api/poweruser/list-service-backups", methods=["GET"])
def poweruser_list_service_backups():
    """List available service backup files.
    
    NON-DESTRUCTIVE: Requires POWERUSER=true in .env
    - Scans output folder for services_backup_*.json files
    - Returns list of files with metadata
    
    Returns:
        JSON with list of backup files.
    """
    if not is_poweruser():
        return jsonify({"error": "Power user mode not enabled"}), 403
    
    import json
    import glob
    
    try:
        # Find all service backup files
        pattern = os.path.join(OUTPUT_DIR, "services_backup_*.json")
        backup_files = glob.glob(pattern)
        
        files = []
        for filepath in sorted(backup_files, reverse=True):  # Most recent first
            filename = os.path.basename(filepath)
            try:
                with open(filepath, "r", encoding="utf-8") as file:
                    services = json.load(file)
                    services_count = len(services) if isinstance(services, list) else 0
            except Exception:
                services_count = 0
            
            files.append({
                "filename": filename,
                "services_count": services_count
            })
        
        return jsonify({
            "status": "success",
            "files": files
        })
        
    except Exception as error:
        logger.exception(f"POWERUSER: Error listing service backup files: {error}")
        return jsonify({"error": str(error)}), 500


@app.route("/api/poweruser/restore-services", methods=["POST"])
def poweruser_restore_services():
    """Restore services from a backup file.
    
    DESTRUCTIVE: Requires POWERUSER=true in .env
    - Reads services from the specified backup file
    - For each service: updates if exists (by name), creates if not
    
    Returns:
        JSON with restore results.
    """
    if not is_poweruser():
        return jsonify({"error": "Power user mode not enabled"}), 403
    
    # Require explicit confirmation
    data = request.get_json() or {}
    if data.get("confirmation") != "CONFIRM":
        return jsonify({
            "error": "Confirmation required",
            "message": "Send {\"confirmation\": \"CONFIRM\"} to proceed"
        }), 400
    
    filename = data.get("filename")
    if not filename:
        return jsonify({"error": "No filename specified"}), 400
    
    # Validate filename (prevent path traversal)
    if ".." in filename or "/" in filename or "\\" in filename:
        return jsonify({"error": "Invalid filename"}), 400
    
    import json
    
    logger.warning(f"POWERUSER: Restoring services from {filename}")
    
    results = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": []
    }
    
    try:
        filepath = os.path.join(OUTPUT_DIR, filename)
        if not os.path.exists(filepath):
            return jsonify({"error": f"Backup file not found: {filename}"}), 404
        
        # Load backup data
        with open(filepath, "r", encoding="utf-8") as file:
            backup_services = json.load(file)
        
        if not isinstance(backup_services, list):
            return jsonify({"error": "Invalid backup file format"}), 400
        
        connection = get_mist_connection()
        session = connection.session
        org_id = connection.org_id
        
        if not session:
            return jsonify({"error": "Not connected to Mist API"}), 500
        
        # Get current services to check for existing ones
        current_response = mistapi.api.v1.orgs.services.listOrgServices(
            session, org_id
        )
        current_services = current_response.data or [] if current_response.status_code == 200 else []
        
        # Build lookup by name
        current_by_name = {s.get("name"): s for s in current_services if s.get("name")}
        
        for service in backup_services:
            service_name = service.get("name")
            if not service_name:
                results["skipped"] += 1
                results["errors"].append("Skipped service with no name")
                continue
            
            # Remove read-only fields before create/update
            service_data = {k: v for k, v in service.items() 
                         if k not in ["id", "org_id", "created_time", "modified_time", "createdBy"]}
            
            try:
                if service_name in current_by_name:
                    # Update existing service
                    existing_id = current_by_name[service_name].get("id")
                    update_response = mistapi.api.v1.orgs.services.updateOrgService(
                        session, org_id, existing_id, service_data
                    )
                    if update_response.status_code in [200, 201]:
                        results["updated"] += 1
                        logger.debug(f"POWERUSER: Updated service: {service_name}")
                    else:
                        results["errors"].append(f"Update {service_name}: HTTP {update_response.status_code}")
                else:
                    # Create new service
                    create_response = mistapi.api.v1.orgs.services.createOrgService(
                        session, org_id, service_data
                    )
                    if create_response.status_code in [200, 201]:
                        results["created"] += 1
                        logger.debug(f"POWERUSER: Created service: {service_name}")
                    else:
                        results["errors"].append(f"Create {service_name}: HTTP {create_response.status_code}")
            except Exception as error:
                results["errors"].append(f"{service_name}: {str(error)}")
        
        logger.warning(
            f"POWERUSER: Completed service restore. Created: {results['created']}, "
            f"Updated: {results['updated']}, Skipped: {results['skipped']}"
        )
        
        return jsonify({
            "status": "success",
            "message": f"Restored {results['created']} new, {results['updated']} updated services",
            "results": results
        })
        
    except Exception as error:
        logger.exception(f"POWERUSER: Error restoring services: {error}")
        return jsonify({"error": str(error)}), 500


@app.route("/api/poweruser/delete-all-services", methods=["POST"])
def poweruser_delete_all_services():
    """Delete all organization services.
    
    DESTRUCTIVE: Requires POWERUSER=true in .env
    - Fetches all services from the org
    - Deletes each one
    
    Returns:
        JSON with deletion results.
    """
    if not is_poweruser():
        return jsonify({"error": "Power user mode not enabled"}), 403
    
    # Require explicit confirmation
    data = request.get_json() or {}
    if data.get("confirmation") != "CONFIRM":
        return jsonify({
            "error": "Confirmation required",
            "message": "Send {\"confirmation\": \"CONFIRM\"} to proceed"
        }), 400
    
    logger.warning("POWERUSER: Deleting all services")
    
    results = {
        "deleted": 0,
        "errors": []
    }
    
    try:
        connection = get_mist_connection()
        session = connection.session
        org_id = connection.org_id
        
        if not session:
            return jsonify({"error": "Not connected to Mist API"}), 500
        
        # Fetch all services
        response = mistapi.api.v1.orgs.services.listOrgServices(
            session, org_id
        )
        
        if response.status_code != 200:
            return jsonify({
                "error": f"API error: {response.status_code}"
            }), 500
        
        services = response.data or []
        
        for service in services:
            service_id = service.get("id")
            service_name = service.get("name", "Unknown")
            
            if not service_id:
                results["errors"].append(f"Service has no ID: {service_name}")
                continue
            
            try:
                delete_response = mistapi.api.v1.orgs.services.deleteOrgService(
                    session, org_id, service_id
                )
                if delete_response.status_code in [200, 204]:
                    results["deleted"] += 1
                    logger.debug(f"POWERUSER: Deleted service: {service_name}")
                else:
                    results["errors"].append(f"Delete {service_name}: HTTP {delete_response.status_code}")
            except Exception as error:
                results["errors"].append(f"{service_name}: {str(error)}")
        
        logger.warning(f"POWERUSER: Deleted {results['deleted']} services")
        
        return jsonify({
            "status": "success",
            "message": f"Deleted {results['deleted']} services",
            "results": results
        })
        
    except Exception as error:
        logger.exception(f"POWERUSER: Error deleting services: {error}")
        return jsonify({"error": str(error)}), 500


@app.route("/api/poweruser/backup-networks", methods=["POST"])
def poweruser_backup_networks():
    """Backup all organization networks to a JSON file.
    
    NON-DESTRUCTIVE: Requires POWERUSER=true in .env
    - Fetches all networks from the org
    - Saves them to output/networks_backup_<timestamp>.json
    
    Returns:
        JSON with backup results including filename and network count.
    """
    if not is_poweruser():
        return jsonify({"error": "Power user mode not enabled"}), 403
    
    import json
    from datetime import datetime
    
    logger.info("POWERUSER: Backing up networks")
    
    results = {
        "networks_count": 0,
        "filename": "",
        "errors": []
    }
    
    try:
        connection = get_mist_connection()
        session = connection.session
        org_id = connection.org_id
        
        if not session:
            return jsonify({"error": "Not connected to Mist API"}), 500
        
        # Fetch all networks
        response = mistapi.api.v1.orgs.networks.listOrgNetworks(
            session, org_id
        )
        
        if response.status_code != 200:
            return jsonify({
                "error": f"API error: {response.status_code}"
            }), 500
        
        networks = response.data or []
        results["networks_count"] = len(networks)
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"networks_backup_{timestamp}.json"
        filepath = os.path.join(OUTPUT_DIR, filename)
        
        # Save to JSON file
        with open(filepath, "w", encoding="utf-8") as file:
            json.dump(networks, file, indent=2)
        
        results["filename"] = filename
        
        logger.info(
            f"POWERUSER: Backed up {results['networks_count']} networks to {filename}"
        )
        
        return jsonify({
            "status": "success",
            "message": f"Backed up {results['networks_count']} networks to {filename}",
            "results": results
        })
        
    except Exception as error:
        logger.exception(f"POWERUSER: Error backing up networks: {error}")
        return jsonify({"error": str(error)}), 500


@app.route("/api/poweruser/list-network-backups", methods=["GET"])
def poweruser_list_network_backups():
    """List available network backup files.
    
    NON-DESTRUCTIVE: Requires POWERUSER=true in .env
    - Scans output folder for networks_backup_*.json files
    - Returns list of files with metadata
    
    Returns:
        JSON with list of backup files.
    """
    if not is_poweruser():
        return jsonify({"error": "Power user mode not enabled"}), 403
    
    import json
    import glob
    
    try:
        # Find all network backup files
        pattern = os.path.join(OUTPUT_DIR, "networks_backup_*.json")
        backup_files = glob.glob(pattern)
        
        files = []
        for filepath in sorted(backup_files, reverse=True):  # Most recent first
            filename = os.path.basename(filepath)
            try:
                with open(filepath, "r", encoding="utf-8") as file:
                    networks = json.load(file)
                    networks_count = len(networks) if isinstance(networks, list) else 0
            except Exception:
                networks_count = 0
            
            files.append({
                "filename": filename,
                "networks_count": networks_count
            })
        
        return jsonify({
            "status": "success",
            "files": files
        })
        
    except Exception as error:
        logger.exception(f"POWERUSER: Error listing network backup files: {error}")
        return jsonify({"error": str(error)}), 500


@app.route("/api/poweruser/restore-networks", methods=["POST"])
def poweruser_restore_networks():
    """Restore networks from a backup file.
    
    DESTRUCTIVE: Requires POWERUSER=true in .env
    - Reads networks from the specified backup file
    - For each network: updates if exists (by name), creates if not
    
    Returns:
        JSON with restore results.
    """
    if not is_poweruser():
        return jsonify({"error": "Power user mode not enabled"}), 403
    
    # Require explicit confirmation
    data = request.get_json() or {}
    if data.get("confirmation") != "CONFIRM":
        return jsonify({
            "error": "Confirmation required",
            "message": "Send {\"confirmation\": \"CONFIRM\"} to proceed"
        }), 400
    
    filename = data.get("filename")
    if not filename:
        return jsonify({"error": "No filename specified"}), 400
    
    # Validate filename (prevent path traversal)
    if ".." in filename or "/" in filename or "\\" in filename:
        return jsonify({"error": "Invalid filename"}), 400
    
    import json
    
    logger.warning(f"POWERUSER: Restoring networks from {filename}")
    
    results = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": []
    }
    
    try:
        filepath = os.path.join(OUTPUT_DIR, filename)
        if not os.path.exists(filepath):
            return jsonify({"error": f"Backup file not found: {filename}"}), 404
        
        # Load backup data
        with open(filepath, "r", encoding="utf-8") as file:
            backup_networks = json.load(file)
        
        if not isinstance(backup_networks, list):
            return jsonify({"error": "Invalid backup file format"}), 400
        
        connection = get_mist_connection()
        session = connection.session
        org_id = connection.org_id
        
        if not session:
            return jsonify({"error": "Not connected to Mist API"}), 500
        
        # Get current networks to check for existing ones
        current_response = mistapi.api.v1.orgs.networks.listOrgNetworks(
            session, org_id
        )
        current_networks = current_response.data or [] if current_response.status_code == 200 else []
        
        # Build lookup by name
        current_by_name = {n.get("name"): n for n in current_networks if n.get("name")}
        
        for network in backup_networks:
            network_name = network.get("name")
            if not network_name:
                results["skipped"] += 1
                results["errors"].append("Skipped network with no name")
                continue
            
            # Remove read-only fields before create/update
            network_data = {k: v for k, v in network.items() 
                         if k not in ["id", "org_id", "created_time", "modified_time", "createdBy"]}
            
            try:
                if network_name in current_by_name:
                    # Update existing network
                    existing_id = current_by_name[network_name].get("id")
                    update_response = mistapi.api.v1.orgs.networks.updateOrgNetwork(
                        session, org_id, existing_id, network_data
                    )
                    if update_response.status_code in [200, 201]:
                        results["updated"] += 1
                        logger.debug(f"POWERUSER: Updated network: {network_name}")
                    else:
                        results["errors"].append(f"Update {network_name}: HTTP {update_response.status_code}")
                else:
                    # Create new network
                    create_response = mistapi.api.v1.orgs.networks.createOrgNetwork(
                        session, org_id, network_data
                    )
                    if create_response.status_code in [200, 201]:
                        results["created"] += 1
                        logger.debug(f"POWERUSER: Created network: {network_name}")
                    else:
                        results["errors"].append(f"Create {network_name}: HTTP {create_response.status_code}")
            except Exception as error:
                results["errors"].append(f"{network_name}: {str(error)}")
        
        logger.warning(
            f"POWERUSER: Completed network restore. Created: {results['created']}, "
            f"Updated: {results['updated']}, Skipped: {results['skipped']}"
        )
        
        return jsonify({
            "status": "success",
            "message": f"Restored {results['created']} new, {results['updated']} updated networks",
            "results": results
        })
        
    except Exception as error:
        logger.exception(f"POWERUSER: Error restoring networks: {error}")
        return jsonify({"error": str(error)}), 500


@app.route("/api/poweruser/delete-all-networks", methods=["POST"])
def poweruser_delete_all_networks():
    """Delete all organization networks.
    
    DESTRUCTIVE: Requires POWERUSER=true in .env
    - Fetches all networks from the org
    - Deletes each one
    
    Returns:
        JSON with deletion results.
    """
    if not is_poweruser():
        return jsonify({"error": "Power user mode not enabled"}), 403
    
    # Require explicit confirmation
    data = request.get_json() or {}
    if data.get("confirmation") != "CONFIRM":
        return jsonify({
            "error": "Confirmation required",
            "message": "Send {\"confirmation\": \"CONFIRM\"} to proceed"
        }), 400
    
    logger.warning("POWERUSER: Deleting all networks")
    
    results = {
        "deleted": 0,
        "errors": []
    }
    
    try:
        connection = get_mist_connection()
        session = connection.session
        org_id = connection.org_id
        
        if not session:
            return jsonify({"error": "Not connected to Mist API"}), 500
        
        # Fetch all networks
        response = mistapi.api.v1.orgs.networks.listOrgNetworks(
            session, org_id
        )
        
        if response.status_code != 200:
            return jsonify({
                "error": f"API error: {response.status_code}"
            }), 500
        
        networks = response.data or []
        
        for network in networks:
            network_id = network.get("id")
            network_name = network.get("name", "Unknown")
            
            if not network_id:
                results["errors"].append(f"Network has no ID: {network_name}")
                continue
            
            try:
                delete_response = mistapi.api.v1.orgs.networks.deleteOrgNetwork(
                    session, org_id, network_id
                )
                if delete_response.status_code in [200, 204]:
                    results["deleted"] += 1
                    logger.debug(f"POWERUSER: Deleted network: {network_name}")
                else:
                    results["errors"].append(f"Delete {network_name}: HTTP {delete_response.status_code}")
            except Exception as error:
                results["errors"].append(f"{network_name}: {str(error)}")
        
        logger.warning(f"POWERUSER: Deleted {results['deleted']} networks")
        
        return jsonify({
            "status": "success",
            "message": f"Deleted {results['deleted']} networks",
            "results": results
        })
        
    except Exception as error:
        logger.exception(f"POWERUSER: Error deleting networks: {error}")
        return jsonify({"error": str(error)}), 500


if __name__ == "__main__":
    # Development server only - use Gunicorn in production
    app.run(host="0.0.0.0", port=8000, debug=True)
