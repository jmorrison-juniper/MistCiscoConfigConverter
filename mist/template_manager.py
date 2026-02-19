"""
Mist Gateway Template Management.

Handles gateway template operations for branch and standalone gateways.
"""

import logging
from typing import Any

import mistapi

from .connection import MistConnection


# Template constants
BRANCH_GATEWAY_TEMPLATE_NAME = "Branch-Default-Template"
STANDALONE_GATEWAY_TEMPLATE_NAME = "Standalone-Default-Template"


class MistTemplateManager:
    """Manages Mist gateway template operations.
    
    Gateway templates are used for branch (type=spoke) and standalone
    (type=standalone) gateways. Hub gateways use device profiles instead.
    
    Attributes:
        connection: MistConnection instance for API access
        branch_template_name: Name of the default branch template
    """
    
    def __init__(
        self,
        connection: MistConnection,
        branch_template_name: str = BRANCH_GATEWAY_TEMPLATE_NAME,
        standalone_template_name: str = STANDALONE_GATEWAY_TEMPLATE_NAME
    ):
        """Initialize template manager with connection.
        
        Args:
            connection: MistConnection instance
            branch_template_name: Name for branch gateway template
            standalone_template_name: Name for standalone gateway template
        """
        self.connection = connection
        self.branch_template_name = branch_template_name
        self.standalone_template_name = standalone_template_name
        self._logger = logging.getLogger(__name__)
    
    def find_by_name(self, template_name: str) -> dict | None:
        """Find a gateway template by name.
        
        Args:
            template_name: Name of the gateway template to find (case-insensitive)
        
        Returns:
            Template dict if found, None otherwise.
        """
        session = self.connection.session
        if not session:
            return None
        
        try:
            response = mistapi.api.v1.orgs.gatewaytemplates.listOrgGatewayTemplates(
                session, self.connection.org_id
            )
            if response.status_code == 200:
                templates = response.data
                for template in templates:
                    if template.get("name", "").lower() == template_name.lower():
                        self._logger.debug(
                            f"Found gateway template: {template_name} (ID: {template.get('id')})"
                        )
                        return template
            return None
        except Exception as error:
            self._logger.error(f"Error searching for gateway template: {error}")
            return None
    
    def create(
        self,
        template_name: str,
        template_type: str = "spoke",
        ntp_servers: list[str] | None = None,
        dns_servers: list[str] | None = None,
        syslog_servers: list[str] | None = None
    ) -> dict | None:
        """Create a new gateway template.
        
        Args:
            template_name: Name for the new gateway template
            template_type: Type of gateway ('spoke' for branch, 'standalone')
            ntp_servers: List of NTP server addresses
            dns_servers: List of DNS server addresses
            syslog_servers: List of syslog server addresses
        
        Returns:
            Created template dict if successful, None on error.
        """
        session = self.connection.session
        if not session:
            return None
        
        template_data: dict[str, Any] = {
            "name": template_name,
            "type": template_type
        }
        
        # Add NTP servers if provided
        if ntp_servers:
            template_data["ntp_servers"] = ntp_servers
        
        # Add DNS servers if provided
        if dns_servers:
            template_data["dns_servers"] = dns_servers
        
        # Add remote syslog if provided
        if syslog_servers:
            template_data["remote_syslog"] = {
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
            response = mistapi.api.v1.orgs.gatewaytemplates.createOrgGatewayTemplate(
                session, self.connection.org_id, template_data
            )
            if response.status_code in [200, 201]:
                self._logger.info(
                    f"Created gateway template: {template_name} (type={template_type})"
                )
                return response.data
            else:
                self._logger.error(
                    f"Failed to create gateway template: {response.status_code}"
                )
                return None
        except Exception as error:
            self._logger.error(f"Error creating gateway template: {error}")
            return None
    
    def update(
        self,
        template_id: str,
        ntp_servers: list[str] | None = None,
        dns_servers: list[str] | None = None,
        syslog_servers: list[str] | None = None
    ) -> dict | None:
        """Update an existing gateway template with NTP/DNS/syslog values.
        
        Only updates fields that are provided (not None).
        
        Args:
            template_id: ID of the template to update
            ntp_servers: List of NTP server IPs
            dns_servers: List of DNS server IPs
            syslog_servers: List of syslog host IPs
        
        Returns:
            Updated template dict on success, None on error.
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
            self._logger.debug("No updates to apply to template")
            return None
        
        update_types = []
        if "ntp_servers" in update_data:
            update_types.append("NTP")
        if "dns_servers" in update_data:
            update_types.append("DNS")
        if "remote_syslog" in update_data:
            update_types.append("Syslog")
        
        self._logger.info(
            f"Updating gateway template {template_id} with {', '.join(update_types)}"
        )
        
        try:
            response = mistapi.api.v1.orgs.gatewaytemplates.updateOrgGatewayTemplate(
                session, self.connection.org_id, template_id, update_data
            )
            if response.status_code == 200:
                self._logger.info(f"Template {template_id} updated successfully")
                return response.data
            else:
                self._logger.error(
                    f"Failed to update gateway template: {response.status_code}"
                )
                return None
        except Exception as error:
            self._logger.error(f"Error updating gateway template: {error}")
            return None
    
    def compare_with_cisco_config(
        self,
        template: dict,
        cisco_ntp_servers: list[str],
        cisco_dns_servers: list[str],
        cisco_syslog_hosts: list[str]
    ) -> dict:
        """Compare template config with Cisco parsed config.
        
        Args:
            template: Gateway template dict from Mist
            cisco_ntp_servers: NTP servers from Cisco config
            cisco_dns_servers: DNS servers from Cisco config  
            cisco_syslog_hosts: Syslog hosts from Cisco config
        
        Returns:
            Dict with comparison results for each category:
            {
                "ntp": {"match": bool, "template": [...], "cisco": [...]},
                "dns": {"match": bool, "template": [...], "cisco": [...]},
                "syslog": {"match": bool, "template": [...], "cisco": [...]}
            }
        """
        # Extract template values
        template_ntp = template.get("ntp_servers", [])
        template_dns = template.get("dns_servers", [])
        
        # Extract syslog hosts from remote_syslog.servers
        remote_syslog = template.get("remote_syslog", {})
        template_syslog = []
        if remote_syslog.get("enabled") and remote_syslog.get("servers"):
            template_syslog = [s.get("host", "") for s in remote_syslog["servers"]]
        
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
        ntp_match = set(template_ntp) == set(cisco_ntp_normalized)
        dns_match = set(template_dns) == set(cisco_dns_normalized)
        syslog_match = set(template_syslog) == set(cisco_syslog_normalized)
        
        # Check if template is empty but Cisco has values (will be auto-updated)
        ntp_will_update = not template_ntp and cisco_ntp_normalized
        dns_will_update = not template_dns and cisco_dns_normalized
        syslog_will_update = not template_syslog and cisco_syslog_normalized
        
        return {
            "ntp": {
                "match": ntp_match,
                "template": template_ntp,
                "cisco": cisco_ntp_normalized,
                "will_update_template": ntp_will_update
            },
            "dns": {
                "match": dns_match,
                "template": template_dns,
                "cisco": cisco_dns_normalized,
                "will_update_template": dns_will_update
            },
            "syslog": {
                "match": syslog_match,
                "template": template_syslog,
                "cisco": cisco_syslog_normalized,
                "will_update_template": syslog_will_update
            }
        }
    
    def get_or_create_branch_template(
        self,
        ntp_servers: list[str] | None = None,
        dns_servers: list[str] | None = None,
        syslog_servers: list[str] | None = None
    ) -> tuple[dict | None, bool]:
        """Get the branch gateway template, creating it if it doesn't exist.
        
        Uses the configured branch_template_name (default: Branch-Default-Template).
        Creates with type='spoke' if not found, using provided config values.
        
        Args:
            ntp_servers: NTP servers for new template creation
            dns_servers: DNS servers for new template creation
            syslog_servers: Syslog servers for new template creation
        
        Returns:
            Tuple of (template dict, was_created bool). Template is None on error.
        """
        template = self.find_by_name(self.branch_template_name)
        if template:
            self._logger.debug(
                f"Using existing gateway template: {self.branch_template_name}"
            )
            return template, False
        
        self._logger.info(
            f"Gateway template '{self.branch_template_name}' not found, creating..."
        )
        new_template = self.create(
            self.branch_template_name, 
            template_type="spoke",
            ntp_servers=ntp_servers,
            dns_servers=dns_servers,
            syslog_servers=syslog_servers
        )
        return new_template, new_template is not None
    
    def get_or_create_standalone_template(
        self,
        ntp_servers: list[str] | None = None,
        dns_servers: list[str] | None = None,
        syslog_servers: list[str] | None = None
    ) -> tuple[dict | None, bool]:
        """Get the standalone gateway template, creating it if it doesn't exist.
        
        Uses the configured standalone_template_name (default: Standalone-Default-Template).
        Creates with type='standalone' if not found, using provided config values.
        
        Args:
            ntp_servers: NTP servers for new template creation
            dns_servers: DNS servers for new template creation
            syslog_servers: Syslog servers for new template creation
        
        Returns:
            Tuple of (template dict, was_created bool). Template is None on error.
        """
        template = self.find_by_name(self.standalone_template_name)
        if template:
            self._logger.debug(
                f"Using existing gateway template: {self.standalone_template_name}"
            )
            return template, False
        
        self._logger.info(
            f"Gateway template '{self.standalone_template_name}' not found, creating..."
        )
        new_template = self.create(
            self.standalone_template_name, 
            template_type="standalone",
            ntp_servers=ntp_servers,
            dns_servers=dns_servers,
            syslog_servers=syslog_servers
        )
        return new_template, new_template is not None
