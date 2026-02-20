"""
Mist Site Management.

Handles site CRUD operations in the Mist cloud.
"""

import ipaddress
import logging

import mistapi

from parser.address_parser import ParsedAddress
from .connection import MistConnection


def subnet_mask_to_prefix(mask: str) -> str:
    """Convert subnet mask to CIDR prefix length.
    
    Args:
        mask: Subnet mask in dotted decimal (e.g., "255.255.255.252")
              or already in prefix format (e.g., "/30" or "30")
    
    Returns:
        Prefix length as string without slash (e.g., "30").
        The slash is added in the template, not the variable.
        Returns empty string if invalid.
    """
    if not mask:
        return ""
    
    # Already in prefix format with slash - strip it
    if mask.startswith("/"):
        return mask[1:]
    
    # Just a number - return as-is
    if mask.isdigit():
        return mask
    
    try:
        # Convert dotted decimal to prefix length
        network = ipaddress.IPv4Network(f"0.0.0.0/{mask}", strict=False)
        return str(network.prefixlen)
    except (ValueError, ipaddress.AddressValueError):
        return ""


class MistSiteManager:
    """Manages Mist site operations.
    
    Provides methods for finding, creating, and updating sites
    in the Mist organization.
    
    Attributes:
        connection: MistConnection instance for API access
    """
    
    def __init__(self, connection: MistConnection):
        """Initialize site manager with connection.
        
        Args:
            connection: MistConnection instance
        """
        self.connection = connection
        self._logger = logging.getLogger(__name__)
    
    def find_by_name(self, site_name: str) -> dict | None:
        """Search for an existing site by name in the Mist org.
        
        Args:
            site_name: Name of site to find (case-insensitive)
        
        Returns:
            Site dict if found, None if not found or error.
        """
        session = self.connection.session
        if not session:
            return None
        
        try:
            response = mistapi.api.v1.orgs.sites.listOrgSites(
                session, self.connection.org_id
            )
            if response.status_code == 200:
                sites = response.data
                for site in sites:
                    if site.get("name", "").lower() == site_name.lower():
                        return site
            return None
        except Exception as error:
            self._logger.error(f"Error searching for site: {error}")
            return None
    
    def list_all(self) -> list[dict]:
        """List all sites in the Mist org.
        
        Returns:
            List of site dicts, empty list on error.
        """
        session = self.connection.session
        if not session:
            return []
        
        try:
            response = mistapi.api.v1.orgs.sites.listOrgSites(
                session, self.connection.org_id
            )
            if response.status_code == 200:
                return response.data or []
            return []
        except Exception as error:
            self._logger.error(f"Error listing sites: {error}")
            return []
    
    def create(
        self,
        site_name: str,
        parsed_address: ParsedAddress | None = None,
        gatewaytemplate_id: str | None = None
    ) -> dict | None:
        """Create a new site in the Mist org.
        
        Args:
            site_name: Name for the new site (typically device hostname)
            parsed_address: ParsedAddress object with address components
            gatewaytemplate_id: Optional gateway template ID to attach (for branch gateways)
        
        Returns:
            Created site dict if successful, None on error.
        """
        session = self.connection.session
        if not session:
            return None
        
        site_data = {
            "name": site_name
        }
        
        # Add parsed address components if available
        if parsed_address:
            mist_address = parsed_address.to_mist_format()
            site_data.update(mist_address)
        
        # Add gateway template ID if provided (for branch gateways)
        if gatewaytemplate_id:
            site_data["gatewaytemplate_id"] = gatewaytemplate_id
        
        self._logger.debug(f"Site data: {site_data}")
        
        try:
            response = mistapi.api.v1.orgs.sites.createOrgSite(
                session, self.connection.org_id, site_data
            )
            if response.status_code in [200, 201]:
                self._logger.info(
                    f"Created site: {site_name} (country={site_data.get('country_code', 'N/A')}, "
                    f"tz={site_data.get('timezone', 'N/A')}, "
                    f"gatewaytemplate={gatewaytemplate_id or 'N/A'})"
                )
                return response.data
            else:
                self._logger.error(f"Failed to create site: {response.status_code}")
                return None
        except Exception as error:
            self._logger.error(f"Error creating site: {error}")
            return None
    
    def update(
        self,
        site_id: str,
        parsed_address: ParsedAddress | None = None,
        gatewaytemplate_id: str | None = None,
        clear_gatewaytemplate: bool = False
    ) -> dict | None:
        """Update an existing site in the Mist org.
        
        Args:
            site_id: ID of the site to update
            parsed_address: ParsedAddress object with address components
            gatewaytemplate_id: Optional gateway template ID to attach (for branch gateways)
            clear_gatewaytemplate: If True, explicitly remove any existing gateway template
        
        Returns:
            Updated site dict if successful, None on error.
        """
        session = self.connection.session
        if not session:
            return None
        
        # Build update body
        site_data = {}
        
        # Add address fields if provided
        if parsed_address:
            site_data.update(parsed_address.to_mist_format())
        
        # Add gateway template ID if provided (for branch gateways)
        if gatewaytemplate_id:
            site_data["gatewaytemplate_id"] = gatewaytemplate_id
        elif clear_gatewaytemplate:
            # Explicitly clear gateway template (for standalone/hub conversion)
            site_data["gatewaytemplate_id"] = None
        
        if not site_data:
            self._logger.debug("No data to update")
            return None
        
        self._logger.debug(f"Updating site {site_id} with: {site_data}")
        
        try:
            response = mistapi.api.v1.sites.sites.updateSiteInfo(
                session, site_id, site_data
            )
            if response.status_code == 200:
                self._logger.info(
                    f"Updated site {site_id} (country={site_data.get('country_code', 'N/A')}, "
                    f"tz={site_data.get('timezone', 'N/A')}, "
                    f"gatewaytemplate={gatewaytemplate_id or 'N/A'})"
                )
                return response.data
            else:
                self._logger.error(f"Failed to update site: {response.status_code}")
                return None
        except Exception as error:
            self._logger.error(f"Error updating site: {error}")
            return None
    
    def get_site_gateways(self, site_id: str) -> list[dict]:
        """Get gateway devices assigned to a site.
        
        Args:
            site_id: Site ID to query
        
        Returns:
            List of gateway device dicts, empty list on error.
        """
        session = self.connection.session
        if not session:
            return []
        
        try:
            response = mistapi.api.v1.sites.devices.listSiteDevices(
                session, site_id, type="gateway"
            )
            if response.status_code == 200:
                data = response.data
                # Handle both list and dict responses
                if isinstance(data, list):
                    gateways = data
                else:
                    gateways = []
                self._logger.debug(
                    f"Found {len(gateways)} gateway device(s) in site {site_id}"
                )
                return gateways
            else:
                self._logger.error(
                    f"Failed to list site devices: {response.status_code}"
                )
                return []
        except Exception as error:
            self._logger.error(f"Error listing site gateways: {error}")
            return []
    
    def get_unassigned_gateways(self) -> list[dict]:
        """Get unassigned gateway devices from org inventory.
        
        Returns:
            List of unassigned gateway device dicts, empty list on error.
        """
        session = self.connection.session
        if not session:
            return []
        
        try:
            response = mistapi.api.v1.orgs.inventory.getOrgInventory(
                session, self.connection.org_id, type="gateway", unassigned=True
            )
            if response.status_code == 200:
                gateways = response.data if isinstance(response.data, list) else []
                self._logger.debug(
                    f"Found {len(gateways)} unassigned gateway device(s) in org inventory"
                )
                return gateways
            else:
                self._logger.error(
                    f"Failed to list org inventory: {response.status_code}"
                )
                return []
        except Exception as error:
            self._logger.error(f"Error listing unassigned gateways: {error}")
            return []
    
    def assign_devices_to_site(
        self, site_id: str, mac_addresses: list[str]
    ) -> dict | None:
        """Assign inventory devices to a site.
        
        Args:
            site_id: Target site ID
            mac_addresses: List of MAC addresses to assign
        
        Returns:
            Response dict with success/error lists, None on error.
        """
        session = self.connection.session
        if not session:
            return None
        
        if not mac_addresses:
            self._logger.error("No MAC addresses provided")
            return None
        
        body = {
            "op": "assign",
            "site_id": site_id,
            "macs": mac_addresses,
            "managed": True,
            "no_reassign": False
        }
        
        self._logger.debug(f"Assigning devices to site {site_id}: {mac_addresses}")
        
        try:
            response = mistapi.api.v1.orgs.inventory.updateOrgInventoryAssignment(
                session, self.connection.org_id, body
            )
            if response.status_code == 200:
                result = response.data
                success_count = len(result.get("success", []))
                error_count = len(result.get("error", []))
                self._logger.info(
                    f"Assigned devices to site: {success_count} success, {error_count} errors"
                )
                return result
            else:
                self._logger.error(
                    f"Failed to assign devices: {response.status_code}"
                )
                return None
        except Exception as error:
            self._logger.error(f"Error assigning devices to site: {error}")
            return None
    
    def unassign_devices_from_site(self, mac_addresses: list[str]) -> dict | None:
        """Unassign devices from their sites, moving them to unassigned inventory.
        
        Args:
            mac_addresses: List of MAC addresses to unassign
        
        Returns:
            Response dict with success/error lists, None on error.
        """
        session = self.connection.session
        if not session:
            return None
        
        if not mac_addresses:
            self._logger.error("No MAC addresses provided for unassign")
            return None
        
        body = {
            "op": "unassign",
            "macs": mac_addresses
        }
        
        self._logger.debug(f"Unassigning devices from sites: {mac_addresses}")
        
        try:
            response = mistapi.api.v1.orgs.inventory.updateOrgInventoryAssignment(
                session, self.connection.org_id, body
            )
            if response.status_code == 200:
                result = response.data
                success_count = len(result.get("success", []))
                error_count = len(result.get("error", []))
                self._logger.info(
                    f"Unassigned devices: {success_count} success, {error_count} errors"
                )
                return result
            else:
                self._logger.error(
                    f"Failed to unassign devices: {response.status_code}"
                )
                return None
        except Exception as error:
            self._logger.error(f"Error unassigning devices: {error}")
            return None
    
    def update_device_name(
        self, site_id: str, device_id: str, name: str
    ) -> bool:
        """Update a device's name.
        
        Args:
            site_id: Site ID where device is assigned
            device_id: Device UUID
            name: New device name
        
        Returns:
            True on success, False on error.
        """
        session = self.connection.session
        if not session:
            return False
        
        body = {"name": name}
        
        self._logger.debug(f"Updating device {device_id} name to '{name}'")
        
        try:
            response = mistapi.api.v1.sites.devices.updateSiteDevice(
                session, site_id, device_id, body
            )
            if response.status_code == 200:
                self._logger.info(f"Device {device_id} renamed to '{name}'")
                return True
            else:
                self._logger.error(
                    f"Failed to update device name: {response.status_code}"
                )
                return False
        except Exception as error:
            self._logger.error(f"Error updating device name: {error}")
            return False
    
    def apply_device_config_overrides(
        self,
        site_id: str,
        device_id: str,
        ntp_servers: list[str] | None = None,
        dns_servers: list[str] | None = None,
        syslog_servers: list[str] | None = None
    ) -> bool:
        """Apply local device-level config overrides for NTP/DNS/Syslog.
        
        These override the template/profile settings at the device level.
        
        Args:
            site_id: Site ID where device is assigned
            device_id: Device UUID
            ntp_servers: List of NTP server IPs (optional)
            dns_servers: List of DNS server IPs (optional)
            syslog_servers: List of syslog host IPs (optional)
        
        Returns:
            True on success, False on error.
        """
        session = self.connection.session
        if not session:
            return False
        
        body: dict = {}
        
        if ntp_servers is not None and len(ntp_servers) > 0:
            body["ntp_servers"] = ntp_servers
        
        if dns_servers is not None and len(dns_servers) > 0:
            body["dns_servers"] = dns_servers
        
        if syslog_servers is not None and len(syslog_servers) > 0:
            # Syslog uses remote_syslog object structure
            body["remote_syslog"] = {
                "enabled": True,
                "servers": [
                    {
                        "name": f"syslog{index}",
                        "host": host,
                        "port": 514,
                        "protocol": "udp",
                        "facility": "any",
                        "severity": "any",
                        "tag": ""
                    }
                    for index, host in enumerate(syslog_servers, 1)
                ]
            }
        
        if not body:
            self._logger.debug(f"No config overrides to apply for device {device_id}")
            return True
        
        override_types = []
        if "ntp_servers" in body:
            override_types.append("NTP")
        if "dns_servers" in body:
            override_types.append("DNS")
        if "remote_syslog" in body:
            override_types.append("Syslog")
        
        self._logger.info(
            f"Applying device config overrides ({', '.join(override_types)}) "
            f"to device {device_id}"
        )
        
        try:
            response = mistapi.api.v1.sites.devices.updateSiteDevice(
                session, site_id, device_id, body
            )
            if response.status_code == 200:
                self._logger.info(
                    f"Device {device_id} config overrides applied successfully"
                )
                return True
            else:
                self._logger.error(
                    f"Failed to apply device config overrides: {response.status_code}"
                )
                return False
        except Exception as error:
            self._logger.error(f"Error applying device config overrides: {error}")
            return False
    
    def apply_config_overrides_to_devices(
        self,
        site_id: str,
        mac_addresses: list[str],
        ntp_servers: list[str] | None = None,
        dns_servers: list[str] | None = None,
        syslog_servers: list[str] | None = None
    ) -> dict:
        """Apply local config overrides to multiple devices by MAC address.
        
        Args:
            site_id: Site ID where devices are assigned
            mac_addresses: List of device MAC addresses
            ntp_servers: List of NTP server IPs (optional)
            dns_servers: List of DNS server IPs (optional)
            syslog_servers: List of syslog host IPs (optional)
        
        Returns:
            Dict with 'success' (list of MACs) and 'error' (list of failed MACs).
        """
        result = {"success": [], "error": []}
        
        # Get site devices to find device IDs from MACs
        site_gateways = self.get_site_gateways(site_id)
        
        # Build MAC to device_id mapping
        mac_to_device: dict[str, dict] = {}
        for gateway in site_gateways:
            mac = gateway.get("mac", "").lower().replace(":", "")
            if mac:
                mac_to_device[mac] = gateway
        
        # Apply overrides to each device
        for mac in mac_addresses:
            normalized_mac = mac.lower().replace(":", "")
            device = mac_to_device.get(normalized_mac)
            
            if not device:
                self._logger.warning(
                    f"Device with MAC {mac} not found at site {site_id}"
                )
                result["error"].append(mac)
                continue
            
            device_id = device.get("id")
            if not device_id:
                self._logger.warning(f"Device {mac} has no ID")
                result["error"].append(mac)
                continue
            
            # Apply the config overrides
            if self.apply_device_config_overrides(
                site_id, device_id,
                ntp_servers=ntp_servers,
                dns_servers=dns_servers,
                syslog_servers=syslog_servers
            ):
                result["success"].append(mac)
            else:
                result["error"].append(mac)
        
        return result
    
    def name_devices_from_inventory(
        self,
        site_id: str,
        mac_addresses: list[str],
        base_name: str,
        is_ha_pair: bool = False
    ) -> dict:
        """Name devices that were assigned from unassigned inventory.
        
        For single devices, uses the base_name directly.
        For HA pairs, appends -R1 and -R2 suffixes.
        
        Args:
            site_id: Site ID where devices are assigned
            mac_addresses: List of device MAC addresses
            base_name: Base device name (from Cisco hostname)
            is_ha_pair: If True, append -R1/-R2 suffixes
        
        Returns:
            Dict with 'success' (list of renamed MACs) and 'error' (list of failed MACs).
        """
        result = {"success": [], "error": []}
        
        # Get site devices to find device IDs from MACs
        site_gateways = self.get_site_gateways(site_id)
        
        # Build MAC to device_id mapping
        mac_to_device: dict[str, dict] = {}
        for gateway in site_gateways:
            mac = gateway.get("mac", "").lower().replace(":", "")
            if mac:
                mac_to_device[mac] = gateway
        
        # Name each device
        for index, mac in enumerate(mac_addresses):
            normalized_mac = mac.lower().replace(":", "")
            device = mac_to_device.get(normalized_mac)
            
            if not device:
                self._logger.warning(
                    f"Device with MAC {mac} not found at site {site_id}"
                )
                result["error"].append(mac)
                continue
            
            device_id = device.get("id")
            if not device_id:
                self._logger.warning(f"Device {mac} has no ID")
                result["error"].append(mac)
                continue
            
            # Determine device name
            if is_ha_pair:
                # R1 for first device, R2 for second
                suffix = f"-R{index + 1}"
                device_name = f"{base_name}{suffix}"
            else:
                device_name = base_name
            
            # Update the device name
            if self.update_device_name(site_id, device_id, device_name):
                result["success"].append(mac)
            else:
                result["error"].append(mac)
        
        return result

    def update_site_variables(
        self,
        site_id: str,
        ntp_servers: list[str] | None = None,
        dns_servers: list[str] | None = None,
        syslog_servers: list[str] | None = None,
        dns_suffix: list[str] | None = None,
        wan_interfaces: list[dict] | None = None
    ) -> bool:
        """Update site variables for NTP, DNS, Syslog, and WAN interfaces.
        
        Sets site-level variables that can be referenced in gateway templates
        using {{variable}} syntax. Variables are named ntp1, ntp2, dns1, dns2,
        syslog1, wan1, wan2, wan3_lte, etc.
        
        For WAN interfaces, creates detailed variables:
        - wan1 = interface name (e.g., "GigabitEthernet0/0/0")
        - wan1_ip = IP address (e.g., "10.1.1.1")
        - wan1_subnet = subnet mask (e.g., "255.255.255.0")
        - wan1_gateway = default gateway (e.g., "10.1.1.254")
        - wan1_vlan = VLAN ID if applicable (e.g., "100")
        - wan1_type = IP config type (static, dhcp, pppoe, negotiated)
        - wan1_upload_kbps = Upload bandwidth in kbps (only if found)
        - wan1_download_kbps = Download bandwidth in kbps (only if found)
        
        NOTE: Bandwidth variables are only created when values are extracted from
        the Cisco config. If not found, the template's traffic_shaping config
        won't resolve/activate for that port.
        
        For LTE WAN interfaces (wan_type == "lte"), also creates:
        - wan3_lte_apn = APN name (e.g., "internet.carrier.com")
        - wan3_lte_auth = authentication type (none, chap, pap)
        - wan3_lte_user = APN username
        - wan3_lte_pass = APN password
        
        Also sets dns_suffix as a direct site setting (not a variable).
        
        Args:
            site_id: Site ID to update
            ntp_servers: List of NTP server addresses
            dns_servers: List of DNS server addresses
            syslog_servers: List of syslog server addresses
            dns_suffix: List of DNS domain suffixes (e.g., ["example.com"])
            wan_interfaces: List of WAN interface dicts with detailed fields
        
        Returns:
            True on success, False on error.
        """
        session = self.connection.session
        if not session:
            return False
        
        # Build vars dictionary
        site_vars: dict[str, str] = {}
        
        if ntp_servers:
            for index, server in enumerate(ntp_servers[:4], 1):  # Max 4 NTP servers
                site_vars[f"ntp{index}"] = server
        
        if dns_servers:
            for index, server in enumerate(dns_servers[:3], 1):  # Max 3 DNS servers
                site_vars[f"dns{index}"] = server
        
        if syslog_servers:
            for index, server in enumerate(syslog_servers[:2], 1):  # Max 2 syslog servers
                site_vars[f"syslog{index}"] = server
        
        if wan_interfaces:
            # Create detailed variables for each WAN interface
            for interface in wan_interfaces:
                var_name = interface.get("wan_var_name", "")
                if not var_name:
                    continue
                
                # Base variable: interface name
                interface_name = interface.get("name", "")
                if interface_name:
                    site_vars[var_name] = interface_name
                
                # Vanity name variable (e.g., "WAN 1", "LTE 1")
                wan_type = interface.get("wan_type", "broadband")
                var_name_clean = var_name.replace("_lte", "")
                port_number = "".join(c for c in var_name_clean if c.isdigit()) or "1"
                if wan_type == "lte":
                    vanity_name = f"LTE {port_number}"
                else:
                    vanity_name = f"WAN {port_number}"
                site_vars[f"{var_name}_name"] = vanity_name
                
                # Description variable (Cisco description or interface name)
                cisco_description = interface.get("description", "")
                site_vars[f"{var_name}_desc"] = cisco_description if cisco_description else interface_name
                
                # IP address variable
                ip_address = interface.get("ip_address", "")
                if ip_address:
                    site_vars[f"{var_name}_ip"] = ip_address
                
                # Subnet mask variable - convert to CIDR prefix format for Mist API
                subnet_mask = interface.get("subnet_mask", "")
                if subnet_mask:
                    prefix = subnet_mask_to_prefix(subnet_mask)
                    if prefix:
                        site_vars[f"{var_name}_subnet"] = prefix
                
                # Default gateway variable
                default_gateway = interface.get("default_gateway", "")
                if default_gateway:
                    site_vars[f"{var_name}_gateway"] = default_gateway
                
                # VLAN ID variable (only if > 0)
                vlan_id = interface.get("encap_vlan_id", 0)
                if vlan_id and vlan_id > 0:
                    site_vars[f"{var_name}_vlan"] = str(vlan_id)
                
                # IP config type variable (static, dhcp, pppoe, negotiated)
                ip_config_type = interface.get("ip_config_type", "")
                if ip_config_type:
                    site_vars[f"{var_name}_type"] = ip_config_type
                
                # LTE/Cellular-specific variables (only for LTE interfaces)
                if wan_type == "lte":
                    # APN name
                    lte_apn = interface.get("lte_apn", "")
                    if lte_apn:
                        site_vars[f"{var_name}_apn"] = lte_apn
                    
                    # Authentication type (none, chap, pap)
                    lte_auth = interface.get("lte_auth", "")
                    if lte_auth:
                        site_vars[f"{var_name}_auth"] = lte_auth
                    
                    # APN username
                    lte_username = interface.get("lte_username", "")
                    if lte_username:
                        site_vars[f"{var_name}_user"] = lte_username
                    
                    # APN password
                    lte_password = interface.get("lte_password", "")
                    if lte_password:
                        site_vars[f"{var_name}_pass"] = lte_password
                
                # Upload/download bandwidth variables (only create if values exist)
                # If not created, the template's traffic_shaping config won't resolve/activate
                upload_kbps = interface.get("upload_kbps", 0)
                if upload_kbps and upload_kbps > 0:
                    site_vars[f"{var_name}_upload_kbps"] = str(upload_kbps)
                
                download_kbps = interface.get("download_kbps", 0)
                if download_kbps and download_kbps > 0:
                    site_vars[f"{var_name}_download_kbps"] = str(download_kbps)
        
        if not site_vars and not dns_suffix:
            self._logger.debug(f"No site variables to update for site {site_id}")
            return True
        
        body: dict = {}
        if site_vars:
            body["vars"] = site_vars
        
        # dns_suffix is a direct site setting, not a variable
        if dns_suffix:
            body["dns_suffix"] = dns_suffix
        
        log_items = []
        if site_vars:
            log_items.append(f"vars: {list(site_vars.keys())}")
        if dns_suffix:
            log_items.append(f"dns_suffix: {dns_suffix}")
        self._logger.info(
            f"Updating site {site_id} settings: {', '.join(log_items)}"
        )
        
        try:
            response = mistapi.api.v1.sites.setting.updateSiteSettings(
                session, site_id, body
            )
            if response.status_code == 200:
                self._logger.info(
                    f"Site {site_id} variables updated: {site_vars}"
                )
                return True
            else:
                self._logger.error(
                    f"Failed to update site variables: {response.status_code}"
                )
                return False
        except Exception as error:
            self._logger.error(f"Error updating site variables: {error}")
            return False

    def update_site_syslog(
        self,
        site_id: str,
        syslog_servers: list[str]
    ) -> bool:
        """Update site remote_syslog settings.
        
        Configures remote syslog servers at the site level. This applies to
        gateways at this site (syslog is not configurable in gateway templates).
        
        Args:
            site_id: Site ID to update
            syslog_servers: List of syslog server hostnames/IPs
        
        Returns:
            True on success, False on error.
        """
        session = self.connection.session
        if not session:
            return False
        
        if not syslog_servers:
            self._logger.debug(f"No syslog servers to configure for site {site_id}")
            return True
        
        # Build remote_syslog configuration
        servers = [
            {
                "host": host,
                "port": 514,
                "protocol": "udp",
                "facility": "any",
                "severity": "any",
                "tag": ""
            }
            for host in syslog_servers
        ]
        
        body = {
            "remote_syslog": {
                "enabled": True,
                "send_to_all_servers": False,
                "servers": servers
            }
        }
        
        self._logger.info(
            f"Updating site {site_id} syslog: {len(syslog_servers)} server(s)"
        )
        
        try:
            response = mistapi.api.v1.sites.setting.updateSiteSettings(
                session, site_id, body
            )
            if response.status_code == 200:
                self._logger.info(
                    f"Site {site_id} syslog configured: {syslog_servers}"
                )
                return True
            else:
                self._logger.error(
                    f"Failed to update site syslog: {response.status_code}"
                )
                return False
        except Exception as error:
            self._logger.error(f"Error updating site syslog: {error}")
            return False

    def update_site_snmp(
        self,
        site_id: str,
        community_strings: list[dict] | None = None,
        contact: str | None = None,
        location: str | None = None,
        trap_hosts: list[dict] | None = None
    ) -> bool:
        """Update site SNMP configuration.
        
        Configures SNMP settings at the site level for switches and other devices.
        
        Args:
            site_id: Site ID to update
            community_strings: List of {community, access} dicts
            contact: SNMP contact string
            location: SNMP location string
            trap_hosts: List of {host, community} dicts for trap destinations
        
        Returns:
            True on success, False on error.
        """
        session = self.connection.session
        if not session:
            return False
        
        # Build snmp_config
        snmp_config: dict = {"enabled": True}
        
        if contact:
            snmp_config["contact"] = contact
        
        if location:
            snmp_config["location"] = location
        
        # Configure v2c communities
        if community_strings:
            v2c_config = []
            for cs in community_strings:
                v2c_config.append({
                    "community_name": cs.get("community", ""),
                    "authorization": "read-only" if cs.get("access", "RO") == "RO" else "read-write"
                })
            if v2c_config:
                snmp_config["v2c_config"] = v2c_config
        
        # Configure trap groups
        if trap_hosts:
            trap_groups = []
            for trap in trap_hosts:
                trap_groups.append({
                    "group_name": "default",
                    "targets": [trap.get("host", "")],
                    "version": "v2"
                })
            if trap_groups:
                snmp_config["trap_groups"] = trap_groups
        
        if len(snmp_config) <= 1:  # Only "enabled" key
            self._logger.debug(f"No SNMP config to set for site {site_id}")
            return True
        
        body = {"snmp_config": snmp_config}
        
        self._logger.info(f"Updating site {site_id} SNMP configuration")
        
        try:
            response = mistapi.api.v1.sites.setting.updateSiteSettings(
                session, site_id, body
            )
            if response.status_code == 200:
                self._logger.info(f"Site {site_id} SNMP configured")
                return True
            else:
                self._logger.error(
                    f"Failed to update site SNMP: {response.status_code}"
                )
                return False
        except Exception as error:
            self._logger.error(f"Error updating site SNMP: {error}")
            return False

    def update_site_tacacs(
        self,
        site_id: str,
        tacacs_servers: list[dict],
        global_key: str = "",
        global_timeout: int = 5
    ) -> bool:
        """Update site TACACS+ configuration for switch management.
        
        Configures TACACS+ authentication for switch CLI access.
        
        Args:
            site_id: Site ID to update
            tacacs_servers: List of {host, port, key, timeout} dicts
            global_key: Global shared secret (used if server doesn't have its own)
            global_timeout: Global timeout in seconds
        
        Returns:
            True on success, False on error.
        """
        session = self.connection.session
        if not session:
            return False
        
        if not tacacs_servers:
            self._logger.debug(f"No TACACS servers to configure for site {site_id}")
            return True
        
        # Build tacplus_servers list
        tacplus_servers = []
        for server in tacacs_servers:
            tacplus_servers.append({
                "host": server.get("host", ""),
                "port": str(server.get("port", 49)),
                "secret": server.get("key", "") or global_key,
                "timeout": server.get("timeout", global_timeout)
            })
        
        # TACACS is under switch_mgmt.tacacs in site settings
        body = {
            "switch_mgmt": {
                "tacacs": {
                    "enabled": True,
                    "tacplus_servers": tacplus_servers
                }
            }
        }
        
        self._logger.info(
            f"Updating site {site_id} TACACS: {len(tacacs_servers)} server(s)"
        )
        
        try:
            response = mistapi.api.v1.sites.setting.updateSiteSettings(
                session, site_id, body
            )
            if response.status_code == 200:
                self._logger.info(
                    f"Site {site_id} TACACS configured: "
                    f"{[s.get('host') for s in tacacs_servers]}"
                )
                return True
            else:
                self._logger.error(
                    f"Failed to update site TACACS: {response.status_code}"
                )
                return False
        except Exception as error:
            self._logger.error(f"Error updating site TACACS: {error}")
            return False

    def update_site_local_accounts(
        self,
        site_id: str,
        local_accounts: list[dict],
        root_password: str = ""
    ) -> bool:
        """Update site local user accounts for switch management.
        
        Configures local user authentication for switch CLI access.
        Only accounts with decodable passwords (Type 0 or 7) will be set.
        
        Args:
            site_id: Site ID to update
            local_accounts: List of account dicts with:
                - username: Account username
                - password: Decoded password (empty if not decodable)
                - mist_role: admin, helpdesk, read, or none
                - is_decodable: True if password was successfully decoded
            root_password: Optional root password to set
        
        Returns:
            True on success, False on error.
        """
        session = self.connection.session
        if not session:
            return False
        
        # Filter to only accounts with decodable passwords
        decodable_accounts = [
            a for a in local_accounts 
            if a.get("is_decodable") and a.get("password")
        ]
        
        if not decodable_accounts and not root_password:
            self._logger.debug(
                f"No decodable local accounts to configure for site {site_id}"
            )
            return True
        
        # Build local_accounts dict (keyed by username)
        accounts_dict = {}
        for account in decodable_accounts:
            username = account.get("username", "")
            if username:
                accounts_dict[username] = {
                    "password": account.get("password", ""),
                    "role": account.get("mist_role", "read")
                }
        
        # Build site settings body
        switch_mgmt = {}
        if accounts_dict:
            switch_mgmt["local_accounts"] = accounts_dict
        if root_password:
            switch_mgmt["root_password"] = root_password
        
        if not switch_mgmt:
            return True
        
        body = {"switch_mgmt": switch_mgmt}
        
        self._logger.info(
            f"Updating site {site_id} local accounts: {list(accounts_dict.keys())}"
        )
        
        try:
            response = mistapi.api.v1.sites.setting.updateSiteSettings(
                session, site_id, body
            )
            if response.status_code == 200:
                self._logger.info(
                    f"Site {site_id} local accounts configured: "
                    f"{len(accounts_dict)} account(s)"
                )
                return True
            else:
                self._logger.error(
                    f"Failed to update site local accounts: {response.status_code}"
                )
                return False
        except Exception as error:
            self._logger.error(f"Error updating site local accounts: {error}")
            return False
