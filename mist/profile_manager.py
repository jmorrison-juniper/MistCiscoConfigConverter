"""
Mist Device Profile Management.

Handles device profile operations for hub gateways.
"""

import logging
import re
from typing import Any

import mistapi

from .connection import MistConnection


def sanitize_hub_profile_name(device_name: str) -> str:
    """Sanitize device name for use as hub profile name.
    
    Hub Profile names must be 2-32 characters and can only contain
    alphanumerics and underscores.
    
    Args:
        device_name: Raw device hostname
    
    Returns:
        Sanitized profile name (e.g., 'RTR-01' becomes 'RTR_01_Hub_Template')
    """
    # Replace non-alphanumeric characters with underscores
    sanitized = re.sub(r'[^a-zA-Z0-9]', '_', device_name)
    # Remove consecutive underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    # Remove leading/trailing underscores
    sanitized = sanitized.strip('_')
    # Build profile name
    profile_name = f"{sanitized}_Hub_Template"
    # Truncate to 32 characters if needed
    if len(profile_name) > 32:
        # Keep as much of the device name as possible
        max_prefix = 32 - len("_Hub_Template")
        profile_name = f"{sanitized[:max_prefix]}_Hub_Template"
    # Ensure minimum 2 characters
    if len(profile_name) < 2:
        profile_name = "Hub_Template"
    return profile_name


