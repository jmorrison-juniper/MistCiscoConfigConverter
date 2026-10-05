"""Tests that an API response never holds exception text or a traceback.

CodeQL rule py/stack-trace-exposure reported 27 sites. Each test raises an
exception with a marker text and proves that the marker reaches the server
log but not the JSON that the client receives.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import app as converter_app
from mist.connection import CONNECTION_FAILURE_MESSAGE, MistConnection
from parser.cisco_parser import PARSE_FAILURE_MESSAGE, CiscoConfigParser

MARKER = "internal-detail-7f3a"  # Text that must never reach a client.


class ErrorReplyTests(unittest.TestCase):
    """Check the one helper that builds client error text."""

    def test_item_logs_detail_and_returns_safe_text(self) -> None:
        """A failed item keeps its context, logs the traceback, and hides the exception text."""
        with self.assertLogs(converter_app.logger, level="ERROR") as captured:  # Capture the server log.
            text = converter_app.ErrorReply.item("Site lab", RuntimeError(MARKER))  # Report one failed item.
        self.assertTrue(text.startswith("Site lab: "))  # Keep the item name for the user.
        self.assertNotIn(MARKER, text)  # Hide the exception text.
        self.assertIn(MARKER, "\n".join(captured.output))  # Keep the detail for the operator.

    def test_response_keeps_shape_and_status(self) -> None:
        """A failed route gives JSON with one error key and the given status."""
        with converter_app.app.app_context():  # Give jsonify an application.
            response, status = converter_app.ErrorReply.response("backing up networks")  # Build the reply.
        self.assertEqual(status, 500)  # Keep the server error status.
        self.assertEqual(
            response.get_json(), {"error": "Error backing up networks. See the server log for details."}
        )  # Name the operation only.


class RouteResponseTests(unittest.TestCase):
    """Check routes that fail with an exception."""

    def setUp(self) -> None:
        """Make a test client in power user mode."""
        self.client = converter_app.app.test_client()  # Call routes with no server.
        environment = patch.dict(os.environ, {"POWERUSER": "true"})  # Turn on the power user routes.
        environment.start()  # Apply the setting for this test.
        self.addCleanup(environment.stop)  # Restore the environment after the test.

    def test_route_exception_is_not_in_response(self) -> None:
        """A power user route that raises gives a generic error."""
        with patch.object(converter_app, "get_mist_connection", side_effect=RuntimeError(MARKER)):  # Fail inside the try.
            with self.assertLogs(converter_app.logger, level="ERROR"):  # Expect the server log entry.
                response = self.client.post("/api/poweruser/backup-service-policies", json={})  # Call the route.
        self.assertEqual(response.status_code, 500)  # Keep the server error status.
        self.assertNotIn(MARKER, response.get_data(as_text=True))  # Hide the exception text.
        self.assertIn("backing up service policies", response.get_json()["error"])  # Name the operation.

    def test_mist_status_hides_connection_exception(self) -> None:
        """The status route reports a fixed text when the org lookup raises."""
        connection = MistConnection(api_token="token", org_id="org")  # Configure a connection with test values.
        connection._session = MagicMock()  # Skip the real session.
        with patch("mist.connection.mistapi.api.v1.orgs.orgs.getOrg", side_effect=RuntimeError(MARKER)):  # Fail the lookup.
            with patch.object(converter_app, "get_mist_connection", return_value=connection):  # Use the test connection.
                with self.assertLogs("mist.connection", level="ERROR") as captured:  # Capture the server log.
                    response = self.client.get("/api/mist-status")  # Call the status route.
        self.assertNotIn(MARKER, response.get_data(as_text=True))  # Hide the exception text.
        self.assertEqual(response.get_json()["error"], CONNECTION_FAILURE_MESSAGE)  # Give the fixed text.
        self.assertIn(MARKER, "\n".join(captured.output))  # Keep the detail for the operator.


class ParserErrorTests(unittest.TestCase):
    """Check that the parse result holds no exception text."""

    def test_parse_error_is_generic(self) -> None:
        """A section parser that raises adds the fixed parse failure text."""
        parser = CiscoConfigParser("hostname r1\n")  # Build a parser for a small file.
        with patch.object(parser, "_parse_vrfs", side_effect=RuntimeError(MARKER)):  # Fail one section.
            with self.assertLogs("parser.cisco_parser", level="ERROR"):  # Expect the server log entry.
                result = parser.parse()  # Run the full parse.
        self.assertEqual(result["parse_errors"], [PARSE_FAILURE_MESSAGE])  # Report the fixed text only.


class SourceTests(unittest.TestCase):
    """Check that no route builds client text from an exception."""

    def test_app_has_no_exception_text_in_responses(self) -> None:
        """The app source holds no str(error) call."""
        source = Path(converter_app.__file__).read_text(encoding="utf-8")  # Read the route source.
        self.assertNotIn("str(error)", source)  # Each site must use ErrorReply.
        self.assertEqual(source.count("ErrorReply.response("), 15)  # Count the route sites.
        self.assertEqual(source.count("ErrorReply.item("), 14)  # Count the results list sites.


if __name__ == "__main__":
    unittest.main()
