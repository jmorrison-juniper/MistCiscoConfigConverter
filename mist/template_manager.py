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
        use_site_variables: bool = True
    ) -> dict | None:
        """Create a new gateway template.
        
        Templates use site variable references for NTP and DNS, allowing
        different values per site. Syslog is configured via site settings,
        not in templates.
        
        Args:
            template_name: Name for the new gateway template
            template_type: Type of gateway ('spoke' for branch, 'standalone')
            use_site_variables: If True, include variable references for NTP/DNS
        
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
        
        # Add NTP/DNS/Syslog server variable references
        # Site-specific values are stored in site vars (ntp1, ntp2, dns1, dns2, syslog1)
        if use_site_variables:
            template_data["ntp_servers"] = ["{{ntp1}}", "{{ntp2}}"]
            template_data["dns_servers"] = ["{{dns1}}", "{{dns2}}"]
            template_data["remote_syslog"] = {
                "enabled": True,
                "send_to_all_servers": False,
                "servers": [
                    {
                        "name": "syslog1",
                        "host": "{{syslog1}}",
                        "port": 514,
                        "protocol": "udp",
                        "facility": "any",
                        "severity": "any",
                        "tag": ""
                    }
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
        use_site_variables: bool = False
    ) -> dict | None:
        """Update an existing gateway template to use site variable references.
        
        Sets NTP, DNS, and Syslog to use site variable references.
        Variables: {{ntp1}}, {{ntp2}}, {{dns1}}, {{dns2}}, {{syslog1}}
        
        Args:
            template_id: ID of the template to update
            use_site_variables: If True, set NTP/DNS/Syslog to variable references
        
        Returns:
            Updated template dict on success, None on error.
        """
        session = self.connection.session
        if not session:
            return None
        
        if not use_site_variables:
            self._logger.debug("No updates to apply to template")
            return None
        
        update_data: dict[str, Any] = {
            "ntp_servers": ["{{ntp1}}", "{{ntp2}}"],
            "dns_servers": ["{{dns1}}", "{{dns2}}"],
            "remote_syslog": {
                "enabled": True,
                "send_to_all_servers": False,
                "servers": [
                    {
                        "name": "syslog1",
                        "host": "{{syslog1}}",
                        "port": 514,
                        "protocol": "udp",
                        "facility": "any",
                        "severity": "any",
                        "tag": ""
                    }
                ]
            }
        }
        
        self._logger.info(
            f"Updating gateway template {template_id} with site variable references"
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
        """Compare Cisco parsed config values for display purposes.
        
        NOTE: With the new architecture, templates use site variable references
        (e.g., {{ntp1}}, {{dns1}}) instead of literal values. Actual values
        are stored in site variables. Syslog is configured via site settings,
        not in templates.
        
        This method normalizes Cisco values and indicates that values will be
        stored as site variables.
        
        Args:
            template: Gateway template dict from Mist (for checking if vars are used)
            cisco_ntp_servers: NTP servers from Cisco config
            cisco_dns_servers: DNS servers from Cisco config  
            cisco_syslog_hosts: Syslog hosts from Cisco config
        
        Returns:
            Dict with parsed values and storage destination.
        """
        # Template values (should be variable references)
        template_ntp = template.get("ntp_servers", [])
        template_dns = template.get("dns_servers", [])
        
        # Check if template uses variable references
        uses_ntp_vars = any("{{" in str(s) for s in template_ntp) if template_ntp else False
        uses_dns_vars = any("{{" in str(s) for s in template_dns) if template_dns else False
        
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
        
        return {
            "ntp": {
                "template_uses_vars": uses_ntp_vars,
                "template": template_ntp,
                "cisco": cisco_ntp_normalized,
                "destination": "site_variables",
                "var_names": ["ntp1", "ntp2"]
            },
            "dns": {
                "template_uses_vars": uses_dns_vars,
                "template": template_dns,
                "cisco": cisco_dns_normalized,
                "destination": "site_variables",
                "var_names": ["dns1", "dns2"]
            },
            "syslog": {
                "template": [],  # Syslog not in template anymore
                "cisco": cisco_syslog_normalized,
                "destination": "site_settings",
                "setting_name": "remote_syslog"
            }
        }
    
    def add_wan_variable_ports(
        self,
        template_id: str,
        wan_interfaces: list[dict] | None = None,
        existing_wan_vars: list[str] | None = None
    ) -> dict | None:
        """Add WAN interface port_config entries with variable references.
        
        Creates port_config entries with keys like "{{wan1}}", "{{wan2_lte}}" that
        can be resolved per-site using site variables. Only adds variables
        that don't already exist in the template.
        
        Configures full WAN interface settings including:
        - IP config type (dhcp, static, pppoe)
        - Static IP/subnet/gateway (as variable references)
        - VLAN ID
        - LTE settings (APN, auth, username, password)
        
        Args:
            template_id: ID of the template to update
            wan_interfaces: List of WAN interface dicts with keys:
                - name: Original interface name (e.g., "GigabitEthernet0/0/0")
                - wan_var_name: Variable name (e.g., "wan1", "wan2_lte")
                - wan_type: "broadband" or "lte"
                - ip_config_type: "dhcp", "static", "pppoe", "negotiated"
                - ip_address, subnet_mask, default_gateway: Static IP details
                - encap_vlan_id: VLAN ID for tagged WAN
                - shutdown: Admin state
                - upload_kbps: Upload bandwidth for traffic shaping (0 if not found)
                - download_kbps: Download bandwidth for traffic shaping (0 if not found)
                - lte_apn, lte_auth, lte_username, lte_password: LTE settings
                - is_cellular: bool
                - cellular_slot: int or None
            existing_wan_vars: List of WAN var names already in template (e.g., ["wan1"])
        
        Returns:
            Updated template dict on success, None on error.
        """
        session = self.connection.session
        if not session:
            return None
        
        existing_wan_vars = existing_wan_vars or []
        wan_interfaces = wan_interfaces or []
        
        # Build port_config entries for ALL WAN variables
        # Always update to ensure name/description and other fields are current
        port_config: dict[str, Any] = {}
        updated_vars = []
        
        for wan_interface in wan_interfaces:
            var_name = wan_interface.get("wan_var_name", "")
            wan_type = wan_interface.get("wan_type", "broadband")
            ip_config_type = wan_interface.get("ip_config_type", "dhcp") or "dhcp"
            
            if var_name:
                port_key = "{{" + var_name + "}}"
                
                # Build ip_config based on parsed config type
                ip_config: dict[str, Any] = {}
                
                if ip_config_type == "static":
                    ip_config["type"] = "static"
                    # Use variable references for IP config values
                    ip_config["ip"] = "{{" + var_name + "_ip}}"
                    ip_config["netmask"] = "/{{" + var_name + "_subnet}}"
                    ip_config["gateway"] = "{{" + var_name + "_gateway}}"
                elif ip_config_type == "pppoe" or ip_config_type == "negotiated":
                    ip_config["type"] = "pppoe"
                    # PPPoE credentials would come from site variables if needed
                elif ip_config_type == "dhcp":
                    ip_config["type"] = "dhcp"
                else:
                    ip_config["type"] = "dhcp"  # Default to DHCP
                
                # Build port config entry
                port_entry: dict[str, Any] = {
                    "usage": "wan",
                    "name": "{{" + var_name + "_name}}",
                    "description": "{{" + var_name + "_desc}}",
                    "aggregated": False,
                    "redundant": False,
                    "critical": False,
                    "wan_type": wan_type,
                    "ip_config": ip_config,
                    "disable_autoneg": False,
                    "wan_source_nat": {
                        "disabled": False
                    }
                }
                
                # Add VLAN ID if configured
                vlan_id = wan_interface.get("encap_vlan_id", 0)
                if vlan_id and vlan_id > 0:
                    port_entry["vlan_id"] = "{{" + var_name + "_vlan}}"
                
                # Add disabled state if interface is shutdown
                if wan_interface.get("shutdown", False):
                    port_entry["disabled"] = True
                
                # Always add traffic shaping with variable references
                # If the site doesn't have the variable, the config won't resolve/activate
                port_entry["traffic_shaping"] = {
                    "enabled": True,
                    "max_tx_kbps": "{{" + var_name + "_upload_kbps}}"
                }
                
                # Add LTE-specific settings
                if wan_type == "lte":
                    lte_apn = wan_interface.get("lte_apn", "")
                    lte_auth = wan_interface.get("lte_auth", "none") or "none"
                    lte_username = wan_interface.get("lte_username", "")
                    lte_password = wan_interface.get("lte_password", "")
                    
                    if lte_apn:
                        # Use direct value or variable reference
                        port_entry["lte_apn"] = "{{" + var_name + "_apn}}"
                    
                    if lte_auth and lte_auth != "none":
                        port_entry["lte_auth"] = lte_auth
                        if lte_username:
                            port_entry["lte_username"] = "{{" + var_name + "_user}}"
                        if lte_password:
                            port_entry["lte_password"] = "{{" + var_name + "_pass}}"
                    else:
                        port_entry["lte_auth"] = "none"
                    
                    # LTE typically uses DHCP for IP
                    port_entry["ip_config"] = {"type": "dhcp"}
                
                port_config[port_key] = port_entry
                updated_vars.append(var_name)
        
        if not port_config:
            self._logger.debug(
                "No WAN interfaces to configure in template"
            )
            return None
        
        self._logger.info(
            f"Updating WAN variable ports in template {template_id}: {updated_vars}"
        )
        
        try:
            # First get existing port_config to merge
            response = mistapi.api.v1.orgs.gatewaytemplates.getOrgGatewayTemplate(
                session, self.connection.org_id, template_id
            )
            if response.status_code != 200:
                self._logger.error(
                    f"Failed to get template for port_config merge: {response.status_code}"
                )
                return None
            
            existing_template = response.data
            existing_port_config = existing_template.get("port_config", {})
            
            # Merge new WAN ports with existing config
            merged_port_config = {**existing_port_config, **port_config}
            
            update_data = {"port_config": merged_port_config}
            
            response = mistapi.api.v1.orgs.gatewaytemplates.updateOrgGatewayTemplate(
                session, self.connection.org_id, template_id, update_data
            )
            if response.status_code == 200:
                self._logger.info(
                    f"Template {template_id} updated with WAN ports: {list(port_config.keys())}"
                )
                return response.data
            else:
                self._logger.error(
                    f"Failed to update template with WAN ports: {response.status_code}"
                )
                return None
        except Exception as error:
            self._logger.error(f"Error adding WAN ports to template: {error}")
            return None

    def get_or_create_branch_template(self) -> tuple[dict | None, bool]:
        """Get the branch gateway template, creating it if it doesn't exist.
        
        Uses the configured branch_template_name (default: Branch-Default-Template).
        Creates with type='spoke' if not found. Template uses site variable
        references for NTP/DNS. Syslog is configured via site settings.
        
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
            use_site_variables=True
        )
        return new_template, new_template is not None
    
    def get_or_create_standalone_template(self) -> tuple[dict | None, bool]:
        """Get the standalone gateway template, creating it if it doesn't exist.
        
        Uses the configured standalone_template_name (default: Standalone-Default-Template).
        Creates with type='standalone' if not found. Template uses site variable
        references for NTP/DNS. Syslog is configured via site settings.
        
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
            use_site_variables=True
        )
        return new_template, new_template is not None
