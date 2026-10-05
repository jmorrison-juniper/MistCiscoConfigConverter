"""Path traversal tests for the routes that read a file named by a request.

CodeQL rule py/path-injection reported these routes. Each test proves that a
name with "..", an absolute path, or a symbolic link that leaves the allowed
directory gets HTTP 400 and that no file outside the directory is read.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as converter_app


RESTORE_ROUTES = [
    "/api/poweruser/restore-service-policies",
    "/api/poweruser/restore-services",
    "/api/poweruser/restore-networks",
]  # Name each power user route that reads a backup file from output/.


class ResolveContainedPathTests(unittest.TestCase):
    """Prove the one helper that keeps a request path inside its directory."""

    def setUp(self) -> None:
        """Make an allowed directory and a secret file outside it for each test."""
        self.workspace = tempfile.TemporaryDirectory()  # Isolate each test in its own folder.
        self.addCleanup(self.workspace.cleanup)  # Remove the folder after the test.
        root = Path(self.workspace.name)  # Use the temporary folder as the root.
        self.allowed = root / "allowed"  # Stand in for input/ or output/.
        self.allowed.mkdir()  # Create the allowed directory.
        self.secret = root / "secret.txt"  # Stand in for a file that the app must not read.
        self.secret.write_text("secret", encoding="utf-8")  # Give the outside file content.
        (self.allowed / "router 1.cfg").write_text("hostname r1", encoding="utf-8")  # Add a valid file with a space.

    def test_plain_file_name_resolves_inside_the_directory(self) -> None:
        """A plain name, with a space, gives the real path inside the directory."""
        resolved = converter_app.resolve_contained_path(self.allowed, "router 1.cfg")  # Resolve a valid name.
        self.assertEqual(resolved, Path(os.path.realpath(self.allowed / "router 1.cfg")))  # Keep the same file.

    def test_parent_reference_is_rejected(self) -> None:
        """A name with ".." does not leave the directory."""
        self.assertIsNone(converter_app.resolve_contained_path(self.allowed, "../secret.txt"))  # Reject traversal.
        self.assertIsNone(converter_app.resolve_contained_path(self.allowed, ".."))  # Reject the parent itself.

    def test_absolute_path_is_rejected(self) -> None:
        """An absolute path does not replace the allowed directory."""
        self.assertIsNone(converter_app.resolve_contained_path(self.allowed, str(self.secret)))  # Reject the path.

    def test_symbolic_link_escape_is_rejected(self) -> None:
        """A link inside the directory that points outside it is rejected."""
        (self.allowed / "link.cfg").symlink_to(self.secret)  # Plant a link to the outside file.
        self.assertIsNone(converter_app.resolve_contained_path(self.allowed, "link.cfg"))  # Reject the link.

    def test_non_text_and_empty_names_are_rejected(self) -> None:
        """A JSON number, None, or an empty name gives no path."""
        for value in (42, None, "", "   "):
            with self.subTest(value=value):
                self.assertIsNone(converter_app.resolve_contained_path(self.allowed, value))  # Reject the value.


class RoutePathContainmentTests(unittest.TestCase):
    """Prove that each flagged route answers HTTP 400 for an escaping name."""

    def setUp(self) -> None:
        """Point input/ and output/ at a temporary folder and plant escape links."""
        self.workspace = tempfile.TemporaryDirectory()  # Isolate the test from the real folders.
        self.addCleanup(self.workspace.cleanup)  # Remove the folder after the test.
        root = Path(self.workspace.name)  # Use the temporary folder as the root.
        secret = root / "secret.txt"  # Stand in for a file outside the allowed folders.
        secret.write_text("[]", encoding="utf-8")  # Give it valid JSON so only containment can stop it.
        for name in ("input", "output"):
            folder = root / name  # Make one allowed folder.
            folder.mkdir()  # Create it on disk.
            (folder / "escape.cfg").symlink_to(secret)  # Plant a link that leaves the folder.
        patches = [
            patch.object(converter_app, "INPUT_DIR", root / "input"),
            patch.object(converter_app, "OUTPUT_DIR", root / "output"),
            patch.object(converter_app, "is_poweruser", return_value=True),
            patch.object(converter_app, "get_mist_connection"),
        ]  # Redirect the folders, open power user mode, and block each Mist call.
        for active in patches:
            active.start()  # Apply the patch for this test.
            self.addCleanup(active.stop)  # Remove the patch after the test.
        self.client = converter_app.app.test_client()  # Send requests to the app in memory.
        self.hostile_names = ["../secret.txt", str(secret), "escape.cfg"]  # List each escape form.

    def test_input_file_routes_reject_escaping_names(self) -> None:
        """The convert and display routes answer 400 for each escape form."""
        for route in ("/api/convert", "/api/display"):
            for name in self.hostile_names:
                with self.subTest(route=route, name=name):
                    response = self.client.post(
                        route, data={"selected_file": name, "gateway_type": "branch"}
                    )  # Ask the route to read the hostile name.
                    self.assertEqual(response.status_code, 400)  # Reject the request.

    def test_restore_routes_reject_escaping_names(self) -> None:
        """Each restore route answers 400 and makes no Mist connection."""
        for route in RESTORE_ROUTES:
            for name in [*self.hostile_names, 42]:
                with self.subTest(route=route, name=name):
                    response = self.client.post(
                        route, json={"confirmation": "CONFIRM", "filename": name}
                    )  # Ask the route to restore from the hostile name.
                    self.assertEqual(response.status_code, 400)  # Reject the request.
        converter_app.get_mist_connection.assert_not_called()  # Prove that no Mist request started.


if __name__ == "__main__":
    unittest.main()
