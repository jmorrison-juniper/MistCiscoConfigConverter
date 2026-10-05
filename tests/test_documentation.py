"""Regression checks for the landing page and its local documentation."""

import re
import struct
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
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
