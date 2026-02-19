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
class UserAccount:
    """Local user account."""
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
    raw_config: list = field(default_factory=list)


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
        self.users: list[UserAccount] = []
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
            self._parse_users()
            self._parse_vlans()
            self._parse_dhcp()
            self._parse_class_maps()
            self._parse_policy_maps()
            self._parse_security_zones()
            self._parse_zone_pairs()
            self._parse_crypto()
            self._parse_interfaces()
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
    
    def _parse_users(self):
        """Extract local user accounts."""
        for line in self.lines:
            stripped = line.strip()
            
            if stripped.startswith("username "):
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
    
    def _parse_interfaces(self):
        """Extract interface configurations."""
        in_interface = False
        current_if = None
        
        for line in self.lines:
            stripped = line.strip()
            
            if stripped.startswith("interface "):
                if current_if:
                    self.interfaces.append(current_if)
                
                if_name = stripped.split("interface ", 1)[1]
                current_if = InterfaceConfig(name=if_name)
                in_interface = True
            elif in_interface and current_if:
                if stripped == "!" or (not line.startswith(" ") and line.strip()):
                    self.interfaces.append(current_if)
                    in_interface = False
                    current_if = None
                else:
                    current_if.raw_config.append(stripped)
                    self._parse_interface_line(stripped, current_if)
        
        if current_if:
            self.interfaces.append(current_if)
    
    def _parse_interface_line(self, line: str, interface: InterfaceConfig):
        """Parse a single interface configuration line."""
        if line.startswith("description "):
            interface.description = line.split("description ", 1)[1]
        elif line.startswith("ip address "):
            parts = line.split()
            if len(parts) >= 4:
                interface.ip_address = parts[2]
                interface.subnet_mask = parts[3]
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
            "users": [
                {"username": u.username, "privilege": u.privilege}
                for u in self.users
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
                    "ipv6_addresses": i.ipv6_addresses,
                    "vrf": i.vrf,
                    "shutdown": i.shutdown,
                    "switchport_mode": i.switchport_mode,
                    "switchport_access_vlan": i.switchport_access_vlan,
                    "security_zone": i.security_zone,
                    "nat_direction": i.nat_direction,
                    "qos_policy_in": i.qos_policy_in,
                    "qos_policy_out": i.qos_policy_out,
                    "raw_config": i.raw_config
                }
                for i in self.interfaces
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
