"""Tests that parser text from a configuration file cannot control or stall a regex.

These tests cover the CodeQL rules py/regex-injection and py/polynomial-redos.
They prove that the parsers keep their results and finish quickly on hostile input.
"""

import time
import unittest

from parser.address_parser import parse_snmp_location
from parser.cisco_parser import CiscoConfigParser

HOSTILE_DIGIT_COUNT = 200_000  # Long enough to make a quadratic scan take many seconds.
TIME_LIMIT_SECONDS = 1.0  # A linear scan of the hostile line finishes well inside this limit.


class ConfigSizeParseTests(unittest.TestCase):
    """Check the "Current configuration" size parse for its result and its speed."""

    def parse_system(self, line: str) -> CiscoConfigParser:
        """Parse one configuration line and return the parser with its system data."""
        parser = CiscoConfigParser(line + "\n")  # Build a parser for the one line.
        parser._parse_system_info()  # Run only the system section that reads the size.
        return parser

    def test_reads_size_in_bytes(self) -> None:
        """A normal size line sets the configuration size."""
        parser = self.parse_system("Current configuration : 12345 bytes")  # Parse a normal IOS header.
        self.assertEqual(parser.system.config_size_bytes, 12345)  # The size must match the header.

    def test_ignores_line_without_byte_count(self) -> None:
        """A size line with no number keeps the default size."""
        parser = self.parse_system("Current configuration : unknown")  # Parse a header with no number.
        self.assertEqual(parser.system.config_size_bytes, 0)  # The default size must stay.

    def test_hostile_digits_parse_quickly(self) -> None:
        """A long run of digits with no "bytes" word does not stall the parse."""
        line = "Current configuration : " + "9" * HOSTILE_DIGIT_COUNT + " x"  # Build the CodeQL attack string.
        started = time.perf_counter()  # Start the clock before the parse.
        parser = self.parse_system(line)  # Parse the hostile line.
        elapsed = time.perf_counter() - started  # Measure the parse time.
        self.assertLess(elapsed, TIME_LIMIT_SECONDS)  # The parse must be linear.
        self.assertEqual(parser.system.config_size_bytes, 0)  # The hostile line sets no size.


class CityCleanupTests(unittest.TestCase):
    """Check that the state code removal from a city name uses no regex built from input."""

    def test_removes_state_code_word(self) -> None:
        """A state code that follows the city name is removed."""
        parsed = parse_snmp_location("1 Main St, Austin TX, TX 78701")  # Parse an address with the code in the city.
        self.assertEqual(parsed.state_code, "TX")  # The state code must be found.
        self.assertEqual(parsed.city, "Austin")  # The city must lose the state code.

    def test_keeps_city_that_contains_state_letters(self) -> None:
        """A state code inside a longer word stays in the city name."""
        parsed = parse_snmp_location("1 Main St, CAMBRIDGE, CA 02139")  # Parse a city that holds the letters CA.
        self.assertEqual(parsed.state_code, "CA")  # The state code must be found.
        self.assertEqual(parsed.city, "CAMBRIDGE")  # The city name must stay whole.

    def test_city_with_regex_characters_is_literal(self) -> None:
        """Regex characters in a city name stay as plain text."""
        parsed = parse_snmp_location("1 Main St, Town (.*+) NY, NY 10001")  # Parse a city with regex symbols.
        self.assertEqual(parsed.city, "Town (.*+)")  # The symbols must stay and the code must go.


if __name__ == "__main__":
    unittest.main()
