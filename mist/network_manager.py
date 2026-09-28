"""
Mist Network and Service Management.

Handles creation and matching of Mist org networks and services
for LAN interfaces extracted from Cisco configurations.

Networks in Mist represent Layer 3 VLAN segments (destinations).
Services in Mist represent applications that use those networks.
Each LAN interface maps to one network and one corresponding service.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any

import mistapi

from .connection import MistConnection

# Hard timeout (seconds) per API call as a fallback when the requests-level
# TimeoutHTTPAdapter does not fire (observed with gevent + SSL edge cases)
API_CALL_TIMEOUT = 120


class MistNetworkManager:
    """Manages Mist organization networks and services for LAN interfaces.

    Naming convention: vlan + 4-digit zero-padded VLAN ID (e.g., vlan0090).
    Match strategy: name + subnet match = skip; name match + different subnet
    = disambiguate with _2 suffix.

    Guest networks are created without OrgOverlay VPN access.
    Internal networks include OrgOverlay routed VPN access.

    Attributes:
        connection: MistConnection instance for API access
    """

    def __init__(self, connection: MistConnection):
        """Initialize network manager with connection.

        Args:
            connection: MistConnection instance
        """
        self.connection = connection
        self._logger = logging.getLogger(__name__)
        self._networks_cache: list[dict] | None = None
        self._services_cache: list[dict] | None = None

    # -----------------------------------------------------------------
    # Cache helpers
    # -----------------------------------------------------------------

    def _list_org_networks(self, force_refresh: bool = False) -> list[dict]:
        """List all org networks, with in-memory caching.

        Args:
            force_refresh: Force re-fetch from API

        Returns:
            List of network dicts from the org.
        """
        if self._networks_cache is not None and not force_refresh:
            return self._networks_cache

        session = self.connection.session
        if not session:
            return []

        try:
            response = mistapi.api.v1.orgs.networks.listOrgNetworks(
                session, self.connection.org_id
            )
            if response.status_code == 200:
                data = response.data
                self._networks_cache = data if isinstance(data, list) else []
                self._logger.debug(
                    f"Loaded {len(self._networks_cache)} org networks"
                )
                return self._networks_cache
        except Exception as error:
            self._logger.error(f"Error listing org networks: {error}")

        return []

    def _list_org_services(self, force_refresh: bool = False) -> list[dict]:
        """List all org services, with in-memory caching.

        Args:
            force_refresh: Force re-fetch from API

        Returns:
            List of service dicts from the org.
        """
        if self._services_cache is not None and not force_refresh:
            return self._services_cache

        session = self.connection.session
        if not session:
            return []

        try:
            response = mistapi.api.v1.orgs.services.listOrgServices(
                session, self.connection.org_id
            )
            if response.status_code == 200:
                data = response.data
                self._services_cache = data if isinstance(data, list) else []
                self._logger.debug(
                    f"Loaded {len(self._services_cache)} org services"
                )
                return self._services_cache
        except Exception as error:
            self._logger.error(f"Error listing org services: {error}")

        return []

    def invalidate_cache(self) -> None:
        """Clear the in-memory network and service caches."""
        self._networks_cache = None
        self._services_cache = None

    # -----------------------------------------------------------------
    # Network matching
    # -----------------------------------------------------------------

    def find_network_by_name(self, name: str) -> dict | None:
        """Find a network by exact name (case-insensitive).

        Args:
            name: Network name to find (e.g., 'vlan0090')

        Returns:
            Network dict if found, None otherwise.
        """
        networks = self._list_org_networks()
        for network in networks:
            if network.get("name", "").lower() == name.lower():
                return network
        return None

    def match_network(
        self, name: str
    ) -> dict[str, Any]:
        """Check if an existing network matches by name.

        Variable-based networks are matched purely by name because the
        subnet field contains site-variable references (e.g.,
        ``{{vlan0300_network}}/{{vlan0300_prefix}}``) that resolve
        differently per site.

        Args:
            name: Proposed network name (e.g., 'vlan0090')

        Returns:
            Dict with:
            - action: 'skip' | 'create'
            - existing: existing network dict or None
            - proposed_name: final name to use
            - reason: human-readable explanation
        """
        existing = self.find_network_by_name(name)

        if existing is None:
            return {
                "action": "create",
                "existing": None,
                "proposed_name": name,
                "reason": f"No existing network named '{name}'"
            }

        return {
            "action": "skip",
            "existing": existing,
            "proposed_name": name,
            "reason": f"Network '{name}' already exists"
        }

    # -----------------------------------------------------------------
    # Network CRUD
    # -----------------------------------------------------------------

    @staticmethod
    def build_network_data(
        name: str,
        is_guest: bool = False
    ) -> dict:
        """Build a Mist network object for API creation.

        Uses variable references for subnet and VLAN ID so that actual
        values are resolved from site variables at runtime.  The variable
        naming convention uses the network name as prefix:
        - subnet  -> {{vlan0300_network}}/{{vlan0300_prefix}}
        - vlan_id -> {{vlan0300_vlan}}

        Internal networks get OrgOverlay routed VPN access.
        Guest networks get no VPN access.

        Args:
            name: Network name (e.g., 'vlan0090')
            is_guest: If True, exclude OrgOverlay VPN routing

        Returns:
            Dict suitable for Mist API createOrgNetwork.
        """
        subnet_var = "{{" + name + "_network}}/{{" + name + "_prefix}}"
        vlan_var = "{{" + name + "_vlan}}"

        network_data: dict[str, Any] = {
            "name": name,
            "subnet": subnet_var,
            "vlan_id": vlan_var,
            "isolation": True,
            "internet_access": {
                "static_nat": {},
                "destination_nat": {}
            }
        }

        if not is_guest:
            network_data["vpn_access"] = {
                "OrgOverlay": {
                    "routed": True,
                    "no_readvertise_to_overlay": False,
                    "no_readvertise_to_lan_bgp": False,
                    "no_readvertise_to_lan_ospf": False
                }
            }

        return network_data

    def create_network(self, network_data: dict) -> dict | None:
        """Create a network in the Mist org.

        Uses concurrent.futures to enforce a hard 120-second timeout as a
        fallback in case the requests-level TimeoutHTTPAdapter does not fire
        (observed with gevent + SSL socket edge cases).

        Args:
            network_data: Network dict from build_network_data()

        Returns:
            Created network dict on success, None on failure.
        """
        session = self.connection.session
        if not session:
            return None

        network_name = network_data.get("name", "unknown")

        def _do_create():
            return mistapi.api.v1.orgs.networks.createOrgNetwork(
                session, self.connection.org_id, network_data
            )

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_create)
                response = future.result(timeout=API_CALL_TIMEOUT)

            if response.status_code == 200:
                created = response.data
                self._logger.info(
                    f"Created network: {created.get('name')} "
                    f"(ID: {created.get('id')})"
                )
                # Invalidate cache so next lookup sees the new network
                self._networks_cache = None
                return created
            else:
                self._logger.error(
                    f"Failed to create network {network_name}: "
                    f"HTTP {response.status_code}"
                )
        except FutureTimeout:
            self._logger.error(
                f"Timeout creating network {network_name}: "
                f"API did not respond within {API_CALL_TIMEOUT}s"
            )
        except Exception as error:
            self._logger.error(
                f"Error creating network {network_name}: {error}"
            )

        return None

    def get_or_create_network(
        self,
        name: str,
        is_guest: bool = False
    ) -> dict[str, Any]:
        """Find or create a variable-based network, returning the result.

        Networks use site-variable references for subnet and VLAN ID so
        that multiple sites can share the same org network definition
        with different concrete values.

        Args:
            name: Proposed network name (e.g., 'vlan0300')
            is_guest: Guest network flag

        Returns:
            Dict with:
            - network: network dict (existing or newly created)
            - action: 'skipped' | 'created' | 'error'
            - name_used: final network name
            - message: human-readable result
        """
        match = self.match_network(name)

        if match["action"] == "skip":
            return {
                "network": match["existing"],
                "action": "skipped",
                "name_used": match["proposed_name"],
                "message": match["reason"]
            }

        final_name = match["proposed_name"]
        network_data = self.build_network_data(
            name=final_name,
            is_guest=is_guest
        )

        created = self.create_network(network_data)
        if created:
            return {
                "network": created,
                "action": "created",
                "name_used": final_name,
                "message": f"Created variable-based network '{final_name}'"
            }

        return {
            "network": None,
            "action": "error",
            "name_used": final_name,
            "message": f"Failed to create network '{final_name}'"
        }

    # -----------------------------------------------------------------
    # Service matching and CRUD
    # -----------------------------------------------------------------

    def find_service_by_name(self, name: str) -> dict | None:
        """Find a service by exact name (case-insensitive).

        Args:
            name: Service name to find

        Returns:
            Service dict if found, None otherwise.
        """
        services = self._list_org_services()
        for service in services:
            if service.get("name", "").lower() == name.lower():
                return service
        return None

    @staticmethod
    def build_service_data(name: str) -> dict:
        """Build a Mist service (application) object for API creation.

        Each service maps 1:1 with a network. The service uses the same
        variable references as the network so addresses resolve from
        site variables at runtime.

        Args:
            name: Service name (matches network name, e.g., 'vlan0090')

        Returns:
            Dict suitable for Mist API createOrgService.
        """
        address_var = "{{" + name + "_network}}/{{" + name + "_prefix}}"
        return {
            "name": name,
            "type": "custom",
            "addresses": [address_var],
            "specs": [{"protocol": "any"}],
            "traffic_type": "default"
        }

    def create_service(self, service_data: dict) -> dict | None:
        """Create a service in the Mist org.

        Uses concurrent.futures to enforce a hard 120-second timeout as a
        fallback in case the requests-level TimeoutHTTPAdapter does not fire
        (observed with gevent + SSL socket edge cases).

        Args:
            service_data: Service dict from build_service_data()

        Returns:
            Created service dict on success, None on failure.
        """
        session = self.connection.session
        if not session:
            return None

        service_name = service_data.get("name", "unknown")

        def _do_create():
            return mistapi.api.v1.orgs.services.createOrgService(
                session, self.connection.org_id, service_data
            )

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_create)
                response = future.result(timeout=API_CALL_TIMEOUT)

            if response.status_code == 200:
                created = response.data
                self._logger.info(
                    f"Created service: {created.get('name')} "
                    f"(ID: {created.get('id')})"
                )
                self._services_cache = None
                return created
            else:
                self._logger.error(
                    f"Failed to create service {service_name}: "
                    f"HTTP {response.status_code}"
                )
        except FutureTimeout:
            self._logger.error(
                f"Timeout creating service {service_name}: "
                f"API did not respond within {API_CALL_TIMEOUT}s"
            )
        except Exception as error:
            self._logger.error(
                f"Error creating service {service_name}: {error}"
            )

        return None

    def get_or_create_service(
        self,
        name: str
    ) -> dict[str, Any]:
        """Find or create a variable-based service, returning the result.

        Service addresses use the same site-variable references as the
        corresponding network (e.g., ``{{vlan0300_network}}/{{vlan0300_prefix}}``).

        Args:
            name: Service name (should match network name)

        Returns:
            Dict with:
            - service: service dict (existing or newly created)
            - action: 'skipped' | 'created' | 'error'
            - message: human-readable result
        """
        existing = self.find_service_by_name(name)
        if existing:
            return {
                "service": existing,
                "action": "skipped",
                "message": f"Service '{name}' already exists"
            }

        service_data = self.build_service_data(name)
        created = self.create_service(service_data)
        if created:
            return {
                "service": created,
                "action": "created",
                "message": f"Created service '{name}'"
            }

        return {
            "service": None,
            "action": "error",
            "message": f"Failed to create service '{name}'"
        }

    # -----------------------------------------------------------------
    # Bulk operations for conversion pipeline
    # -----------------------------------------------------------------

    def preview_lan_networks(
        self, lan_interfaces: list[dict]
    ) -> list[dict]:
        """Preview what network/service operations would be performed.

        Does NOT create anything - just checks existing state and returns
        planned actions for the confirmation dialog.

        Args:
            lan_interfaces: List of extracted LAN interface dicts

        Returns:
            List of preview dicts, one per LAN interface, with:
            - lan_var_name: variable name reference
            - interface_name: Cisco interface name
            - mist_network_name: proposed network name
            - subnet: computed subnet string (for display only)
            - subnet_var: variable reference pattern used in the network
            - vlan_var: variable reference pattern for VLAN ID
            - is_guest: guest classification
            - network_action: 'skip' | 'create'
            - network_reason: explanation text
            - service_exists: whether matching service already exists
        """
        self.invalidate_cache()
        previews = []

        for lan in lan_interfaces:
            ip_address = lan.get("ip_address", "")
            cidr_prefix = lan.get("cidr_prefix", "")
            subnet_mask = lan.get("subnet_mask", "")
            vlan_id = lan.get("vlan_id", 0)
            mist_name = lan.get("mist_network_name", "")
            is_guest = lan.get("is_guest", False)

            # Compute network address for display (site variable value)
            subnet = self._compute_network_address(
                ip_address, subnet_mask, cidr_prefix
            )

            # Variable references that will be used in the network object
            subnet_var = "{{" + mist_name + "_network}}/{{" + mist_name + "_prefix}}"
            vlan_var = "{{" + mist_name + "_vlan}}"

            # Check network match (name-based for variable networks)
            network_match = self.match_network(mist_name)

            # Check service match
            service_name = network_match["proposed_name"]
            existing_service = self.find_service_by_name(service_name)

            previews.append({
                "lan_var_name": lan.get("lan_var_name", ""),
                "interface_name": lan.get("name", ""),
                "mist_network_name": network_match["proposed_name"],
                "subnet": subnet,
                "subnet_var": subnet_var,
                "vlan_var": vlan_var,
                "vlan_id": vlan_id,
                "is_guest": is_guest,
                "guest_indicators": lan.get("guest_indicators", []),
                "network_action": network_match["action"],
                "network_reason": network_match["reason"],
                "service_exists": existing_service is not None
            })

        return previews

    def apply_lan_networks(
        self, lan_interfaces: list[dict]
    ) -> dict[str, Any]:
        """Create networks and services for all LAN interfaces.

        Skips existing matches. Reports results for each interface.

        Args:
            lan_interfaces: List of extracted LAN interface dicts

        Returns:
            Dict with:
            - results: list of per-interface result dicts
            - networks_created: count
            - networks_skipped: count
            - services_created: count
            - services_skipped: count
            - errors: list of error messages
        """
        self.invalidate_cache()
        results = []
        networks_created = 0
        networks_skipped = 0
        services_created = 0
        services_skipped = 0
        errors = []

        for lan in lan_interfaces:
            mist_name = lan.get("mist_network_name", "")
            is_guest = lan.get("is_guest", False)

            # Create or skip variable-based network
            network_result = self.get_or_create_network(
                name=mist_name,
                is_guest=is_guest
            )

            if network_result["action"] == "error":
                errors.append(network_result["message"])
            elif network_result["action"] == "skipped":
                networks_skipped += 1
            else:
                networks_created += 1

            # Create or skip variable-based service
            service_name = network_result["name_used"]
            service_result = self.get_or_create_service(
                name=service_name
            )

            if service_result["action"] == "error":
                errors.append(service_result["message"])
            elif service_result["action"] == "skipped":
                services_skipped += 1
            else:
                services_created += 1

            results.append({
                "interface": lan.get("name", ""),
                "network": network_result,
                "service": service_result
            })

        return {
            "results": results,
            "networks_created": networks_created,
            "networks_skipped": networks_skipped,
            "services_created": services_created,
            "services_skipped": services_skipped,
            "errors": errors
        }

    @staticmethod
    def _compute_network_address(
        ip_address: str,
        subnet_mask: str,
        cidr_prefix: str
    ) -> str:
        """Compute the network address from an IP and mask.

        Args:
            ip_address: Host IP (e.g., '10.90.0.1')
            subnet_mask: Dotted mask (e.g., '255.255.255.0')
            cidr_prefix: Prefix length as string (e.g., '24')

        Returns:
            Network in CIDR notation (e.g., '10.90.0.0/24')
        """
        if not ip_address or not subnet_mask:
            return ""

        try:
            ip_parts = [int(octet) for octet in ip_address.split(".")]
            mask_parts = [int(octet) for octet in subnet_mask.split(".")]

            network_parts = [
                ip_parts[index] & mask_parts[index] for index in range(4)
            ]
            network_address = ".".join(str(part) for part in network_parts)
            prefix = cidr_prefix if cidr_prefix else "0"
            return f"{network_address}/{prefix}"
        except (ValueError, IndexError):
            return ""
