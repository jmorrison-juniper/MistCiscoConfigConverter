"""
Cisco Config Parser Module

Parses Cisco IOS/IOS-XE configurations into structured sections
with extracted variables for conversion to Juniper Mist format.
"""

from parser.cisco_parser import CiscoConfigParser

__all__ = ["CiscoConfigParser"]
