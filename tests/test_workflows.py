"""Contract checks for the GitHub Actions workflows of this repository.

A later edit can remove a guard from a workflow with no failed run. These
tests read each workflow file as text and fail when a required value is absent.
"""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]  # Find the repository root from this file.
WORKFLOWS = ROOT / ".github" / "workflows"  # Find the workflow folder of the repository.
DEVTOOLS_PIN = "da02d4c6a2163d1882f2ad25fce80b8ba38304d1 # v0.6.2"  # Name the approved shared release.


class WorkflowContractTests(unittest.TestCase):
    """Protect the CodeQL workflow and the misthelper-devtools pins."""

    def read_workflow(self, name: str) -> str:
        """Return the text of one workflow file, so each test reads the same path."""
        return (WORKFLOWS / name).read_text(encoding="utf-8")  # Read the file as UTF-8 text.

    def test_codeql_workflow_calls_the_shared_analysis(self) -> None:
        """Require the shared CodeQL call, its inputs, and its permissions."""
        content = self.read_workflow("codeql.yml")  # Read the CodeQL caller workflow.
        expected_values = [
            f"reusable-codeql.yml@{DEVTOOLS_PIN}",
            "languages: '[\"python\"]'",
            "config-file: ./.github/codeql/codeql-config.yml",
            "permissions: {}",
            "security-events: write",
            "actions: read",
            "pull_request:",
            "branches: [main]",
            "schedule:",
            "workflow_dispatch:",
        ]  # List each value that a future edit must keep.
        for value in expected_values:
            with self.subTest(value=value):
                self.assertIn(value, content)  # Fail when the workflow loses a required value.
        self.assertTrue((ROOT / ".github" / "codeql" / "codeql-config.yml").is_file())  # Require the configuration file.

    def test_codeql_workflow_never_cancels_a_main_run(self) -> None:
        """Require a unique group and no cancellation for a run on main."""
        content = self.read_workflow("codeql.yml")  # Read the CodeQL caller workflow.
        self.assertIn("github.ref == 'refs/heads/main' && github.run_id", content)  # Give each main run its own group.
        self.assertIn("cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}", content)  # Never cancel on main.

    def test_every_devtools_pin_names_release_0_6_2(self) -> None:
        """Require each shared workflow call to pin the approved release commit."""
        pins = []  # Collect each misthelper-devtools call line.
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            for line in workflow.read_text(encoding="utf-8").splitlines():
                if "misthelper-devtools/" in line and "uses:" in line:
                    pins.append((workflow.name, line.strip()))  # Keep the file name for the report.
        self.assertGreaterEqual(len(pins), 3)  # Fail when the scan finds no pins to examine.
        for workflow_name, line in pins:
            with self.subTest(workflow=workflow_name):
                self.assertTrue(line.endswith(DEVTOOLS_PIN), line)  # Reject an older or unnamed pin.


if __name__ == "__main__":
    unittest.main()
