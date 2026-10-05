"""Offline regression tests for Cisco-to-Mist conversion behavior."""

import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch

import app as converter_app
from parser.cisco_parser import (
    CiscoConfigParser,
    extract_speed_from_description,
    subnet_mask_to_cidr,
)


SAMPLE_CONFIG = """\
hostname edge-branch-01
snmp-server location 123 Main St, Springfield, IL 62704
snmp-server contact NetOps
ntp server 192.0.2.123
ip name-server 192.0.2.53
vlan 300
 name Users
!
ip dhcp excluded-address 10.30.0.1 10.30.0.20
ip dhcp pool CLIENTS
 network 10.30.0.0 255.255.255.0
 default-router 10.30.0.1
 dns-server 192.0.2.53
!
interface GigabitEthernet0/0/0
 description Internet 100M
 ip address dhcp
 ip nat outside
 no shutdown
!
interface Vlan300
 description Users
 ip address 10.30.0.1 255.255.255.0
 ip helper-address 192.0.2.10
 no shutdown
!
ip route 0.0.0.0 0.0.0.0 GigabitEthernet0/0/0 198.51.100.1
"""


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.client = converter_app.app.test_client()

    def test_uploaded_config_returns_wan_lan_route_and_dhcp_preview(self):
        site_manager = MagicMock()
        site_manager.find_by_name.return_value = None
        template_manager = MagicMock()
        template_manager.branch_template_name = "Branch"
        template_manager.find_by_name.return_value = None
        network_manager = MagicMock()
        network_manager.preview_lan_networks.return_value = [{"name": "vlan0300"}]

        with (
            patch.object(converter_app, "get_site_manager", return_value=site_manager),
            patch.object(
                converter_app, "get_template_manager", return_value=template_manager
            ),
            patch.object(
                converter_app, "get_network_manager", return_value=network_manager
            ),
        ):
            response = self.client.post(
                "/api/convert",
                data={
                    "gateway_type": "branch",
                    "config_file": (BytesIO(SAMPLE_CONFIG.encode()), "branch.cfg"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["status"], "preview")
        proposed = body["proposed"]
        self.assertEqual(proposed["device_name"], "edge-branch-01")
        self.assertEqual(proposed["wan_interfaces"][0]["ip_config_type"], "dhcp")
        self.assertEqual(proposed["wan_interfaces"][0]["upload_kbps"], 100_000)
        self.assertEqual(
            proposed["wan_interfaces"][0]["default_gateway"], "198.51.100.1"
        )
        self.assertEqual(proposed["lan_interfaces"][0]["vlan_id"], 300)
        self.assertEqual(proposed["lan_interfaces"][0]["cidr_prefix"], "24")
        self.assertEqual(proposed["static_routes_extracted"][0]["cidr"], "0.0.0.0/0")
        self.assertEqual(proposed["dhcp_pools_extracted"][0]["ip_start"], "10.30.0.21")
        self.assertEqual(proposed["network_preview"], [{"name": "vlan0300"}])
        site_manager.find_by_name.assert_called_once_with("edge-branch-01")
        template_manager.find_by_name.assert_called_once_with("Branch")
        network_manager.preview_lan_networks.assert_called_once()

    def test_invalid_gateway_and_missing_input_are_rejected_without_mist_calls(self):
        with patch.object(converter_app, "get_site_manager") as get_site_manager:
            bad_gateway = self.client.post(
                "/api/convert", data={"gateway_type": "unsupported"}
            )
            missing_file = self.client.post(
                "/api/convert", data={"gateway_type": "branch"}
            )

        self.assertEqual(bad_gateway.status_code, 400)
        self.assertEqual(missing_file.status_code, 400)
        get_site_manager.assert_not_called()

    def test_selected_filename_traversal_is_rejected(self):
        response = self.client.post(
            "/api/convert",
            data={"gateway_type": "branch", "selected_file": "../outside.cfg"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "Invalid filename")

    def test_configuration_without_hostname_is_rejected_before_api_lookup(self):
        with patch.object(converter_app, "get_site_manager") as get_site_manager:
            response = self.client.post(
                "/api/convert",
                data={
                    "gateway_type": "branch",
                    "config_file": (
                        BytesIO(b"interface Vlan1\n ip address dhcp\n"),
                        "empty.cfg",
                    ),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Could not extract hostname", response.get_json()["error"])
        get_site_manager.assert_not_called()

    def test_parser_boundaries_for_masks_and_description_speeds(self):
        self.assertEqual(subnet_mask_to_cidr("255.255.255.0"), 24)
        self.assertEqual(subnet_mask_to_cidr("255.0.255.0"), 0)
        self.assertEqual(subnet_mask_to_cidr("not-a-mask"), 0)
        self.assertEqual(
            extract_speed_from_description("BDW=50/10"), (50_000, 10_000)
        )
        self.assertEqual(extract_speed_from_description("no speed specified"), (0, 0))

        parsed = CiscoConfigParser("hostname edge\n").parse()
        self.assertEqual(parsed["system"]["hostname"], "edge")
        self.assertEqual(parsed["summary"]["interface_count"], 0)


class ApplyConfirmationTests(unittest.TestCase):
    """Prove that /api/apply needs the typed word before it touches Mist."""

    APPLY_BODY = {"device_name": "edge-branch-01", "gateway_type": "branch"}  # A valid body except for the word.

    def setUp(self):
        """Create a Flask test client so no network request leaves the test."""
        self.client = converter_app.app.test_client()  # Send requests to the app in memory.

    def post_apply(self, extra: dict) -> tuple:
        """Send an apply request with a mocked Mist connection and return the response and the mock."""
        with patch.object(converter_app, "get_mist_connection") as connection_factory:  # Block each live Mist call.
            connection_factory.return_value.check_connection.return_value = {
                "connected": False,
                "error": "offline test",
            }  # Stop the route at the first Mist step after the word check.
            response = self.client.post("/api/apply", json={**self.APPLY_BODY, **extra})  # Send the request.
        return response, connection_factory  # Give the response and the call record to the test.

    def test_missing_confirmation_is_rejected_before_mist_calls(self):
        """A request with no confirmation field changes nothing."""
        response, connection_factory = self.post_apply({})  # Omit the confirmation field.
        self.assertEqual(response.status_code, 400)  # Reject the request.
        self.assertEqual(response.get_json()["error"], "Confirmation required")  # Give a clear JSON error.
        self.assertIn(converter_app.APPLY_CONFIRMATION_WORD, response.get_json()["message"])  # Name the word.
        connection_factory.assert_not_called()  # Prove that no Mist request started.

    def test_wrong_confirmation_is_rejected_before_mist_calls(self):
        """A request with an incorrect word changes nothing."""
        for word in ["apply", "CONFIRM", " APPLY", "", None, ["APPLY"]]:  # Test case, space, other words, and types.
            with self.subTest(word=word):
                response, connection_factory = self.post_apply({"confirmation": word})  # Send the wrong word.
                self.assertEqual(response.status_code, 400)  # Reject the request.
                self.assertEqual(response.get_json()["error"], "Confirmation required")  # Give a clear JSON error.
                connection_factory.assert_not_called()  # Prove that no Mist request started.

    def test_matching_confirmation_reaches_the_mist_connection_check(self):
        """A request with the correct word passes the gate and reaches the mocked Mist check."""
        response, connection_factory = self.post_apply(
            {"confirmation": converter_app.APPLY_CONFIRMATION_WORD}
        )  # Send the correct word.
        self.assertEqual(response.status_code, 503)  # Stop at the mocked offline connection, not at the gate.
        self.assertIn("Mist API not connected", response.get_json()["error"])  # Prove the gate let it through.
        connection_factory.assert_called_once()  # Prove that the route reached the Mist step.

    def test_non_object_json_body_is_rejected(self):
        """A JSON list cannot pass the gate."""
        with patch.object(converter_app, "get_mist_connection") as connection_factory:  # Block each live Mist call.
            response = self.client.post("/api/apply", json=["APPLY"])  # Send a list instead of an object.
        self.assertEqual(response.status_code, 400)  # Reject the request.
        connection_factory.assert_not_called()  # Prove that no Mist request started.

    def test_page_disables_apply_until_the_word_is_typed(self):
        """The page gives the word to the browser and starts with the apply button disabled."""
        with patch.object(converter_app, "get_mist_connection") as connection_factory:  # Block each live Mist call.
            page = self.client.get("/").get_data(as_text=True)  # Render the interface.
        connection_factory.assert_not_called()  # The page render needs no Mist request.
        self.assertIn('id="applyConfirmInput"', page)  # Show the field for the typed word.
        self.assertIn(
            f'data-confirmation-word="{converter_app.APPLY_CONFIRMATION_WORD}"', page
        )  # Use the server word in the browser.
        self.assertRegex(page, r'id="applyConfirmBtn" disabled>')  # Start with the apply button disabled.
        self.assertIn("confirmation: typedConfirmation", page)  # Send the typed word to the server.


if __name__ == "__main__":
    unittest.main()
