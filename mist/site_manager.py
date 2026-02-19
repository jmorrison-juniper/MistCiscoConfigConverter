"""
Mist Site Management.

Handles site CRUD operations in the Mist cloud.
"""

import logging

import mistapi

from parser.address_parser import ParsedAddress
from .connection import MistConnection


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
                        "host": host,
                        "port": 514,
                        "protocol": "udp",
                        "facility": "any",
                        "severity": "any",
                        "tag": ""
                    }
                    for host in syslog_servers
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
