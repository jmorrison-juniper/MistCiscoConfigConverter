"""
Cisco IOS/IOS-XE Configuration Parser

Breaks down Cisco configurations into logical sections and extracts
variables for display and conversion to Juniper Mist format.
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# Cisco Type 7 password decoding
# The XOR key used by Cisco for Type 7 encoding
CISCO_TYPE7_KEY = "dsfd;kfoA,.iyewrkldJKDHSUBsgvca69834ncxv9873254k;fg87"


def decode_cisco_type7(encoded: str) -> str:
    """Decode a Cisco Type 7 encoded password.
    
    Type 7 is a weak XOR-based encoding (not encryption) using a known key.
    Format: 2-digit seed (00-15) followed by hex pairs.
    
    Args:
        encoded: The Type 7 encoded string (e.g., "060506324F41")
    
    Returns:
        Decoded plaintext password, or original string if decoding fails.
    """
    if not encoded or len(encoded) < 4:
        return encoded
    
    try:
        # First 2 characters are the seed (decimal 00-15)
        seed = int(encoded[:2])
        if seed < 0 or seed > 15:
            logger.debug(f"Invalid Type 7 seed: {seed}")
            return encoded
        
        # Remaining characters are hex pairs
        hex_chars = encoded[2:]
        if len(hex_chars) % 2 != 0:
            logger.debug(f"Invalid Type 7 hex length: {len(hex_chars)}")
            return encoded
        
        # Decode each byte
        decoded = []
        key_len = len(CISCO_TYPE7_KEY)
        for i in range(0, len(hex_chars), 2):
            hex_byte = hex_chars[i:i+2]
            try:
                byte_val = int(hex_byte, 16)
            except ValueError:
                logger.debug(f"Invalid hex byte: {hex_byte}")
                return encoded
            
            # XOR with key at position (seed + i/2) mod key_length
            key_index = (seed + i // 2) % key_len
            decoded_char = chr(byte_val ^ ord(CISCO_TYPE7_KEY[key_index]))
            decoded.append(decoded_char)
        
        result = "".join(decoded)
        logger.debug(f"Decoded Type 7 password (length {len(result)})")
        return result
        
    except Exception as error:
        logger.debug(f"Type 7 decode error: {error}")
        return encoded


def extract_speed_from_description(description: str) -> tuple[int, int]:
    """Extract upload and download bandwidth speeds from interface description.
    
    Parses common speed patterns found in interface descriptions:
    - "100M", "100Mbps", "100 Mbps", "100Meg" (symmetric)
    - "1G", "1Gbps", "1 Gig", "1Gb" (symmetric)
    - "10G", "10Gbps" (symmetric)
    - "50M MPLS", "BDIA 100Meg", "Internet 1G" (symmetric)
    - "BDW=500/500" (upload/download format, Mbps)
    - "BDW=500" (symmetric, Mbps)
    
    Args:
        description: Interface description string
    
    Returns:
        Tuple of (upload_kbps, download_kbps). Both 0 if not found/parseable.
    """
    if not description:
        return (0, 0)
    
    # Normalize description
    desc_upper = description.upper()
    
    # Check for carrier circuit format first: BDW=500/500 or BDW=500
    # This is a common format in structured circuit descriptions
    # Pattern: BDW=<upload>/<download> or BDW=<symmetric>
    bdw_match = re.search(r'BDW[=:]?\s*(\d+)(?:/(\d+))?', desc_upper)
    if bdw_match:
        upload_mbps = int(bdw_match.group(1))
        download_mbps = int(bdw_match.group(2)) if bdw_match.group(2) else upload_mbps
        upload_kbps = upload_mbps * 1000
        download_kbps = download_mbps * 1000
        logger.debug(
            f"Extracted speeds {upload_kbps}/{download_kbps} kbps from BDW format: {description}"
        )
        return (upload_kbps, download_kbps)
    
    # Speed multipliers
    multipliers = {
        "K": 1,           # Kbps
        "M": 1000,        # Mbps -> kbps
        "G": 1000000,     # Gbps -> kbps
        "MEG": 1000,      # Alternate for Mbps
        "GIG": 1000000,   # Alternate for Gbps
    }
    
    # Regex patterns for speed extraction
    # Match patterns like: 100M, 100Mbps, 100 Mbps, 100Meg, 1G, 1Gbps, 1Gig
    patterns = [
        # Match number followed by unit (with optional space)
        r'(\d+(?:\.\d+)?)\s*(G(?:BPS|IG|B)?|M(?:BPS|EG|B)?|K(?:BPS|B)?)\b',
        # Match unit followed by number (less common but seen)  
        r'\b(G(?:BPS|IG)?|M(?:BPS|EG)?)\s*(\d+(?:\.\d+)?)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, desc_upper)
        if match:
            groups = match.groups()
            
            # Determine which group has the number vs unit
            if groups[0].replace(".", "").isdigit():
                num_str = groups[0]
                unit = groups[1]
            else:
                unit = groups[0]
                num_str = groups[1]
            
            try:
                speed_value = float(num_str)
                
                # Find matching multiplier
                for unit_key, mult in multipliers.items():
                    if unit.startswith(unit_key):
                        result_kbps = int(speed_value * mult)
                        logger.debug(
                            f"Extracted speed {result_kbps} kbps from description: {description}"
                        )
                        # Symmetric speed for non-BDW formats
                        return (result_kbps, result_kbps)
            except (ValueError, TypeError):
                continue
    
    return (0, 0)


@dataclass
class SystemInfo:
    """Global system configuration."""
    hostname: str = ""
    version: str = ""
    boot_image: str = ""
    last_config_change: str = ""
    config_size_bytes: int = 0
    services: list = field(default_factory=list)
    platform_settings: list = field(default_factory=list)


@dataclass
class VRFDefinition:
    """VRF configuration."""
    name: str = ""
    route_distinguisher: str = ""
    address_families: list = field(default_factory=list)
    description: str = ""


@dataclass
class AAAConfig:
    """AAA authentication/authorization configuration."""
    new_model: bool = False
    server_groups: list = field(default_factory=list)
    authentication_methods: list = field(default_factory=list)
    authorization_methods: list = field(default_factory=list)
    accounting_methods: list = field(default_factory=list)


@dataclass
class TACACSConfig:
    """TACACS+ server configuration."""
    servers: list = field(default_factory=list)  # List of {host, port, key, timeout}
    timeout: int = 5
    key: str = ""  # Global key if set


@dataclass
class CellularProfile:
    """Cisco cellular data profile configuration.
    
    Parses settings from:
    - profile cellular data <id>
    - Cellular controller lte sim data-profile
    - chat-script modem commands
    """
    profile_id: int = 0
    apn: str = ""
    authentication: str = ""  # none, chap, pap, pap_chap
    username: str = ""
    password: str = ""  # Decoded if Type 7
    password_type: str = ""  # "0", "7" (encoded)
    primary_pdp: str = ""  # ip, ipv6, ipv4v6
    slot: int = 0  # SIM slot (0 or 1)


@dataclass
class LocalAccount:
    """Local user account with password and role mapping."""
    username: str = ""
    privilege: int = 1
    password: str = ""  # Decoded password (empty if non-decodable)
    password_type: str = ""  # "0", "5", "7", "8", "9"
    is_decodable: bool = False  # True for Type 0/7, False for Type 5/8/9
    mist_role: str = "read"  # admin, helpdesk, read, none


@dataclass
class UserAccount:
    """Legacy user account (kept for backwards compatibility)."""
    username: str = ""
    privilege: int = 1
    secret_type: str = ""


@dataclass
class VLANConfig:
    """VLAN configuration."""
    vlan_id: int = 0
    name: str = ""
    state: str = "active"


@dataclass
class InterfaceConfig:
    """Interface configuration."""
    name: str = ""
    description: str = ""
    ip_address: str = ""
    subnet_mask: str = ""
    ipv6_addresses: list = field(default_factory=list)
    vrf: str = ""
    shutdown: bool = False
    switchport_mode: str = ""
    switchport_access_vlan: int = 0
    switchport_trunk_allowed: list = field(default_factory=list)
    switchport_trunk_native: int = 0
    security_zone: str = ""
    nat_direction: str = ""
    qos_policy_in: str = ""
    qos_policy_out: str = ""
    flow_monitor_in: str = ""
    flow_monitor_out: str = ""
    cdp_enabled: bool = True
    pim_mode: str = ""
    nhrp_config: dict = field(default_factory=dict)
    tunnel_config: dict = field(default_factory=dict)
    tunnel_source: str = ""  # Source interface for tunnel (e.g., "GigabitEthernet0/0/0")
    raw_config: list = field(default_factory=list)
    # DHCP relay helpers
    helper_addresses: list = field(default_factory=list)
    # IP configuration type and details
    ip_config_type: str = ""  # static, dhcp, pppoe, negotiated
    default_gateway: str = ""  # Next-hop gateway for static configs
    encap_vlan_id: int = 0  # VLAN ID for subinterface encapsulation (dot1q)
    pppoe_group: str = ""  # PPPoE dialer group name
    negotiated: bool = False  # IP address negotiated (PPP/PPPoE)
    # Cellular profile reference (for Cellular interfaces)
    cellular_profile_id: int = 0  # ID of the data profile attached to this cellular interface
    # Bandwidth configuration (from bandwidth command)
    bandwidth_kbps: int = 0  # Interface bandwidth in kbps (legacy, kept for compatibility)
    derived_bandwidth_kbps: int = 0  # Derived from tunnel or description hints (legacy)
    bandwidth_source: str = ""  # Where bandwidth was derived from: "config", "tunnel", "description"
    # Separate upload/download speeds (in kbps) for traffic shaping
    upload_kbps: int = 0  # Upload bandwidth from config or derived
    download_kbps: int = 0  # Download bandwidth from config or derived
    derived_upload_kbps: int = 0  # Derived upload speed from tunnel or description
    derived_download_kbps: int = 0  # Derived download speed from tunnel or description
    # Classification fields (populated by classify_interface)
    interface_role: str = ""  # wan, lan, management, unknown
    port_type: str = ""  # trunk, access, routed, virtual, tunnel, cellular
    wan_score: float = 0.0  # Confidence score for WAN classification (0.0-1.0)
    lan_score: float = 0.0  # Confidence score for LAN classification (0.0-1.0)
    classification_confidence: float = 0.0  # Confidence in the classification
    classification_indicators: list = field(default_factory=list)  # Reasons for classification


# Interface classification weights
# Positive = WAN indicator, Negative = LAN indicator
INTERFACE_CLASSIFICATION_WEIGHTS = {
    # NAT direction is a strong indicator
    "nat_outside": 0.35,  # Strong WAN indicator
    "nat_inside": -0.35,  # Strong LAN indicator
    
    # Security zones
    "zone_inet": 0.30,  # INET/WAN zone
    "zone_wan": 0.30,
    "zone_outside": 0.25,
    "zone_guest": -0.15,  # Guest is still LAN-side
    "zone_inside": -0.25,
    "zone_lan": -0.25,
    
    # VRF naming patterns
    "vrf_inet": 0.25,
    "vrf_wan": 0.25,
    "vrf_internet": 0.25,
    
    # Interface types
    "type_cellular": 0.40,  # Cellular is almost always WAN
    "type_tunnel": 0.0,  # Tunnels are logical overlays, not physical WAN interfaces
    "type_vlan_svi": -0.20,  # SVIs are typically LAN
    "type_loopback": 0.0,  # Neutral - management/routing
    
    # Switchport configuration
    "switchport_trunk": -0.30,  # Trunk to LAN switches
    "switchport_access": -0.20,  # Access port to end devices
    
    # IP addressing
    "ip_public": 0.35,  # Public IP = WAN
    "ip_private": -0.15,  # Private IP suggests LAN (but WAN can have private too)
    
    # DHCP relay
    "dhcp_helper": -0.25,  # DHCP relay = LAN facing
    
    # Description keywords
    "desc_wan": 0.20,
    "desc_inet": 0.20,
    "desc_internet": 0.20,
    "desc_carrier": 0.20,
    "desc_isp": 0.20,
    "desc_lan": -0.20,
    "desc_data": -0.15,
    "desc_voice": -0.15,
    "desc_trunk": -0.20,
    
    # Other indicators
    "cdp_disabled": 0.10,  # CDP often disabled on WAN
    "shutdown": 0.0,  # Neutral - could be either
}


def is_rfc1918_address(ip_address: str) -> bool:
    """Check if an IP address is in RFC1918 private space."""
    if not ip_address:
        return False
    try:
        parts = ip_address.split(".")
        if len(parts) != 4:
            return False
        first = int(parts[0])
        second = int(parts[1])
        
        # 10.0.0.0/8
        if first == 10:
            return True
        # 172.16.0.0/12
        if first == 172 and 16 <= second <= 31:
            return True
        # 192.168.0.0/16
        if first == 192 and second == 168:
            return True
        return False
    except (ValueError, IndexError):
        return False


def classify_interface(
    interface: InterfaceConfig,
    wan_threshold: float = 0.3,
    lan_threshold: float = -0.3
) -> InterfaceConfig:
    """
    Classify an interface as WAN, LAN, or unknown based on weighted indicators.
    
    Args:
        interface: The InterfaceConfig to classify
        wan_threshold: Score above this = WAN (default 0.3)
        lan_threshold: Score below this = LAN (default -0.3)
    
    Returns:
        The same InterfaceConfig with classification fields populated
    """
    score = 0.0
    indicators = []
    
    # NAT direction
    if interface.nat_direction == "outside":
        score += INTERFACE_CLASSIFICATION_WEIGHTS["nat_outside"]
        indicators.append(("nat_outside", INTERFACE_CLASSIFICATION_WEIGHTS["nat_outside"]))
    elif interface.nat_direction == "inside":
        score += INTERFACE_CLASSIFICATION_WEIGHTS["nat_inside"]
        indicators.append(("nat_inside", INTERFACE_CLASSIFICATION_WEIGHTS["nat_inside"]))
    
    # Security zone analysis
    zone_lower = interface.security_zone.lower()
    if "inet" in zone_lower:
        score += INTERFACE_CLASSIFICATION_WEIGHTS["zone_inet"]
        indicators.append(("zone_inet", INTERFACE_CLASSIFICATION_WEIGHTS["zone_inet"]))
    elif "wan" in zone_lower:
        score += INTERFACE_CLASSIFICATION_WEIGHTS["zone_wan"]
        indicators.append(("zone_wan", INTERFACE_CLASSIFICATION_WEIGHTS["zone_wan"]))
    elif "outside" in zone_lower:
        score += INTERFACE_CLASSIFICATION_WEIGHTS["zone_outside"]
        indicators.append(("zone_outside", INTERFACE_CLASSIFICATION_WEIGHTS["zone_outside"]))
    elif "guest" in zone_lower:
        score += INTERFACE_CLASSIFICATION_WEIGHTS["zone_guest"]
        indicators.append(("zone_guest", INTERFACE_CLASSIFICATION_WEIGHTS["zone_guest"]))
    elif "inside" in zone_lower:
        score += INTERFACE_CLASSIFICATION_WEIGHTS["zone_inside"]
        indicators.append(("zone_inside", INTERFACE_CLASSIFICATION_WEIGHTS["zone_inside"]))
    elif "lan" in zone_lower:
        score += INTERFACE_CLASSIFICATION_WEIGHTS["zone_lan"]
        indicators.append(("zone_lan", INTERFACE_CLASSIFICATION_WEIGHTS["zone_lan"]))
    
    # VRF naming patterns
    vrf_lower = interface.vrf.lower()
    if "inet" in vrf_lower:
        score += INTERFACE_CLASSIFICATION_WEIGHTS["vrf_inet"]
        indicators.append(("vrf_inet", INTERFACE_CLASSIFICATION_WEIGHTS["vrf_inet"]))
    elif "wan" in vrf_lower:
        score += INTERFACE_CLASSIFICATION_WEIGHTS["vrf_wan"]
        indicators.append(("vrf_wan", INTERFACE_CLASSIFICATION_WEIGHTS["vrf_wan"]))
    elif "internet" in vrf_lower:
        score += INTERFACE_CLASSIFICATION_WEIGHTS["vrf_internet"]
        indicators.append(("vrf_internet", INTERFACE_CLASSIFICATION_WEIGHTS["vrf_internet"]))
    
    # Interface type classification
    name_lower = interface.name.lower()
    if name_lower.startswith("cellular"):
        score += INTERFACE_CLASSIFICATION_WEIGHTS["type_cellular"]
        indicators.append(("type_cellular", INTERFACE_CLASSIFICATION_WEIGHTS["type_cellular"]))
        interface.port_type = "cellular"
    elif name_lower.startswith("tunnel"):
        # Tunnels are logical overlays (DMVPN, IPsec, GRE) - not physical WAN interfaces
        # They should be classified as "tunnel" role, not WAN
        indicators.append(("type_tunnel", 0.0))
        interface.port_type = "tunnel"
        interface.interface_role = "tunnel"  # Force tunnel role - skip WAN/LAN classification
        interface.classification_confidence = 1.0
        interface.wan_score = 0.0
        interface.lan_score = 0.0
        interface.classification_indicators = [
            f"{ind[0]}:{ind[1]:+.2f}" for ind in indicators
        ]
        return interface  # Early return - don't continue scoring
    elif name_lower.startswith("vlan"):
        score += INTERFACE_CLASSIFICATION_WEIGHTS["type_vlan_svi"]
        indicators.append(("type_vlan_svi", INTERFACE_CLASSIFICATION_WEIGHTS["type_vlan_svi"]))
        interface.port_type = "virtual"
    elif name_lower.startswith("loopback"):
        interface.port_type = "virtual"
    
    # Switchport mode
    if interface.switchport_mode == "trunk":
        score += INTERFACE_CLASSIFICATION_WEIGHTS["switchport_trunk"]
        indicators.append(("switchport_trunk", INTERFACE_CLASSIFICATION_WEIGHTS["switchport_trunk"]))
        if not interface.port_type:
            interface.port_type = "trunk"
    elif interface.switchport_mode == "access":
        score += INTERFACE_CLASSIFICATION_WEIGHTS["switchport_access"]
        indicators.append(("switchport_access", INTERFACE_CLASSIFICATION_WEIGHTS["switchport_access"]))
        if not interface.port_type:
            interface.port_type = "access"
    elif interface.ip_address and not interface.port_type:
        interface.port_type = "routed"
    
    # IP address analysis
    if interface.ip_address:
        if is_rfc1918_address(interface.ip_address):
            score += INTERFACE_CLASSIFICATION_WEIGHTS["ip_private"]
            indicators.append(("ip_private", INTERFACE_CLASSIFICATION_WEIGHTS["ip_private"]))
        else:
            score += INTERFACE_CLASSIFICATION_WEIGHTS["ip_public"]
            indicators.append(("ip_public", INTERFACE_CLASSIFICATION_WEIGHTS["ip_public"]))
    
    # DHCP helper addresses
    if interface.helper_addresses:
        score += INTERFACE_CLASSIFICATION_WEIGHTS["dhcp_helper"]
        indicators.append(("dhcp_helper", INTERFACE_CLASSIFICATION_WEIGHTS["dhcp_helper"]))
    
    # Description keyword analysis
    desc_lower = interface.description.lower()
    desc_keywords_wan = ["wan", "inet", "internet", "carrier", "isp", "bdia"]
    desc_keywords_lan = ["lan", "data", "voice", "trunk"]
    
    for keyword in desc_keywords_wan:
        if keyword in desc_lower:
            weight_key = f"desc_{keyword}" if f"desc_{keyword}" in INTERFACE_CLASSIFICATION_WEIGHTS else "desc_wan"
            score += INTERFACE_CLASSIFICATION_WEIGHTS.get(weight_key, 0.15)
            indicators.append((f"desc_{keyword}", INTERFACE_CLASSIFICATION_WEIGHTS.get(weight_key, 0.15)))
            break  # Only count once
    
    for keyword in desc_keywords_lan:
        if keyword in desc_lower:
            weight_key = f"desc_{keyword}" if f"desc_{keyword}" in INTERFACE_CLASSIFICATION_WEIGHTS else "desc_lan"
            score += INTERFACE_CLASSIFICATION_WEIGHTS.get(weight_key, -0.15)
            indicators.append((f"desc_{keyword}", INTERFACE_CLASSIFICATION_WEIGHTS.get(weight_key, -0.15)))
            break  # Only count once
    
    # CDP disabled (minor WAN indicator)
    if not interface.cdp_enabled:
        score += INTERFACE_CLASSIFICATION_WEIGHTS["cdp_disabled"]
        indicators.append(("cdp_disabled", INTERFACE_CLASSIFICATION_WEIGHTS["cdp_disabled"]))
    
    # Normalize score to -1.0 to 1.0 range (approximately)
    # Max possible positive score is around 1.5, max negative around -1.5
    normalized_score = max(-1.0, min(1.0, score))
    
    # Calculate WAN and LAN confidence scores (0.0 to 1.0)
    if normalized_score > 0:
        interface.wan_score = normalized_score
        interface.lan_score = 0.0
    else:
        interface.wan_score = 0.0
        interface.lan_score = abs(normalized_score)
    
    # Classify based on thresholds
    if score >= wan_threshold:
        interface.interface_role = "wan"
        interface.classification_confidence = min(1.0, score / wan_threshold * 0.5 + 0.5)
    elif score <= lan_threshold:
        interface.interface_role = "lan"
        interface.classification_confidence = min(1.0, abs(score / lan_threshold) * 0.5 + 0.5)
    else:
        interface.interface_role = "unknown"
        interface.classification_confidence = 0.5 - abs(score) * 0.5
    
    # Store indicators for debugging/display
    interface.classification_indicators = [
        f"{ind[0]}:{ind[1]:+.2f}" for ind in indicators
    ]
    
    logger.debug(
        f"Interface {interface.name}: score={score:.2f}, "
        f"role={interface.interface_role}, confidence={interface.classification_confidence:.2f}"
    )
    
    return interface


@dataclass
class DHCPPool:
    """DHCP pool configuration."""
    name: str = ""
    vrf: str = ""
    network: str = ""
    subnet_mask: str = ""
    default_router: str = ""
    dns_servers: list = field(default_factory=list)
    excluded_addresses: list = field(default_factory=list)


@dataclass
class StaticRoute:
    """Static route configuration."""
    vrf: str = ""
    destination: str = ""
    mask: str = ""
    next_hop: str = ""
    interface: str = ""
    name: str = ""
    distance: int = 1


@dataclass
class BGPConfig:
    """BGP routing configuration."""
    asn: int = 0
    router_id: str = ""
    neighbors: list = field(default_factory=list)
    networks: list = field(default_factory=list)
    address_families: list = field(default_factory=list)
    raw_config: list = field(default_factory=list)


@dataclass
class ClassMap:
    """QoS class-map configuration."""
    name: str = ""
    match_type: str = "match-any"
    inspect_type: bool = False
    match_conditions: list = field(default_factory=list)


@dataclass
class PolicyMap:
    """QoS policy-map configuration."""
    name: str = ""
    inspect_type: bool = False
    classes: list = field(default_factory=list)
    raw_config: list = field(default_factory=list)


@dataclass
class SecurityZone:
    """Zone-based firewall zone configuration."""
    name: str = ""
    description: str = ""


@dataclass 
class ZonePair:
    """Zone-pair security configuration."""
    name: str = ""
    source_zone: str = ""
    destination_zone: str = ""
    service_policy: str = ""


@dataclass
class CryptoConfig:
    """Crypto/VPN configuration."""
    ikev2_proposals: list = field(default_factory=list)
    ikev2_policies: list = field(default_factory=list)
    ikev2_profiles: list = field(default_factory=list)
    ipsec_profiles: list = field(default_factory=list)
    ipsec_transform_sets: list = field(default_factory=list)
    pki_trustpoints: list = field(default_factory=list)


@dataclass
class NetFlowConfig:
    """NetFlow/monitoring configuration."""
    exporters: list = field(default_factory=list)
    monitors: list = field(default_factory=list)


@dataclass
class SNMPConfig:
    """SNMP configuration."""
    community_strings: list = field(default_factory=list)
    trap_hosts: list = field(default_factory=list)
    location: str = ""
    contact: str = ""


@dataclass
class LoggingConfig:
    """Logging configuration."""
    buffer_size: int = 0
    console_enabled: bool = True
    monitor_enabled: bool = True
    trap_level: str = ""
    hosts: list = field(default_factory=list)
    discriminators: list = field(default_factory=list)


@dataclass
class NTPConfig:
    """NTP configuration."""
    servers: list = field(default_factory=list)
    source_interface: str = ""


@dataclass
class DNSConfig:
    """DNS/Name server configuration."""
    servers: list = field(default_factory=list)
    domain_name: str = ""
    domain_lookup: bool = True


@dataclass
class AccessList:
    """Access list configuration."""
    name: str = ""
    acl_type: str = ""  # standard, extended, named
    entries: list = field(default_factory=list)


class CiscoConfigParser:
    """
    Parses Cisco IOS/IOS-XE configurations into structured sections.
    
    All parsing happens server-side. Returns structured data for display
    or conversion to Mist format.
    """
    
    def __init__(self, config_text: str):
        """Initialize parser with raw config text."""
        self.raw_config = config_text
        self.lines = config_text.splitlines()
        self.current_line_index = 0
        
        # Parsed sections
        self.system = SystemInfo()
        self.vrfs: list[VRFDefinition] = []
        self.aaa = AAAConfig()
        self.tacacs = TACACSConfig()
        self.users: list[UserAccount] = []
        self.local_accounts: list[LocalAccount] = []
        self.vlans: list[VLANConfig] = []
        self.interfaces: list[InterfaceConfig] = []
        self.dhcp_pools: list[DHCPPool] = []
        self.static_routes: list[StaticRoute] = []
        self.bgp = BGPConfig()
        self.class_maps: list[ClassMap] = []
        self.policy_maps: list[PolicyMap] = []
        self.security_zones: list[SecurityZone] = []
        self.zone_pairs: list[ZonePair] = []
        self.crypto = CryptoConfig()
        self.netflow = NetFlowConfig()
        self.snmp = SNMPConfig()
        self.logging_config = LoggingConfig()
        self.ntp = NTPConfig()
        self.dns = DNSConfig()
        self.access_lists: list[AccessList] = []
        self.cellular_profiles: list[CellularProfile] = []
        
        # Parsing state
        self.parse_errors: list[str] = []
    
    def parse(self) -> dict:
        """
        Parse the entire configuration and return structured data.
        
        Returns:
            Dictionary with all parsed sections and variables.
        """
        logger.info("Starting Cisco config parse")
        
        try:
            self._parse_system_info()
            self._parse_vrfs()
            self._parse_aaa()
            self._parse_tacacs()
            self._parse_users()
            self._parse_vlans()
            self._parse_dhcp()
            self._parse_class_maps()
            self._parse_policy_maps()
            self._parse_security_zones()
            self._parse_zone_pairs()
            self._parse_crypto()
            self._parse_cellular_profiles()
            self._parse_interfaces()
            self._derive_interface_bandwidth()
            self._parse_routing()
            self._parse_netflow()
            self._parse_logging()
            self._parse_snmp()
            self._parse_ntp()
            self._parse_dns()
            self._parse_access_lists()
        except Exception as error:
            logger.error(f"Parse error: {error}")
            self.parse_errors.append(str(error))
        
        return self.to_dict()
    
    def _parse_system_info(self):
        """Extract system/global configuration."""
        for line in self.lines:
            line = line.strip()
            
            # Hostname
            if line.startswith("hostname "):
                self.system.hostname = line.split("hostname ", 1)[1].strip()
            
            # Version
            elif line.startswith("version "):
                self.system.version = line.split("version ", 1)[1].strip()
            
            # Boot image
            elif line.startswith("boot system "):
                match = re.search(r"boot system \S+ (\S+)", line)
                if match:
                    self.system.boot_image = match.group(1)
            
            # Last config change
            elif "Last configuration change" in line:
                self.system.last_config_change = line
            
            # Config size
            elif "Current configuration :" in line:
                match = re.search(r"(\d+) bytes", line)
                if match:
                    self.system.config_size_bytes = int(match.group(1))
            
            # Services
            elif line.startswith("service "):
                self.system.services.append(line.split("service ", 1)[1])
            
            # Platform settings
            elif line.startswith("platform "):
                self.system.platform_settings.append(line)
    
    def _parse_vrfs(self):
        """Extract VRF definitions."""
        in_vrf = False
        current_vrf = None
        
        for line in self.lines:
            stripped = line.strip()
            
            if stripped.startswith("vrf definition "):
                in_vrf = True
                current_vrf = VRFDefinition(
                    name=stripped.split("vrf definition ", 1)[1]
                )
            elif in_vrf and current_vrf:
                if stripped.startswith("rd "):
                    current_vrf.route_distinguisher = stripped.split("rd ", 1)[1]
                elif stripped.startswith("address-family "):
                    current_vrf.address_families.append(
                        stripped.split("address-family ", 1)[1]
                    )
                elif stripped.startswith("description "):
                    current_vrf.description = stripped.split("description ", 1)[1]
                elif stripped == "!" or (not line.startswith(" ") and line.strip()):
                    if current_vrf.name:
                        self.vrfs.append(current_vrf)
                    in_vrf = False
                    current_vrf = None
    
    def _parse_aaa(self):
        """Extract AAA configuration."""
        for line in self.lines:
            stripped = line.strip()
            
            if stripped == "aaa new-model":
                self.aaa.new_model = True
            elif stripped.startswith("aaa group server "):
                parts = stripped.split()
                if len(parts) >= 4:
                    self.aaa.server_groups.append({
                        "type": parts[3],
                        "name": parts[4] if len(parts) > 4 else ""
                    })
            elif stripped.startswith("aaa authentication "):
                self.aaa.authentication_methods.append(stripped)
            elif stripped.startswith("aaa authorization "):
                self.aaa.authorization_methods.append(stripped)
            elif stripped.startswith("aaa accounting "):
                self.aaa.accounting_methods.append(stripped)
    
    def _parse_tacacs(self):
        """Extract TACACS+ server configuration.
        
        Handles both legacy and modern IOS syntax:
        - Legacy: tacacs-server host <ip> [key <key>] [port <port>]
        - Modern: tacacs server <name> / address ipv4 <ip> / key 7 <key> / timeout <sec>
        """
        in_tacacs_server = False
        current_server: dict = {}
        
        for i, line in enumerate(self.lines):
            stripped = line.strip()
            
            # Modern syntax: tacacs server <name> (block start)
            if stripped.startswith("tacacs server "):
                # Save previous server if exists
                if current_server and current_server.get("host"):
                    self.tacacs.servers.append(current_server)
                
                server_name = stripped.split("tacacs server ", 1)[1].strip()
                current_server = {"name": server_name, "host": "", "port": 49, "timeout": 5, "key": ""}
                in_tacacs_server = True
            
            # Inside tacacs server block
            elif in_tacacs_server:
                if stripped.startswith("address ipv4 "):
                    current_server["host"] = stripped.split("address ipv4 ", 1)[1].strip()
                elif stripped.startswith("address ipv6 "):
                    # Also capture IPv6 if no IPv4
                    if not current_server.get("host"):
                        current_server["host"] = stripped.split("address ipv6 ", 1)[1].strip()
                elif stripped.startswith("key "):
                    # key 7 <encrypted> or key 0 <plaintext> or key <plaintext>
                    key_part = stripped.split("key ", 1)[1].strip()
                    if key_part.startswith("7 "):
                        # Type 7 encoded - decode it
                        encoded_key = key_part[2:]
                        current_server["key"] = decode_cisco_type7(encoded_key)
                    elif key_part.startswith("0 "):
                        # Plaintext with "0 " prefix
                        current_server["key"] = key_part[2:]
                    else:
                        # Plaintext without prefix
                        current_server["key"] = key_part
                elif stripped.startswith("timeout "):
                    try:
                        current_server["timeout"] = int(stripped.split("timeout ", 1)[1].strip())
                    except ValueError:
                        pass
                elif stripped.startswith("port "):
                    try:
                        current_server["port"] = int(stripped.split("port ", 1)[1].strip())
                    except ValueError:
                        pass
                elif stripped == "!" or (not stripped.startswith(" ") and stripped):
                    # End of block
                    if current_server and current_server.get("host"):
                        self.tacacs.servers.append(current_server)
                    in_tacacs_server = False
                    current_server = {}
            
            # Legacy syntax: tacacs-server host <ip> [key <key>] [port <port>] [timeout <seconds>]
            elif stripped.startswith("tacacs-server host "):
                parts = stripped.split()
                host = parts[2] if len(parts) > 2 else ""
                server = {"host": host, "port": 49, "timeout": 5, "key": ""}
                
                # Parse optional parameters
                for j, part in enumerate(parts):
                    if part == "key" and j + 1 < len(parts):
                        key_val = parts[j + 1]
                        # Check for "key 7 <encoded>" format
                        if key_val == "7" and j + 2 < len(parts):
                            server["key"] = decode_cisco_type7(parts[j + 2])
                        elif key_val == "0" and j + 2 < len(parts):
                            server["key"] = parts[j + 2]
                        else:
                            server["key"] = key_val
                    elif part == "port" and j + 1 < len(parts):
                        try:
                            server["port"] = int(parts[j + 1])
                        except ValueError:
                            pass
                    elif part == "timeout" and j + 1 < len(parts):
                        try:
                            server["timeout"] = int(parts[j + 1])
                        except ValueError:
                            pass
                
                if host:
                    self.tacacs.servers.append(server)
            
            # tacacs-server key <key> (global key)
            elif stripped.startswith("tacacs-server key "):
                key_part = stripped.split("tacacs-server key ", 1)[1].strip()
                # Handle "key 7 <encoded>" or "key 0 <plain>" or "key <plain>"
                parts = key_part.split()
                if len(parts) >= 2 and parts[0] == "7":
                    self.tacacs.key = decode_cisco_type7(parts[1])
                elif len(parts) >= 2 and parts[0] == "0":
                    self.tacacs.key = parts[1]
                else:
                    self.tacacs.key = key_part
            
            # tacacs-server timeout <seconds> (global timeout)
            elif stripped.startswith("tacacs-server timeout "):
                timeout_part = stripped.split("tacacs-server timeout ", 1)[1]
                try:
                    self.tacacs.timeout = int(timeout_part.strip())
                except ValueError:
                    pass
        
        # Don't forget last server if file doesn't end with !
        if current_server and current_server.get("host"):
            self.tacacs.servers.append(current_server)
    
    def _parse_users(self):
        """Extract local user accounts and decode passwords where possible."""
        for line in self.lines:
            stripped = line.strip()
            
            if stripped.startswith("username "):
                # Legacy UserAccount parsing (backwards compatibility)
                match = re.match(
                    r"username (\S+)(?: privilege (\d+))?(?: secret \d+ )?",
                    stripped
                )
                if match:
                    user = UserAccount(
                        username=match.group(1),
                        privilege=int(match.group(2)) if match.group(2) else 1
                    )
                    self.users.append(user)
                
                # Enhanced LocalAccount parsing with password decoding
                # Formats:
                #   username <name> privilege <n> password|secret <type> <value>
                #   username <name> password|secret <type> <value>
                enhanced_match = re.match(
                    r"username (\S+)"
                    r"(?: privilege (\d+))?"
                    r"(?: (?:password|secret) (\d+) (.+))?",
                    stripped
                )
                if enhanced_match:
                    username = enhanced_match.group(1)
                    privilege = int(enhanced_match.group(2)) if enhanced_match.group(2) else 1
                    password_type = enhanced_match.group(3) or ""
                    password_value = enhanced_match.group(4) or ""
                    
                    # Map privilege level to Mist role
                    if privilege >= 15:
                        mist_role = "admin"
                    elif privilege >= 5:
                        mist_role = "helpdesk"
                    elif privilege >= 1:
                        mist_role = "read"
                    else:
                        mist_role = "none"
                    
                    # Determine if password is decodable and decode if possible
                    is_decodable = password_type in ("0", "7")
                    decoded_password = ""
                    
                    if password_type == "0":
                        # Plaintext
                        decoded_password = password_value
                    elif password_type == "7":
                        # Type 7 - decode it
                        decoded_password = decode_cisco_type7(password_value)
                    elif password_type in ("5", "8", "9"):
                        # Hashed - cannot decode
                        logger.debug(
                            f"User '{username}' has Type {password_type} password "
                            "(not decodable)"
                        )
                    
                    account = LocalAccount(
                        username=username,
                        privilege=privilege,
                        password=decoded_password,
                        password_type=password_type,
                        is_decodable=is_decodable,
                        mist_role=mist_role
                    )
                    self.local_accounts.append(account)
    
    def _parse_vlans(self):
        """Extract VLAN configurations."""
        in_vlan = False
        current_vlan = None
        
        for line in self.lines:
            stripped = line.strip()
            
            if re.match(r"^vlan \d+$", stripped):
                if current_vlan:
                    self.vlans.append(current_vlan)
                vlan_id = int(stripped.split("vlan ", 1)[1])
                current_vlan = VLANConfig(vlan_id=vlan_id)
                in_vlan = True
            elif in_vlan and current_vlan:
                if stripped.startswith("name "):
                    current_vlan.name = stripped.split("name ", 1)[1]
                elif stripped.startswith("state "):
                    current_vlan.state = stripped.split("state ", 1)[1]
                elif stripped == "!" or (not line.startswith(" ") and line.strip()):
                    self.vlans.append(current_vlan)
                    in_vlan = False
                    current_vlan = None
        
        # Catch last VLAN
        if current_vlan:
            self.vlans.append(current_vlan)
    
    def _parse_dhcp(self):
        """Extract DHCP pool configurations."""
        in_pool = False
        current_pool = None
        
        # Parse excluded addresses
        excluded_by_vrf: dict = {}
        for line in self.lines:
            stripped = line.strip()
            if stripped.startswith("ip dhcp excluded-address"):
                match = re.match(
                    r"ip dhcp excluded-address(?: vrf (\S+))? (\S+)(?: (\S+))?",
                    stripped
                )
                if match:
                    vrf = match.group(1) or ""
                    start_ip = match.group(2)
                    end_ip = match.group(3) or start_ip
                    if vrf not in excluded_by_vrf:
                        excluded_by_vrf[vrf] = []
                    excluded_by_vrf[vrf].append(f"{start_ip} - {end_ip}")
        
        # Parse pools
        for line in self.lines:
            stripped = line.strip()
            
            if stripped.startswith("ip dhcp pool "):
                if current_pool:
                    self.dhcp_pools.append(current_pool)
                pool_name = stripped.split("ip dhcp pool ", 1)[1]
                current_pool = DHCPPool(name=pool_name)
                in_pool = True
            elif in_pool and current_pool:
                if stripped.startswith("vrf "):
                    current_pool.vrf = stripped.split("vrf ", 1)[1]
                    # Attach excluded addresses for this VRF
                    current_pool.excluded_addresses = excluded_by_vrf.get(
                        current_pool.vrf, []
                    )
                elif stripped.startswith("network "):
                    parts = stripped.split()
                    if len(parts) >= 3:
                        current_pool.network = parts[1]
                        current_pool.subnet_mask = parts[2]
                elif stripped.startswith("default-router "):
                    current_pool.default_router = stripped.split(
                        "default-router ", 1
                    )[1].strip()
                elif stripped.startswith("dns-server "):
                    dns_servers = stripped.split("dns-server ", 1)[1].split()
                    current_pool.dns_servers = dns_servers
                elif stripped == "!" or (not line.startswith(" ") and line.strip()):
                    self.dhcp_pools.append(current_pool)
                    in_pool = False
                    current_pool = None
        
        if current_pool:
            self.dhcp_pools.append(current_pool)
    
    def _parse_class_maps(self):
        """Extract QoS class-map configurations."""
        in_class_map = False
        current_cm = None
        
        for line in self.lines:
            stripped = line.strip()
            
            if stripped.startswith("class-map "):
                if current_cm:
                    self.class_maps.append(current_cm)
                
                # Parse class-map type
                match = re.match(
                    r"class-map(?: type inspect)? (match-\S+) (\S+)",
                    stripped
                )
                if match:
                    current_cm = ClassMap(
                        name=match.group(2),
                        match_type=match.group(1),
                        inspect_type="type inspect" in stripped
                    )
                    in_class_map = True
            elif in_class_map and current_cm:
                if stripped.startswith("match "):
                    current_cm.match_conditions.append(stripped)
                elif stripped == "!" or (not line.startswith(" ") and line.strip()):
                    self.class_maps.append(current_cm)
                    in_class_map = False
                    current_cm = None
        
        if current_cm:
            self.class_maps.append(current_cm)
    
    def _parse_policy_maps(self):
        """Extract QoS policy-map configurations."""
        in_policy_map = False
        current_pm = None
        
        for line in self.lines:
            stripped = line.strip()
            
            if stripped.startswith("policy-map "):
                if current_pm:
                    self.policy_maps.append(current_pm)
                
                name = stripped.split(" ")[-1]
                current_pm = PolicyMap(
                    name=name,
                    inspect_type="type inspect" in stripped
                )
                in_policy_map = True
            elif in_policy_map and current_pm:
                if stripped == "!" or (not line.startswith(" ") and stripped):
                    if not stripped.startswith("class "):
                        self.policy_maps.append(current_pm)
                        in_policy_map = False
                        current_pm = None
                else:
                    current_pm.raw_config.append(stripped)
        
        if current_pm:
            self.policy_maps.append(current_pm)
    
    def _parse_security_zones(self):
        """Extract zone-based firewall zones."""
        for line in self.lines:
            stripped = line.strip()
            
            if stripped.startswith("zone security "):
                zone_name = stripped.split("zone security ", 1)[1]
                self.security_zones.append(SecurityZone(name=zone_name))
    
    def _parse_zone_pairs(self):
        """Extract zone-pair security configurations."""
        in_zone_pair = False
        current_zp = None
        
        for line in self.lines:
            stripped = line.strip()
            
            if stripped.startswith("zone-pair security "):
                if current_zp:
                    self.zone_pairs.append(current_zp)
                
                match = re.match(
                    r"zone-pair security (\S+) source (\S+) destination (\S+)",
                    stripped
                )
                if match:
                    current_zp = ZonePair(
                        name=match.group(1),
                        source_zone=match.group(2),
                        destination_zone=match.group(3)
                    )
                    in_zone_pair = True
            elif in_zone_pair and current_zp:
                if stripped.startswith("service-policy type inspect "):
                    current_zp.service_policy = stripped.split(
                        "service-policy type inspect ", 1
                    )[1]
                elif stripped == "!" or (not line.startswith(" ") and line.strip()):
                    self.zone_pairs.append(current_zp)
                    in_zone_pair = False
                    current_zp = None
        
        if current_zp:
            self.zone_pairs.append(current_zp)
    
    def _parse_crypto(self):
        """Extract crypto/VPN configurations."""
        for line in self.lines:
            stripped = line.strip()
            
            if stripped.startswith("crypto ikev2 proposal "):
                self.crypto.ikev2_proposals.append(
                    stripped.split("crypto ikev2 proposal ", 1)[1]
                )
            elif stripped.startswith("crypto ikev2 policy "):
                self.crypto.ikev2_policies.append(
                    stripped.split("crypto ikev2 policy ", 1)[1]
                )
            elif stripped.startswith("crypto ikev2 profile "):
                self.crypto.ikev2_profiles.append(
                    stripped.split("crypto ikev2 profile ", 1)[1]
                )
            elif stripped.startswith("crypto ipsec profile "):
                self.crypto.ipsec_profiles.append(
                    stripped.split("crypto ipsec profile ", 1)[1]
                )
            elif stripped.startswith("crypto ipsec transform-set "):
                self.crypto.ipsec_transform_sets.append(stripped)
            elif stripped.startswith("crypto pki trustpoint "):
                self.crypto.pki_trustpoints.append(
                    stripped.split("crypto pki trustpoint ", 1)[1]
                )
    
    def _parse_cellular_profiles(self):
        """Extract cellular data profile configurations for LTE/4G interfaces.
        
        Parses:
        - profile cellular data <id>
        - controller Cellular <slot>
        - lte sim data-profile commands
        
        Common Cisco cellular config patterns:
        
        profile cellular data 1
         apn internet.carrier.com
         authentication chap
         username apn_user password 7 ENCODED_PWD
        
        controller Cellular 0/2/0
         lte sim data-profile 1 slot 0
        """
        in_profile = False
        current_profile = None
        
        # Map interface names to their data-profile IDs
        interface_profile_map: dict[str, int] = {}
        
        for index, line in enumerate(self.lines):
            stripped = line.strip()
            
            # Profile cellular data <id>
            if stripped.startswith("profile cellular data "):
                if current_profile:
                    self.cellular_profiles.append(current_profile)
                
                try:
                    profile_id = int(stripped.split("profile cellular data ", 1)[1])
                except ValueError:
                    profile_id = 0
                
                current_profile = CellularProfile(profile_id=profile_id)
                in_profile = True
                
            elif in_profile and current_profile:
                if stripped.startswith("apn "):
                    current_profile.apn = stripped.split("apn ", 1)[1].strip()
                    
                elif stripped.startswith("authentication "):
                    auth_type = stripped.split("authentication ", 1)[1].strip().lower()
                    # Normalize auth types: chap, pap, pap_chap, none
                    if "chap" in auth_type and "pap" in auth_type:
                        current_profile.authentication = "pap_chap"
                    elif "chap" in auth_type:
                        current_profile.authentication = "chap"
                    elif "pap" in auth_type:
                        current_profile.authentication = "pap"
                    else:
                        current_profile.authentication = "none"
                        
                elif stripped.startswith("username "):
                    # username <user> password [0|7] <password>
                    parts = stripped.split()
                    if len(parts) >= 2:
                        current_profile.username = parts[1]
                    
                    # Find password position
                    if "password" in parts:
                        pwd_idx = parts.index("password")
                        if len(parts) > pwd_idx + 1:
                            # Check for password type (0 or 7)
                            next_part = parts[pwd_idx + 1]
                            if next_part in ("0", "7"):
                                current_profile.password_type = next_part
                                if len(parts) > pwd_idx + 2:
                                    raw_pwd = parts[pwd_idx + 2]
                                    if next_part == "7":
                                        current_profile.password = decode_type7_password(raw_pwd)
                                    else:
                                        current_profile.password = raw_pwd
                            else:
                                # No type specified, assume cleartext
                                current_profile.password_type = "0"
                                current_profile.password = next_part
                                
                elif stripped.startswith("primary-pdp-type "):
                    current_profile.primary_pdp = stripped.split("primary-pdp-type ", 1)[1].strip()
                    
                elif stripped == "!" or (not stripped.startswith(" ") and stripped != ""):
                    # End of profile block
                    in_profile = False
            
            # Controller Cellular <slot> - extract data-profile assignments
            if stripped.startswith("controller Cellular "):
                controller_name = stripped.split("controller Cellular ", 1)[1].strip()
                # Read subsequent lines for lte sim data-profile
                for next_index in range(index + 1, min(index + 20, len(self.lines))):
                    next_line = self.lines[next_index].strip()
                    if next_line == "!" or (not next_line.startswith(" ") and next_line != ""):
                        break
                    
                    # lte sim data-profile <id> [attach-profile <id>] [slot <n>]
                    if "lte sim data-profile" in next_line:
                        match = re.search(r"lte sim data-profile\s+(\d+)", next_line)
                        if match:
                            profile_id = int(match.group(1))
                            # Extract slot if present
                            slot_match = re.search(r"slot\s+(\d+)", next_line)
                            slot_num = int(slot_match.group(1)) if slot_match else 0
                            
                            # Map this controller interface to the profile
                            # Convert controller name to interface name: 0/2/0 -> Cellular0/2/0
                            interface_name = f"Cellular{controller_name}"
                            interface_profile_map[interface_name] = profile_id
                            
                            # Also update the profile's slot
                            for profile in self.cellular_profiles:
                                if profile.profile_id == profile_id:
                                    profile.slot = slot_num
                                    break
        
        # Save final profile
        if current_profile:
            self.cellular_profiles.append(current_profile)
        
        # Store the interface mapping for later use in _parse_interfaces
        self._interface_cellular_profile_map = interface_profile_map
        
        if self.cellular_profiles:
            logger.info(f"Parsed {len(self.cellular_profiles)} cellular profile(s)")
            for profile in self.cellular_profiles:
                logger.debug(
                    f"Profile {profile.profile_id}: APN={profile.apn}, "
                    f"Auth={profile.authentication}, User={profile.username}"
                )

    def _parse_interfaces(self):
        """Extract interface configurations."""
        in_interface = False
        current_if = None
        
        # Get cellular profile mapping if available
        cellular_profile_map = getattr(self, "_interface_cellular_profile_map", {})
        
        for line in self.lines:
            stripped = line.strip()
            
            if stripped.startswith("interface "):
                if current_if:
                    # Link cellular interface to its profile before saving
                    if current_if.name in cellular_profile_map:
                        current_if.cellular_profile_id = cellular_profile_map[current_if.name]
                    self.interfaces.append(current_if)
                
                if_name = stripped.split("interface ", 1)[1]
                current_if = InterfaceConfig(name=if_name)
                in_interface = True
            elif in_interface and current_if:
                if stripped == "!" or (not line.startswith(" ") and line.strip()):
                    # Link cellular interface to its profile before saving
                    if current_if.name in cellular_profile_map:
                        current_if.cellular_profile_id = cellular_profile_map[current_if.name]
                    self.interfaces.append(current_if)
                    in_interface = False
                    current_if = None
                else:
                    current_if.raw_config.append(stripped)
                    self._parse_interface_line(stripped, current_if)
        
        if current_if:
            # Link cellular interface to its profile before saving
            if current_if.name in cellular_profile_map:
                current_if.cellular_profile_id = cellular_profile_map[current_if.name]
            self.interfaces.append(current_if)
    
    def _derive_interface_bandwidth(self):
        """Derive bandwidth for interfaces from tunnels and descriptions.
        
        For each interface without explicit bandwidth:
        1. Check if any tunnel interfaces source from it and have bandwidth
        2. Check the interface description for speed hints
        3. Check descriptions of tunnels that source from it
        
        Sets derived_upload_kbps, derived_download_kbps, and bandwidth_source fields.
        Also populates legacy derived_bandwidth_kbps for compatibility.
        """
        # Build lookup maps
        interface_by_name: dict[str, InterfaceConfig] = {
            iface.name: iface for iface in self.interfaces
        }
        
        # Find all tunnels and their source interfaces
        tunnels_by_source: dict[str, list[InterfaceConfig]] = {}
        for iface in self.interfaces:
            if iface.name.lower().startswith("tunnel") and iface.tunnel_source:
                source = iface.tunnel_source
                if source not in tunnels_by_source:
                    tunnels_by_source[source] = []
                tunnels_by_source[source].append(iface)
        
        # Process each interface
        for iface in self.interfaces:
            # Skip if already has explicit bandwidth
            if iface.bandwidth_kbps > 0:
                # Explicit bandwidth is treated as symmetric
                iface.upload_kbps = iface.bandwidth_kbps
                iface.download_kbps = iface.bandwidth_kbps
                iface.derived_upload_kbps = iface.bandwidth_kbps
                iface.derived_download_kbps = iface.bandwidth_kbps
                iface.derived_bandwidth_kbps = iface.bandwidth_kbps
                iface.bandwidth_source = "config"
                continue
            
            # Skip tunnel interfaces themselves
            if iface.name.lower().startswith("tunnel"):
                # For tunnels, check if they have bandwidth in config
                if iface.bandwidth_kbps > 0:
                    iface.upload_kbps = iface.bandwidth_kbps
                    iface.download_kbps = iface.bandwidth_kbps
                    iface.derived_upload_kbps = iface.bandwidth_kbps
                    iface.derived_download_kbps = iface.bandwidth_kbps
                    iface.derived_bandwidth_kbps = iface.bandwidth_kbps
                    iface.bandwidth_source = "config"
                else:
                    # Try description
                    upload, download = extract_speed_from_description(iface.description)
                    if upload > 0 or download > 0:
                        iface.derived_upload_kbps = upload
                        iface.derived_download_kbps = download
                        iface.derived_bandwidth_kbps = upload  # Legacy: use upload
                        iface.bandwidth_source = "description"
                continue
            
            # For physical interfaces, check multiple sources
            derived_upload = 0
            derived_download = 0
            source = ""
            
            # 1. Check tunnels that source from this interface
            if iface.name in tunnels_by_source:
                for tunnel in tunnels_by_source[iface.name]:
                    # Check tunnel's explicit bandwidth (symmetric)
                    if tunnel.bandwidth_kbps > 0:
                        derived_upload = tunnel.bandwidth_kbps
                        derived_download = tunnel.bandwidth_kbps
                        source = f"tunnel:{tunnel.name}"
                        break
                    # Check tunnel's description for speed hints
                    upload, download = extract_speed_from_description(tunnel.description)
                    if upload > 0 or download > 0:
                        derived_upload = upload
                        derived_download = download
                        source = f"tunnel_desc:{tunnel.name}"
                        break
            
            # 2. If still no bandwidth, check this interface's description
            if derived_upload == 0 and derived_download == 0:
                upload, download = extract_speed_from_description(iface.description)
                if upload > 0 or download > 0:
                    derived_upload = upload
                    derived_download = download
                    source = "description"
            
            # 3. Check tunnels by IP match (tunnel source might be an IP, not interface name)
            if derived_upload == 0 and derived_download == 0 and iface.ip_address:
                for other_iface in self.interfaces:
                    if (other_iface.name.lower().startswith("tunnel") and 
                        other_iface.tunnel_source == iface.ip_address):
                        if other_iface.bandwidth_kbps > 0:
                            derived_upload = other_iface.bandwidth_kbps
                            derived_download = other_iface.bandwidth_kbps
                            source = f"tunnel:{other_iface.name}"
                            break
                        upload, download = extract_speed_from_description(other_iface.description)
                        if upload > 0 or download > 0:
                            derived_upload = upload
                            derived_download = download
                            source = f"tunnel_desc:{other_iface.name}"
                            break
            
            if derived_upload > 0 or derived_download > 0:
                iface.derived_upload_kbps = derived_upload
                iface.derived_download_kbps = derived_download
                iface.derived_bandwidth_kbps = derived_upload  # Legacy: use upload
                iface.bandwidth_source = source
                logger.debug(
                    f"Interface {iface.name}: derived upload={derived_upload} download={derived_download} kbps from {source}"
                )
    
    def _parse_vlan_list(self, vlan_str: str) -> list[int]:
        """Parse a VLAN list string like '100,200,300' or '10-20,30' into a list of ints."""
        vlans = []
        for part in vlan_str.split(","):
            part = part.strip()
            if "-" in part:
                # Range like "10-20"
                try:
                    start, end = part.split("-", 1)
                    for vlan_id in range(int(start), int(end) + 1):
                        vlans.append(vlan_id)
                except ValueError:
                    pass
            else:
                # Single VLAN
                try:
                    vlans.append(int(part))
                except ValueError:
                    pass
        return vlans
    
    def _parse_interface_line(self, line: str, interface: InterfaceConfig):
        """Parse a single interface configuration line."""
        if line.startswith("description "):
            interface.description = line.split("description ", 1)[1]
        elif line.startswith("ip address "):
            parts = line.split()
            # Handle different ip address formats
            if len(parts) >= 3:
                if parts[2] == "dhcp":
                    # ip address dhcp
                    interface.ip_config_type = "dhcp"
                elif parts[2] == "negotiated":
                    # ip address negotiated (PPPoE/PPP)
                    interface.ip_config_type = "negotiated"
                    interface.negotiated = True
                elif len(parts) >= 4:
                    # ip address X.X.X.X Y.Y.Y.Y (static)
                    interface.ip_address = parts[2]
                    interface.subnet_mask = parts[3]
                    interface.ip_config_type = "static"
        elif line.startswith("ipv6 address "):
            ipv6 = line.split("ipv6 address ", 1)[1]
            interface.ipv6_addresses.append(ipv6)
        elif line.startswith("vrf forwarding "):
            interface.vrf = line.split("vrf forwarding ", 1)[1]
        elif line == "shutdown":
            interface.shutdown = True
        elif line.startswith("switchport mode "):
            interface.switchport_mode = line.split("switchport mode ", 1)[1]
        elif line.startswith("switchport access vlan "):
            interface.switchport_access_vlan = int(
                line.split("switchport access vlan ", 1)[1]
            )
        elif line.startswith("switchport trunk native vlan "):
            interface.switchport_trunk_native = int(
                line.split("switchport trunk native vlan ", 1)[1]
            )
        elif line.startswith("switchport trunk allowed vlan "):
            # Parse "switchport trunk allowed vlan 100,200,300" or "10-20,30"
            vlan_str = line.split("switchport trunk allowed vlan ", 1)[1]
            interface.switchport_trunk_allowed = self._parse_vlan_list(vlan_str)
        elif line.startswith("encapsulation dot1q "):
            # Subinterface VLAN encapsulation: encapsulation dot1q 100
            try:
                vlan_str = line.split("encapsulation dot1q ", 1)[1].split()[0]
                interface.encap_vlan_id = int(vlan_str)
            except (ValueError, IndexError):
                pass
        elif line.startswith("pppoe-client dial-pool-number "):
            # PPPoE client configuration
            interface.ip_config_type = "pppoe"
            pool_num = line.split("pppoe-client dial-pool-number ", 1)[1].split()[0]
            interface.pppoe_group = f"pool{pool_num}"
        elif line.startswith("pppoe enable group "):
            # PPPoE enable with group name
            interface.ip_config_type = "pppoe"
            interface.pppoe_group = line.split("pppoe enable group ", 1)[1]
        elif line.startswith("ip helper-address "):
            helper = line.split("ip helper-address ", 1)[1].strip()
            interface.helper_addresses.append(helper)
        elif line.startswith("zone-member security "):
            interface.security_zone = line.split("zone-member security ", 1)[1]
        elif line.startswith("ip nat "):
            interface.nat_direction = line.split("ip nat ", 1)[1]
        elif line.startswith("service-policy input "):
            interface.qos_policy_in = line.split("service-policy input ", 1)[1]
        elif line.startswith("service-policy output "):
            interface.qos_policy_out = line.split("service-policy output ", 1)[1]
        elif line.startswith("ip flow monitor ") and " input" in line:
            match = re.search(r"ip flow monitor (\S+) input", line)
            if match:
                interface.flow_monitor_in = match.group(1)
        elif line.startswith("ip flow monitor ") and " output" in line:
            match = re.search(r"ip flow monitor (\S+) output", line)
            if match:
                interface.flow_monitor_out = match.group(1)
        elif line == "no cdp enable":
            interface.cdp_enabled = False
        elif line.startswith("ip pim "):
            interface.pim_mode = line.split("ip pim ", 1)[1]
        elif line.startswith("bandwidth "):
            # bandwidth <kbps> - interface bandwidth for traffic shaping
            try:
                bw_str = line.split("bandwidth ", 1)[1].split()[0]
                interface.bandwidth_kbps = int(bw_str)
            except (ValueError, IndexError):
                pass
        elif line.startswith("tunnel source "):
            # tunnel source <interface-name or IP>
            source = line.split("tunnel source ", 1)[1].strip()
            interface.tunnel_source = source
    
    def _parse_routing(self):
        """Extract routing configurations (BGP, static routes)."""
        # Static routes
        for line in self.lines:
            stripped = line.strip()
            
            if stripped.startswith("ip route "):
                match = re.match(
                    r"ip route(?: vrf (\S+))? (\S+) (\S+) (?:(\S+) )?(\S+)"
                    r"(?: name \"([^\"]+)\")?",
                    stripped
                )
                if match:
                    route = StaticRoute(
                        vrf=match.group(1) or "",
                        destination=match.group(2),
                        mask=match.group(3),
                        interface=match.group(4) or "",
                        next_hop=match.group(5),
                        name=match.group(6) or ""
                    )
                    self.static_routes.append(route)
        
        # BGP
        in_bgp = False
        for line in self.lines:
            stripped = line.strip()
            
            if stripped.startswith("router bgp "):
                self.bgp.asn = int(stripped.split("router bgp ", 1)[1])
                in_bgp = True
            elif in_bgp:
                if stripped == "!" or (not line.startswith(" ") and line.strip()):
                    in_bgp = False
                else:
                    self.bgp.raw_config.append(stripped)
                    if stripped.startswith("bgp router-id "):
                        self.bgp.router_id = stripped.split("bgp router-id ", 1)[1]
                    elif stripped.startswith("neighbor "):
                        self.bgp.neighbors.append(stripped)
                    elif stripped.startswith("network "):
                        self.bgp.networks.append(stripped)
    
    def _parse_netflow(self):
        """Extract NetFlow configuration."""
        in_exporter = False
        current_exporter = {}
        
        in_monitor = False  
        current_monitor = {}
        
        for line in self.lines:
            stripped = line.strip()
            
            if stripped.startswith("flow exporter "):
                if current_exporter:
                    self.netflow.exporters.append(current_exporter)
                current_exporter = {"name": stripped.split("flow exporter ", 1)[1]}
                in_exporter = True
                in_monitor = False
            elif in_exporter:
                if stripped == "!" or (not line.startswith(" ") and line.strip()):
                    self.netflow.exporters.append(current_exporter)
                    in_exporter = False
                    current_exporter = {}
                else:
                    if stripped.startswith("destination "):
                        current_exporter["destination"] = stripped.split(
                            "destination ", 1
                        )[1]
                    elif stripped.startswith("transport udp "):
                        current_exporter["port"] = stripped.split(
                            "transport udp ", 1
                        )[1]
                    elif stripped.startswith("source "):
                        current_exporter["source"] = stripped.split("source ", 1)[1]
            
            elif stripped.startswith("flow monitor "):
                if current_monitor:
                    self.netflow.monitors.append(current_monitor)
                current_monitor = {"name": stripped.split("flow monitor ", 1)[1]}
                in_monitor = True
                in_exporter = False
            elif in_monitor:
                if stripped == "!" or (not line.startswith(" ") and line.strip()):
                    self.netflow.monitors.append(current_monitor)
                    in_monitor = False
                    current_monitor = {}
                else:
                    if stripped.startswith("exporter "):
                        current_monitor["exporter"] = stripped.split("exporter ", 1)[1]
                    elif stripped.startswith("record "):
                        current_monitor["record"] = stripped.split("record ", 1)[1]
        
        if current_exporter:
            self.netflow.exporters.append(current_exporter)
        if current_monitor:
            self.netflow.monitors.append(current_monitor)
    
    def _parse_logging(self):
        """Extract logging configuration."""
        for line in self.lines:
            stripped = line.strip()
            
            if stripped.startswith("logging buffered "):
                match = re.search(r"logging buffered.*?(\d+)", stripped)
                if match:
                    self.logging_config.buffer_size = int(match.group(1))
            elif stripped == "no logging console":
                self.logging_config.console_enabled = False
            elif stripped.startswith("logging host "):
                self.logging_config.hosts.append(
                    stripped.split("logging host ", 1)[1]
                )
            elif stripped.startswith("logging discriminator "):
                self.logging_config.discriminators.append(stripped)
    
    def _parse_snmp(self):
        """Extract SNMP configuration."""
        for line in self.lines:
            stripped = line.strip()
            
            if stripped.startswith("snmp-server community "):
                # Extract community string (first word after "community")
                parts = stripped.split()
                if len(parts) >= 3:
                    self.snmp.community_strings.append({
                        "community": parts[2],
                        "access": parts[3] if len(parts) > 3 else "RO"
                    })
            elif stripped.startswith("snmp-server location "):
                self.snmp.location = stripped.split("snmp-server location ", 1)[1]
            elif stripped.startswith("snmp-server contact "):
                self.snmp.contact = stripped.split("snmp-server contact ", 1)[1]
            elif stripped.startswith("snmp-server host "):
                # Extract trap host details
                parts = stripped.split()
                if len(parts) >= 3:
                    self.snmp.trap_hosts.append({
                        "host": parts[2],
                        "raw": stripped
                    })
    
    def _parse_ntp(self):
        """Extract NTP configuration."""
        for line in self.lines:
            stripped = line.strip()
            
            if stripped.startswith("ntp server "):
                # Extract NTP server (may have vrf and other options)
                parts = stripped.split()
                server_info = {"raw": stripped}
                
                # Check for VRF
                if "vrf" in parts:
                    vrf_index = parts.index("vrf")
                    if vrf_index + 1 < len(parts):
                        server_info["vrf"] = parts[vrf_index + 1]
                        # Server is after vrf name
                        if vrf_index + 2 < len(parts):
                            server_info["server"] = parts[vrf_index + 2]
                else:
                    # Server is right after "ntp server"
                    if len(parts) >= 3:
                        server_info["server"] = parts[2]
                
                if "server" in server_info:
                    self.ntp.servers.append(server_info)
            
            elif stripped.startswith("ntp source "):
                self.ntp.source_interface = stripped.split("ntp source ", 1)[1]

    def _parse_dns(self):
        """Extract DNS/name-server configuration."""
        for line in self.lines:
            stripped = line.strip()
            
            # Global DNS servers: ip name-server [vrf name] x.x.x.x [y.y.y.y ...]
            if stripped.startswith("ip name-server "):
                parts = stripped.split()
                # Skip "ip" and "name-server"
                remaining = parts[2:]
                
                # Check if VRF is specified
                if remaining and remaining[0] == "vrf" and len(remaining) >= 2:
                    # Skip "vrf" and vrf-name, rest are servers
                    remaining = remaining[2:]
                
                # All remaining parts should be DNS server IPs
                for server in remaining:
                    # Basic IP validation (contains dots)
                    if "." in server:
                        self.dns.servers.append(server)
            
            # Domain name: ip domain name example.com
            elif stripped.startswith("ip domain name ") or stripped.startswith("ip domain-name "):
                if "ip domain name " in stripped:
                    self.dns.domain_name = stripped.split("ip domain name ", 1)[1].strip()
                else:
                    self.dns.domain_name = stripped.split("ip domain-name ", 1)[1].strip()
            
            # Domain lookup disabled
            elif stripped == "no ip domain lookup" or stripped == "no ip domain-lookup":
                self.dns.domain_lookup = False

    def _parse_access_lists(self):
        """Extract access list configurations."""
        # Standard and extended numbered ACLs
        for line in self.lines:
            stripped = line.strip()
            
            if stripped.startswith("access-list "):
                parts = stripped.split()
                if len(parts) >= 2:
                    acl_num = parts[1]
                    # Find or create ACL
                    existing = next(
                        (a for a in self.access_lists if a.name == acl_num),
                        None
                    )
                    if not existing:
                        acl = AccessList(name=acl_num, acl_type="numbered")
                        acl.entries.append(stripped)
                        self.access_lists.append(acl)
                    else:
                        existing.entries.append(stripped)
    
    def to_dict(self) -> dict:
        """Convert all parsed data to a dictionary for JSON serialization."""
        return {
            "system": {
                "hostname": self.system.hostname,
                "version": self.system.version,
                "boot_image": self.system.boot_image,
                "last_config_change": self.system.last_config_change,
                "config_size_bytes": self.system.config_size_bytes,
                "services": self.system.services,
                "platform_settings": self.system.platform_settings
            },
            "vrfs": [
                {
                    "name": vrf.name,
                    "route_distinguisher": vrf.route_distinguisher,
                    "address_families": vrf.address_families,
                    "description": vrf.description
                }
                for vrf in self.vrfs
            ],
            "aaa": {
                "new_model": self.aaa.new_model,
                "server_groups": self.aaa.server_groups,
                "authentication_methods": self.aaa.authentication_methods,
                "authorization_methods": self.aaa.authorization_methods,
                "accounting_methods": self.aaa.accounting_methods
            },
            "tacacs": {
                "servers": self.tacacs.servers,
                "timeout": self.tacacs.timeout,
                "key": self.tacacs.key
            },
            "users": [
                {"username": u.username, "privilege": u.privilege}
                for u in self.users
            ],
            "local_accounts": [
                {
                    "username": a.username,
                    "privilege": a.privilege,
                    "password": a.password,
                    "password_type": a.password_type,
                    "is_decodable": a.is_decodable,
                    "mist_role": a.mist_role
                }
                for a in self.local_accounts
            ],
            "vlans": [
                {"vlan_id": v.vlan_id, "name": v.name, "state": v.state}
                for v in self.vlans
            ],
            "dhcp_pools": [
                {
                    "name": p.name,
                    "vrf": p.vrf,
                    "network": p.network,
                    "subnet_mask": p.subnet_mask,
                    "default_router": p.default_router,
                    "dns_servers": p.dns_servers,
                    "excluded_addresses": p.excluded_addresses
                }
                for p in self.dhcp_pools
            ],
            "interfaces": [
                {
                    "name": i.name,
                    "description": i.description,
                    "ip_address": i.ip_address,
                    "subnet_mask": i.subnet_mask,
                    "ip_config_type": i.ip_config_type,
                    "encap_vlan_id": i.encap_vlan_id,
                    "default_gateway": i.default_gateway,
                    "pppoe_group": i.pppoe_group,
                    "negotiated": i.negotiated,
                    "cellular_profile_id": i.cellular_profile_id,
                    "bandwidth_kbps": i.bandwidth_kbps,
                    "derived_bandwidth_kbps": i.derived_bandwidth_kbps,
                    "bandwidth_source": i.bandwidth_source,
                    "tunnel_source": i.tunnel_source,
                    "ipv6_addresses": i.ipv6_addresses,
                    "vrf": i.vrf,
                    "shutdown": i.shutdown,
                    "switchport_mode": i.switchport_mode,
                    "switchport_access_vlan": i.switchport_access_vlan,
                    "switchport_trunk_allowed": i.switchport_trunk_allowed,
                    "switchport_trunk_native": i.switchport_trunk_native,
                    "helper_addresses": i.helper_addresses,
                    "security_zone": i.security_zone,
                    "nat_direction": i.nat_direction,
                    "qos_policy_in": i.qos_policy_in,
                    "qos_policy_out": i.qos_policy_out,
                    # Classification fields
                    "interface_role": i.interface_role,
                    "port_type": i.port_type,
                    "wan_score": round(i.wan_score, 3),
                    "lan_score": round(i.lan_score, 3),
                    "classification_confidence": round(i.classification_confidence, 3),
                    "classification_indicators": i.classification_indicators,
                    "raw_config": i.raw_config
                }
                for i in self.interfaces
            ],
            "cellular_profiles": [
                {
                    "profile_id": p.profile_id,
                    "apn": p.apn,
                    "authentication": p.authentication,
                    "username": p.username,
                    "password": p.password,
                    "slot": p.slot,
                    "primary_pdp": p.primary_pdp
                }
                for p in self.cellular_profiles
            ],
            "static_routes": [
                {
                    "vrf": r.vrf,
                    "destination": r.destination,
                    "mask": r.mask,
                    "next_hop": r.next_hop,
                    "interface": r.interface,
                    "name": r.name
                }
                for r in self.static_routes
            ],
            "bgp": {
                "asn": self.bgp.asn,
                "router_id": self.bgp.router_id,
                "neighbors": self.bgp.neighbors,
                "networks": self.bgp.networks,
                "raw_config": self.bgp.raw_config
            },
            "class_maps": [
                {
                    "name": cm.name,
                    "match_type": cm.match_type,
                    "inspect_type": cm.inspect_type,
                    "match_conditions": cm.match_conditions
                }
                for cm in self.class_maps
            ],
            "policy_maps": [
                {
                    "name": pm.name,
                    "inspect_type": pm.inspect_type,
                    "raw_config": pm.raw_config
                }
                for pm in self.policy_maps
            ],
            "security_zones": [
                {"name": sz.name}
                for sz in self.security_zones
            ],
            "zone_pairs": [
                {
                    "name": zp.name,
                    "source_zone": zp.source_zone,
                    "destination_zone": zp.destination_zone,
                    "service_policy": zp.service_policy
                }
                for zp in self.zone_pairs
            ],
            "crypto": {
                "ikev2_proposals": self.crypto.ikev2_proposals,
                "ikev2_policies": self.crypto.ikev2_policies,
                "ikev2_profiles": self.crypto.ikev2_profiles,
                "ipsec_profiles": self.crypto.ipsec_profiles,
                "ipsec_transform_sets": self.crypto.ipsec_transform_sets,
                "pki_trustpoints": self.crypto.pki_trustpoints
            },
            "netflow": {
                "exporters": self.netflow.exporters,
                "monitors": self.netflow.monitors
            },
            "logging": {
                "buffer_size": self.logging_config.buffer_size,
                "console_enabled": self.logging_config.console_enabled,
                "hosts": self.logging_config.hosts,
                "discriminators": self.logging_config.discriminators
            },
            "snmp": {
                "location": self.snmp.location,
                "contact": self.snmp.contact,
                "community_strings": self.snmp.community_strings,
                "trap_hosts": self.snmp.trap_hosts
            },
            "ntp": {
                "servers": self.ntp.servers,
                "source_interface": self.ntp.source_interface
            },
            "dns": {
                "servers": self.dns.servers,
                "domain_name": self.dns.domain_name,
                "domain_lookup": self.dns.domain_lookup
            },
            "access_lists": [
                {
                    "name": al.name,
                    "type": al.acl_type,
                    "entries": al.entries
                }
                for al in self.access_lists
            ],
            "summary": {
                "total_lines": len(self.lines),
                "interface_count": len(self.interfaces),
                "vlan_count": len(self.vlans),
                "route_count": len(self.static_routes),
                "acl_count": len(self.access_lists),
                "vrf_count": len(self.vrfs),
                "class_map_count": len(self.class_maps),
                "policy_map_count": len(self.policy_maps),
                "security_zone_count": len(self.security_zones),
                "zone_pair_count": len(self.zone_pairs),
                "bgp_enabled": self.bgp.asn > 0,
                "dhcp_pool_count": len(self.dhcp_pools),
                "user_count": len(self.users)
            },
            "parse_errors": self.parse_errors
        }
