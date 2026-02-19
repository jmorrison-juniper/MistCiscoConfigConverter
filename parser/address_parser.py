"""
Address Parser for SNMP Location Strings

Parses SNMP location strings to extract address components for Mist site creation.
Supports US address formats with optional room/location suffixes.
"""

import re
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# US state abbreviations to full names
US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
    "PR": "Puerto Rico", "VI": "Virgin Islands", "GU": "Guam"
}

# US state abbreviation to timezone mapping (primary timezone for each state)
US_STATE_TIMEZONES = {
    "AL": "America/Chicago", "AK": "America/Anchorage", "AZ": "America/Phoenix",
    "AR": "America/Chicago", "CA": "America/Los_Angeles", "CO": "America/Denver",
    "CT": "America/New_York", "DE": "America/New_York", "FL": "America/New_York",
    "GA": "America/New_York", "HI": "Pacific/Honolulu", "ID": "America/Boise",
    "IL": "America/Chicago", "IN": "America/Indiana/Indianapolis", "IA": "America/Chicago",
    "KS": "America/Chicago", "KY": "America/New_York", "LA": "America/Chicago",
    "ME": "America/New_York", "MD": "America/New_York", "MA": "America/New_York",
    "MI": "America/Detroit", "MN": "America/Chicago", "MS": "America/Chicago",
    "MO": "America/Chicago", "MT": "America/Denver", "NE": "America/Chicago",
    "NV": "America/Los_Angeles", "NH": "America/New_York", "NJ": "America/New_York",
    "NM": "America/Denver", "NY": "America/New_York", "NC": "America/New_York",
    "ND": "America/Chicago", "OH": "America/New_York", "OK": "America/Chicago",
    "OR": "America/Los_Angeles", "PA": "America/New_York", "RI": "America/New_York",
    "SC": "America/New_York", "SD": "America/Chicago", "TN": "America/Chicago",
    "TX": "America/Chicago", "UT": "America/Denver", "VT": "America/New_York",
    "VA": "America/New_York", "WA": "America/Los_Angeles", "WV": "America/New_York",
    "WI": "America/Chicago", "WY": "America/Denver", "DC": "America/New_York",
    "PR": "America/Puerto_Rico", "VI": "America/Virgin", "GU": "Pacific/Guam"
}


@dataclass
class ParsedAddress:
    """Parsed address components."""
    raw: str = ""
    street: str = ""
    city: str = ""
    state: str = ""
    state_code: str = ""
    zip_code: str = ""
    country_code: str = ""
    timezone: str = ""
    room_info: str = ""
    contact: str = ""
    
    def to_mist_format(self) -> dict:
        """Convert to Mist site API format."""
        result = {}
        
        # Build full address string for Mist
        address_parts = []
        if self.street:
            address_parts.append(self.street)
        if self.city:
            address_parts.append(self.city)
        if self.state_code:
            address_parts.append(self.state_code)
        if self.zip_code:
            address_parts.append(self.zip_code)
        
        if address_parts:
            result["address"] = ", ".join(address_parts)
        
        if self.country_code:
            result["country_code"] = self.country_code
        
        if self.timezone:
            result["timezone"] = self.timezone
        
        # Build notes from contact and room_info
        notes_parts = []
        if self.contact:
            notes_parts.append(f"Contact: {self.contact}")
        if self.room_info:
            notes_parts.append(f"Location: {self.room_info}")
        if notes_parts:
            result["notes"] = "\n".join(notes_parts)
        
        return result


def parse_snmp_location(location: str) -> ParsedAddress:
    """
    Parse SNMP location string into address components.
    
    Handles formats like:
    - "2330 Preston Rd, Frisco, TX : Telco Room"
    - "123 Main St, Austin, TX 78701"
    - "Building A, Floor 2, New York, NY"
    - "1601 S. Deanza Blvd., Cupertino, CA, 95014"
    
    Args:
        location: Raw SNMP location string
        
    Returns:
        ParsedAddress with extracted components
    """
    if not location:
        return ParsedAddress()
    
    parsed = ParsedAddress(raw=location)
    
    # Split off room/building info after colon or dash
    address_part = location
    room_part = ""
    
    for separator in [" : ", " - ", " -- "]:
        if separator in location:
            parts = location.split(separator, 1)
            address_part = parts[0].strip()
            room_part = parts[1].strip() if len(parts) > 1 else ""
            break
    
    parsed.room_info = room_part
    
    # Try to extract ZIP code (5 digits or 5+4 format)
    zip_match = re.search(r'\b(\d{5}(?:-\d{4})?)\b', address_part)
    if zip_match:
        parsed.zip_code = zip_match.group(1)
        # Remove zip from address for further parsing
        address_part = address_part.replace(zip_match.group(0), "").strip(" ,")
    
    # Try to extract state code (2-letter abbreviation)
    state_match = re.search(r'\b([A-Z]{2})\b', address_part)
    if state_match:
        potential_state = state_match.group(1)
        if potential_state in US_STATES:
            parsed.state_code = potential_state
            parsed.state = US_STATES[potential_state]
            parsed.country_code = "US"
            parsed.timezone = US_STATE_TIMEZONES.get(potential_state, "America/New_York")
    
    # Split remaining address by commas
    parts = [p.strip() for p in address_part.split(",") if p.strip()]
    
    if len(parts) >= 3:
        # Format: "Street, City, State" or "Street, City, State ZIP"
        parsed.street = parts[0]
        parsed.city = parts[1]
        # State may be in parts[2] - extract if not already found
        if not parsed.state_code:
            state_in_part = re.search(r'\b([A-Z]{2})\b', parts[2])
            if state_in_part and state_in_part.group(1) in US_STATES:
                parsed.state_code = state_in_part.group(1)
                parsed.state = US_STATES[parsed.state_code]
                parsed.country_code = "US"
                parsed.timezone = US_STATE_TIMEZONES.get(parsed.state_code, "America/New_York")
    elif len(parts) == 2:
        # Could be "City, State" or "Street, City"
        # Check if second part contains a state
        state_in_part = re.search(r'\b([A-Z]{2})\b', parts[1])
        if state_in_part and state_in_part.group(1) in US_STATES:
            parsed.city = parts[0]
            parsed.state_code = state_in_part.group(1)
            parsed.state = US_STATES[parsed.state_code]
            parsed.country_code = "US"
            parsed.timezone = US_STATE_TIMEZONES.get(parsed.state_code, "America/New_York")
        else:
            parsed.street = parts[0]
            parsed.city = parts[1]
    elif len(parts) == 1:
        # Single part - could be just city or full address without commas
        parsed.street = parts[0]
    
    # Clean up city name (remove state code if it got included)
    if parsed.city and parsed.state_code:
        parsed.city = re.sub(r'\s*' + parsed.state_code + r'\s*', '', parsed.city).strip()
    
    # Default to US if we found a valid state
    if parsed.state_code and not parsed.country_code:
        parsed.country_code = "US"
    
    logger.debug(f"Parsed SNMP location '{location}' -> street='{parsed.street}', "
                 f"city='{parsed.city}', state='{parsed.state_code}', zip='{parsed.zip_code}'")
    
    return parsed