class MistProfileManager:
    """Manages Mist device profile operations for hub gateways.
    
    Device profiles (type=gateway) are used for hub gateways,
    which are device-level configurations rather than site-level.
    
    Attributes:
        connection: MistConnection instance for API access
    """
    
    def __init__(self, connection: MistConnection):
        """Initialize profile manager with connection.
        
        Args:
            connection: MistConnection instance
        """
        self.connection = connection
        self._logger = logging.getLogger(__name__)
    
    def find_by_name(self, profile_name: str) -> dict | None:
        """Find a gateway device profile (hub profile) by name.
        
        Args:
            profile_name: Name of the device profile to find (case-insensitive)
        
        Returns:
            Profile dict if found, None otherwise.
        """
        session = self.connection.session
        if not session:
            return None
        
        try:
            response = mistapi.api.v1.orgs.deviceprofiles.listOrgDeviceProfiles(
                session, self.connection.org_id, type="gateway"
            )
            if response.status_code == 200:
                profiles = response.data
                for profile in profiles:
                    if profile.get("name", "").lower() == profile_name.lower():
                        self._logger.debug(
                            f"Found device profile: {profile_name} (ID: {profile.get('id')})"
                        )
                        return profile
            return None
        except Exception as error:
            self._logger.error(f"Error searching for device profile: {error}")
            return None
    
    def create(
        self, 
        profile_name: str,
        ntp_servers: list[str] | None = None,
        dns_servers: list[str] | None = None,
        syslog_servers: list[str] | None = None
    ) -> dict | None:
        """Create a new gateway device profile (hub profile).
        
        Args:
            profile_name: Name for the new device profile
                          Must be 2-32 chars, alphanumerics and underscores only
            ntp_servers: List of NTP server addresses
            dns_servers: List of DNS server addresses  
            syslog_servers: List of syslog server addresses
        
        Returns:
            Created profile dict if successful, None on error.
        """
        session = self.connection.session
        if not session:
            return None
        
        profile_data: dict[str, Any] = {
            "name": profile_name,
            "type": "gateway"
        }
        
        # Add NTP servers if provided
        if ntp_servers:
            profile_data["ntp_servers"] = ntp_servers
        
        # Add DNS servers if provided
        if dns_servers:
            profile_data["dns_servers"] = dns_servers
        
        # Add remote syslog if provided
        if syslog_servers:
            profile_data["remote_syslog"] = {
                "enabled": True,
                "servers": [
                    {
                        "host": server,
                        "port": 514,
                        "protocol": "udp",
                        "facility": "any",
                        "severity": "info"
                    }
                    for server in syslog_servers
                ]
            }
        
        try:
            response = mistapi.api.v1.orgs.deviceprofiles.createOrgDeviceProfile(
                session, self.connection.org_id, profile_data
            )
            if response.status_code in [200, 201]:
                self._logger.info(
                    f"Created device profile (hub): {profile_name}"
                )
                return response.data
            else:
                self._logger.error(
                    f"Failed to create device profile: {response.status_code}"
                )
                return None
        except Exception as error:
            self._logger.error(f"Error creating device profile: {error}")
            return None
    
    def update(
        self,
        profile_id: str,
        ntp_servers: list[str] | None = None,
        dns_servers: list[str] | None = None,
        syslog_servers: list[str] | None = None
    ) -> dict | None:
        """Update an existing device profile with NTP/DNS/syslog values.
        
        Only updates fields that are provided (not None).
        
        Args:
            profile_id: ID of the profile to update
            ntp_servers: List of NTP server IPs
            dns_servers: List of DNS server IPs
            syslog_servers: List of syslog host IPs
        
        Returns:
            Updated profile dict on success, None on error.
        """
        session = self.connection.session
        if not session:
            return None
        
        update_data: dict[str, Any] = {}
        
        if ntp_servers:
            update_data["ntp_servers"] = ntp_servers
        
        if dns_servers:
            update_data["dns_servers"] = dns_servers
        
        if syslog_servers:
            update_data["remote_syslog"] = {
                "enabled": True,
                "servers": [
                    {
                        "host": server,
                        "port": 514,
                        "protocol": "udp",
                        "facility": "any",
                        "severity": "info"
                    }
                    for server in syslog_servers
                ]
            }
        
        if not update_data:
            self._logger.debug("No updates to apply to profile")
            return None
        
        update_types = []
        if "ntp_servers" in update_data:
            update_types.append("NTP")
        if "dns_servers" in update_data:
            update_types.append("DNS")
        if "remote_syslog" in update_data:
            update_types.append("Syslog")
        
        self._logger.info(
            f"Updating device profile {profile_id} with {', '.join(update_types)}"
        )
        
        try:
            response = mistapi.api.v1.orgs.deviceprofiles.updateOrgDeviceProfile(
                session, self.connection.org_id, profile_id, update_data
            )
            if response.status_code == 200:
                self._logger.info(f"Profile {profile_id} updated successfully")
                return response.data
            else:
                self._logger.error(
                    f"Failed to update device profile: {response.status_code}"
                )
                return None
        except Exception as error:
            self._logger.error(f"Error updating device profile: {error}")
            return None
    
    def compare_with_cisco_config(
        self,
        profile: dict,
        cisco_ntp_servers: list[str],
        cisco_dns_servers: list[str],
        cisco_syslog_hosts: list[str]
    ) -> dict:
        """Compare profile config with Cisco parsed config.
        
        Args:
            profile: Device profile dict from Mist
            cisco_ntp_servers: NTP servers from Cisco config
            cisco_dns_servers: DNS servers from Cisco config  
            cisco_syslog_hosts: Syslog hosts from Cisco config
        
        Returns:
            Dict with comparison results for each category.
        """
        # Extract profile values
        profile_ntp = profile.get("ntp_servers", [])
        profile_dns = profile.get("dns_servers", [])
        
        # Extract syslog hosts from remote_syslog.servers
        remote_syslog = profile.get("remote_syslog", {})
        profile_syslog = []
        if remote_syslog.get("enabled") and remote_syslog.get("servers"):
            profile_syslog = [s.get("host", "") for s in remote_syslog["servers"]]
        
        # Normalize Cisco NTP servers - may be strings or dicts with "server" key
        cisco_ntp_normalized = []
        for server in cisco_ntp_servers:
            if isinstance(server, dict):
                cisco_ntp_normalized.append(server.get("server", ""))
            else:
                cisco_ntp_normalized.append(str(server))
        
        # Normalize Cisco DNS servers - may be strings or dicts
        cisco_dns_normalized = []
        for server in cisco_dns_servers:
            if isinstance(server, dict):
                cisco_dns_normalized.append(server.get("server", ""))
            else:
                cisco_dns_normalized.append(str(server))
        
        # Normalize Cisco syslog hosts - may be strings or dicts with "host" key
        cisco_syslog_normalized = []
        for host in cisco_syslog_hosts:
            if isinstance(host, dict):
                cisco_syslog_normalized.append(host.get("host", ""))
            else:
                cisco_syslog_normalized.append(str(host))
        
        # Compare (order-independent)
        ntp_match = set(profile_ntp) == set(cisco_ntp_normalized)
        dns_match = set(profile_dns) == set(cisco_dns_normalized)
        syslog_match = set(profile_syslog) == set(cisco_syslog_normalized)
        
        # Check if profile is empty but Cisco has values (will be auto-updated)
        ntp_will_update = not profile_ntp and cisco_ntp_normalized
        dns_will_update = not profile_dns and cisco_dns_normalized
        syslog_will_update = not profile_syslog and cisco_syslog_normalized
        
        return {
            "ntp": {
                "match": ntp_match,
                "template": profile_ntp,
                "cisco": cisco_ntp_normalized,
                "will_update_template": ntp_will_update
            },
            "dns": {
                "match": dns_match,
                "template": profile_dns,
                "cisco": cisco_dns_normalized,
                "will_update_template": dns_will_update
            },
            "syslog": {
                "match": syslog_match,
                "template": profile_syslog,
                "cisco": cisco_syslog_normalized,
                "will_update_template": syslog_will_update
            }
        }
    
    def get_or_create_hub_profile(
        self, 
        device_name: str,
        ntp_servers: list[str] | None = None,
        dns_servers: list[str] | None = None,
        syslog_servers: list[str] | None = None
    ) -> tuple[dict | None, bool]:
        """Get or create a hub device profile for the given device.
        
        Generates a sanitized profile name from the device hostname:
        e.g., 'RTR-01' becomes 'RTR_01_Hub_Template'
        
        Args:
            device_name: Device hostname to use as profile prefix
            ntp_servers: NTP servers for new profile creation
            dns_servers: DNS servers for new profile creation
            syslog_servers: Syslog servers for new profile creation
        
        Returns:
            Tuple of (profile_dict, was_created):
            - profile_dict: Profile dict if found/created, None on error
            - was_created: True if profile was newly created, False if existing
        """
        profile_name = sanitize_hub_profile_name(device_name)
        
        profile = self.find_by_name(profile_name)
        if profile:
            self._logger.debug(
                f"Using existing device profile: {profile_name}"
            )
            return (profile, False)
        
        self._logger.info(
            f"Device profile '{profile_name}' not found, creating..."
        )
        created_profile = self.create(
            profile_name,
            ntp_servers=ntp_servers,
            dns_servers=dns_servers,
            syslog_servers=syslog_servers
        )
        return (created_profile, created_profile is not None)
    
    @staticmethod
    def sanitize_name(device_name: str) -> str:
        """Sanitize device name for hub profile naming.
        
        Convenience method to expose the sanitization logic.
        
        Args:
            device_name: Raw device hostname
        
        Returns:
            Sanitized profile name
        """
        return sanitize_hub_profile_name(device_name)
    
    def assign_devices(self, profile_id: str, mac_addresses: list[str]) -> dict | None:
        """Assign gateway devices to a device profile.
        
        Uses POST /api/v1/orgs/{org_id}/deviceprofiles/{deviceprofile_id}/assign
        
        Args:
            profile_id: Device profile ID to assign devices to
            mac_addresses: List of device MAC addresses (format: 12 hex chars, no colons)
        
        Returns:
            Dict with 'success' list of assigned MACs, or None on error.
        """
        if not mac_addresses:
            self._logger.debug("No MAC addresses provided for assignment")
            return {"success": []}
        
        session = self.connection.session
        if not session:
            return None
        
        # Normalize MAC addresses (remove colons, lowercase)
        normalized_macs = [mac.replace(":", "").replace("-", "").lower() for mac in mac_addresses]
        
        assign_data = {"macs": normalized_macs}
        
        try:
            response = mistapi.api.v1.orgs.deviceprofiles.assignOrgDeviceProfile(
                session, self.connection.org_id, profile_id, assign_data
            )
            if response.status_code == 200:
                result = response.data
                self._logger.info(
                    f"Assigned {len(result.get('success', []))} device(s) to profile {profile_id}"
                )
                return result
            else:
                self._logger.error(
                    f"Failed to assign devices to profile: {response.status_code}"
                )
                return None
        except Exception as error:
            self._logger.error(f"Error assigning devices to profile: {error}")
            return None
    
    def unassign_devices(self, profile_id: str, mac_addresses: list[str]) -> dict | None:
        """Unassign gateway devices from a device profile.
        
        Uses POST /api/v1/orgs/{org_id}/deviceprofiles/{deviceprofile_id}/unassign
        
        Args:
            profile_id: Device profile ID to unassign devices from
            mac_addresses: List of device MAC addresses (format: 12 hex chars, no colons)
        
        Returns:
            Dict with 'success' list of unassigned MACs, or None on error.
        """
        if not mac_addresses:
            self._logger.debug("No MAC addresses provided for unassignment")
            return {"success": []}
        
        session = self.connection.session
        if not session:
            return None
        
        # Normalize MAC addresses (remove colons, lowercase)
        normalized_macs = [mac.replace(":", "").replace("-", "").lower() for mac in mac_addresses]
        
        unassign_data = {"macs": normalized_macs}
        
        try:
            response = mistapi.api.v1.orgs.deviceprofiles.unassignOrgDeviceProfile(
                session, self.connection.org_id, profile_id, unassign_data
            )
            if response.status_code == 200:
                result = response.data
                self._logger.info(
                    f"Unassigned {len(result.get('success', []))} device(s) from profile {profile_id}"
                )
                return result
            else:
                self._logger.error(
                    f"Failed to unassign devices from profile: {response.status_code}"
                )
                return None
        except Exception as error:
            self._logger.error(f"Error unassigning devices from profile: {error}")
            return None
    
    def create_ha_cluster(
        self,
        site_id: str,
        mac_addresses: list[str],
        managed: bool = True
    ) -> dict | None:
        """Create an HA cluster from two gateway devices.
        
        Uses POST /api/v1/orgs/{org_id}/inventory/create_ha_cluster
        
        Args:
            site_id: Site ID to assign the cluster to
            mac_addresses: List of exactly 2 device MAC addresses (node0, node1)
            managed: Whether the cluster should be Mist-managed (default True)
        
        Returns:
            Result dict if successful, None on error.
        """
        if len(mac_addresses) != 2:
            self._logger.error("HA cluster requires exactly 2 MAC addresses")
            return None
        
        session = self.connection.session
        if not session:
            return None
        
        # Normalize MAC addresses (remove colons/dashes, lowercase)
        normalized_macs = [
            mac.replace(":", "").replace("-", "").lower() 
            for mac in mac_addresses
        ]
        
        cluster_data = {
            "site_id": site_id,
            "managed": managed,
            "nodes": [
                {"mac": normalized_macs[0]},
                {"mac": normalized_macs[1]}
            ]
        }
        
        try:
            response = mistapi.api.v1.orgs.inventory.createOrgGatewayHaCluster(
                session, self.connection.org_id, cluster_data
            )
            if response.status_code == 200:
                self._logger.info(
                    f"Created HA cluster with nodes: {normalized_macs[0]}, {normalized_macs[1]}"
                )
                return {"success": True, "nodes": normalized_macs}
            else:
                self._logger.error(
                    f"Failed to create HA cluster: {response.status_code}"
                )
                return None
        except Exception as error:
            self._logger.error(f"Error creating HA cluster: {error}")
            return None
