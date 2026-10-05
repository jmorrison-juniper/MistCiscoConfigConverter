"""Tests for the debug mode of the Flask development server.

CodeQL rule py/flask-debug reported `debug=True` in `app.run`. These tests
prove that debug mode is off unless FLASK_DEBUG turns it on.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import app as converter_app


ROOT = Path(__file__).resolve().parents[1]  # Find the repository root from this file.


class FlaskDebugTests(unittest.TestCase):
    """Prove that the development server starts with debug mode off by default."""

    def test_debug_is_off_when_the_variable_is_absent(self) -> None:
        """No FLASK_DEBUG value gives debug mode off."""
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(converter_app.flask_debug_enabled())  # Default to off.

    def test_debug_reads_only_explicit_on_values(self) -> None:
        """Only 1, true, or yes turns debug mode on."""
        cases = {"1": True, "true": True, " TRUE ": True, "yes": True, "0": False, "false": False, "": False, "on2": False}
        for value, expected in cases.items():
            with self.subTest(value=value), patch.dict(os.environ, {"FLASK_DEBUG": value}, clear=True):
                self.assertEqual(converter_app.flask_debug_enabled(), expected)  # Match the documented values.

    def test_development_server_starts_with_debug_off_by_default(self) -> None:
        """`python app.py` passes debug=False to Flask when FLASK_DEBUG is absent."""
        with patch.dict(os.environ, {}, clear=True), patch.object(converter_app.app, "run") as run:
            converter_app.run_development_server()  # Start the server with the run call mocked.
        run.assert_called_once_with(host="0.0.0.0", port=8000, debug=False)  # Prove debug mode is off.

    def test_container_files_do_not_set_flask_debug(self) -> None:
        """The container image and the Compose file never turn debug mode on."""
        for name in ("Containerfile", "compose.yml"):
            with self.subTest(file=name):
                self.assertNotIn("FLASK_DEBUG", (ROOT / name).read_text(encoding="utf-8"))  # Keep debug off.


if __name__ == "__main__":
    unittest.main()
