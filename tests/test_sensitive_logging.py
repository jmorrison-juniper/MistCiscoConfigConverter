"""Tests that the logs hold no MAC address, password, or street address.

CodeQL rule py/clear-text-logging-sensitive-data reported each log call that
these tests examine. Each test captures the log output at DEBUG level and
proves that the sensitive value is absent.
"""

import logging
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import app as converter_app
from mist.profile_manager import MistProfileManager
from mist.site_manager import MistSiteManager
from parser.address_parser import parse_snmp_location
from parser.cisco_parser import CiscoConfigParser, decode_cisco_type7


MAC_ONE = "aa:bb:cc:11:22:33"  # Give a MAC address that must not reach a log.
MAC_TWO = "aa:bb:cc:44:55:66"  # Give a second MAC address for an HA pair.
MAC_FRAGMENTS = ("aa:bb:cc:11:22:33", "aabbcc112233", "aa:bb:cc:44:55:66", "aabbcc445566")  # List each form.


class SensitiveLoggingTests(unittest.TestCase):
    """Prove that each flagged log call writes no sensitive value."""

    def capture(self, logger_name: str, action) -> str:
        """Run one action and return each log line that it wrote at DEBUG level."""
        with self.assertLogs(logger_name, level=logging.DEBUG) as captured:  # Record every level.
            action()  # Run the code under test.
        return "\n".join(captured.output)  # Join the lines so one search covers them.

    def assert_no_mac(self, text: str) -> None:
        """Fail when the log text holds any form of the test MAC addresses."""
        for fragment in MAC_FRAGMENTS:
            self.assertNotIn(fragment, text)  # Reject the MAC address in each form.

    def site_manager(self) -> MistSiteManager:
        """Return a site manager with a mocked connection and one unknown device."""
        manager = MistSiteManager(MagicMock())  # Use a connection that sends no request.
        manager.get_site_gateways = MagicMock(return_value=[{"mac": "001122334455"}])  # Hold no test MAC.
        return manager  # Give the manager to the test.

    def test_site_manager_logs_counts_not_mac_addresses(self) -> None:
        """Assign, unassign, override, and naming calls log no MAC address."""
        manager = self.site_manager()  # Build the manager with mocks.
        with patch("mist.site_manager.mistapi") as mist_api:  # Block each live Mist call.
            mist_api.api.v1.orgs.inventory.updateOrgInventoryAssignment.return_value = MagicMock(
                status_code=200, data={"success": [], "error": []}
            )  # Answer the assignment calls offline.
            text = self.capture("mist.site_manager", lambda: (
                manager.assign_devices_to_site("site-1", [MAC_ONE, MAC_TWO]),
                manager.unassign_devices_from_site([MAC_ONE, MAC_TWO]),
                manager.apply_config_overrides_to_devices("site-1", [MAC_ONE, MAC_TWO], ntp_servers=["10.0.0.1"]),
                manager.name_devices_from_inventory("site-1", [MAC_ONE, MAC_TWO], "edge"),
            ))  # Run each flagged call.
        self.assert_no_mac(text)  # Prove that no MAC address reached the log.
        self.assertIn("Device 1 of 2 not found", text)  # Keep a useful message with the list position.

    def test_profile_manager_logs_ha_cluster_count(self) -> None:
        """The HA cluster success log holds the node count only."""
        manager = MistProfileManager(MagicMock())  # Use a connection that sends no request.
        with patch("mist.profile_manager.mistapi") as mist_api:  # Block each live Mist call.
            mist_api.api.v1.orgs.inventory.createOrgGatewayHaCluster.return_value = MagicMock(status_code=200)
            text = self.capture("mist.profile_manager", lambda: manager.create_ha_cluster("site-1", [MAC_ONE, MAC_TWO]))
        self.assert_no_mac(text)  # Prove that no MAC address reached the log.
        self.assertIn("Created HA cluster with 2 nodes", text)  # Keep the success message.

    def test_type7_decoder_logs_no_secret_text(self) -> None:
        """A bad Type 7 value logs an offset, not the hex text."""
        text = self.capture("parser.cisco_parser", lambda: decode_cisco_type7("08ZZ5D4F"))  # Decode a bad value.
        self.assertNotIn("ZZ", text)  # Prove that the secret text is absent.

    def test_hashed_password_log_names_no_password_field(self) -> None:
        """A hashed local password logs the user name only."""
        config = "username admin privilege 15 secret 9 $9$hashedvalueXYZ\n"  # Give a hashed password line.
        text = self.capture("parser.cisco_parser", lambda: CiscoConfigParser(config).parse())  # Parse the line.
        self.assertIn("User 'admin' has a hashed password", text)  # Keep the useful message.
        self.assertNotIn("hashedvalueXYZ", text)  # Prove that the hash is absent.
        self.assertNotIn("Type 9", text)  # Prove that no part of the password field is present.

    def test_snmp_location_log_holds_no_address_text(self) -> None:
        """The SNMP location log names the parts found, not the address."""
        location = "1234 Elm Street, Springfield, IL 62704"  # Give a street address.
        text = self.capture("parser.address_parser", lambda: parse_snmp_location(location))  # Parse the address.
        for fragment in ("1234 Elm", "Springfield", "62704"):
            self.assertNotIn(fragment, text)  # Prove that no address text reached the log.
        self.assertIn("Parsed SNMP location fields:", text)  # Keep a useful message.

    def test_apply_route_source_logs_device_counts(self) -> None:
        """The apply flow in app.py logs device counts, not MAC address lists."""
        source = Path(converter_app.__file__).read_text(encoding="utf-8")  # Read the route source.
        for forbidden in (
            "with devices: {mac_addresses}",
            "hub profile: {mac_addresses}",
            "Newly assigned devices: {assigned_devices}",
        ):
            self.assertNotIn(forbidden, source)  # Reject a log that writes the MAC list.


if __name__ == "__main__":
    unittest.main()
