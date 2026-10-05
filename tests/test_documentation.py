"""Regression checks for the landing page and its local documentation."""

import hashlib
import re
import struct
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
    def test_agent_instructions_use_the_canonical_file_and_spec_kit_path(self):
        """Protect the shared rules and the Spec Kit output path."""
        agents_file = ROOT / "AGENTS.md"  # Read the canonical shared instruction file.
        instructions_file = ROOT / ".github" / "copilot-instructions.md"  # Read repository rules.
        context_script = ROOT / ".specify" / "scripts" / "bash" / "update-agent-context.sh"  # Read Spec Kit targets.
        digest = hashlib.sha256(agents_file.read_bytes()).hexdigest()  # Check the canonical content.
        instructions = instructions_file.read_text(encoding="utf-8")  # Check the repository guidance.
        script = context_script.read_text(encoding="utf-8")  # Check every generated target.
        self.assertEqual(
            digest,
            "db663ecdfa28bd6000ca3c658bff22543791c15d3b59a3642ae181dc7c4e6d43",
        )  # Reject a change to the canonical shared rules.
        self.assertIn(".specify/memory/agent-context-*.md", instructions)  # Keep the context location clear.
        protected_targets = re.findall(
            r'^(?:CLAUDE_FILE|COPILOT_FILE|AGENTS_FILE|AMP_FILE|Q_FILE|BOB_FILE)="([^"]+)"',
            script,
            flags=re.MULTILINE,
        )  # Find each Spec Kit target that must use the memory folder.
        self.assertEqual(len(protected_targets), 6)  # Require one target for each protected agent type.
        for target in protected_targets:
            with self.subTest(target=target):
                self.assertIn(
                    "/.specify/memory/agent-context-",
                    target,
                )  # Keep every protected target in the memory folder.
        self.assertIn(".specify/memory/agent-context-", script)  # Keep Spec Kit context output enabled.

    def test_landing_page_has_only_the_six_requested_sections(self):
        content = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertEqual(
            re.findall(r"^#{2,6} (.+)$", content, flags=re.MULTILINE),
            ["What", "How", "Where", "When", "Why", "Who"],
        )

    def test_local_documentation_links_and_fragments_resolve(self):
        documents = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
        for document in documents:
            content = document.read_text(encoding="utf-8")
            for target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", content):
                url = urlsplit(target)
                if url.scheme or url.netloc:
                    continue
                with self.subTest(document=document.name, target=target):
                    path = document.parent / unquote(url.path) if url.path else document
                    self.assertTrue(path.is_file(), f"Missing link target: {path}")
                    if url.fragment and path.suffix == ".md":
                        headings = re.findall(
                            r"^#{1,6} (.+)$",
                            path.read_text(encoding="utf-8"),
                            flags=re.MULTILINE,
                        )
                        anchors = {
                            re.sub(r"[^\w-]", "", heading.lower().replace(" ", "-"))
                            for heading in headings
                        }
                        self.assertIn(unquote(url.fragment), anchors)

    def test_landing_page_embeds_four_nonempty_png_screens(self):
        content = (ROOT / "README.md").read_text(encoding="utf-8")
        images = re.findall(r"!\[([^\]]+)\]\(([^)]+)\)", content)
        self.assertEqual(len(images), 4)
        self.assertEqual(len({target for _, target in images}), 4)
        for alternative_text, target in images:
            with self.subTest(target=target):
                self.assertTrue(alternative_text.strip())
                self.assertTrue(target.startswith("docs/screenshots/"))
                image = (ROOT / target).read_bytes()
                self.assertEqual(image[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(image[12:16], b"IHDR")
                width, height = struct.unpack(">II", image[16:24])
                self.assertGreaterEqual(width, 1000)
                self.assertGreaterEqual(height, 700)
                self.assertGreater(len(image), 10_000)


if __name__ == "__main__":
    unittest.main()
