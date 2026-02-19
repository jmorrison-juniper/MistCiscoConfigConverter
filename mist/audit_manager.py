"""
Mist Audit Log Management.

Handles querying the Mist audit log to verify changes.
"""

import logging
import time
from dataclasses import dataclass

import mistapi.api.v1.orgs.logs

from .connection import MistConnection


@dataclass
class AuditEntry:
    """Represents a single audit log entry."""
    
    id: str
    timestamp: int
    admin_name: str
    message: str
    site_id: str | None = None
    org_id: str | None = None
    
    @classmethod
    def from_api_response(cls, data: dict) -> "AuditEntry":
        """Create AuditEntry from API response dict."""
        return cls(
            id=data.get("id", ""),
            timestamp=data.get("timestamp", 0),
            admin_name=data.get("admin_name", ""),
            message=data.get("message", ""),
            site_id=data.get("site_id"),
            org_id=data.get("org_id")
        )


@dataclass
class ChangeVerification:
    """Result of verifying changes against audit log."""
    
    verified: bool
    expected_actions: list[str]
    found_actions: list[str]
    missing_actions: list[str]
    audit_entries: list[AuditEntry]
    message: str


class MistAuditManager:
    """Manages Mist audit log queries.
    
    Used to verify that changes were successfully applied by checking
    the audit log for expected entries.
    """
    
    def __init__(self, connection: MistConnection):
        """Initialize with MistConnection.
        
        Args:
            connection: MistConnection instance for API access
        """
        self.connection = connection
        self._logger = logging.getLogger(__name__)
    
    def get_recent_logs(
        self,
        site_id: str | None = None,
        duration: str = "5m",
        limit: int = 50
    ) -> list[AuditEntry]:
        """Get recent audit log entries.
        
        Args:
            site_id: Optional site ID to filter by
            duration: Time window (e.g., "5m", "1h", "1d")
            limit: Maximum entries to return
        
        Returns:
            List of AuditEntry objects, newest first.
        """
        session = self.connection.session
        if not session:
            self._logger.error("No Mist session available")
            return []
        
        try:
            # Query without site_id filter or sort - let API return recent entries
            response = mistapi.api.v1.orgs.logs.listOrgAuditLogs(
                session,
                self.connection.org_id,
                duration=duration,
                limit=limit
            )
            
            if response.status_code == 200:
                results = response.data.get("results", [])
                self._logger.debug(f"Raw audit log response: {len(results)} entries")
                # Filter by site_id client-side if specified
                if site_id:
                    results = [r for r in results if r.get("site_id") == site_id]
                entries = [AuditEntry.from_api_response(r) for r in results]
                self._logger.debug(f"Retrieved {len(entries)} audit log entries")
                return entries
            else:
                self._logger.error(f"Failed to get audit logs: {response.status_code}")
                return []
                
        except Exception as error:
            self._logger.error(f"Error getting audit logs: {error}")
            return []
    
    def verify_changes(
        self,
        site_id: str | None,
        site_name: str,
        expected_actions: list[str],
        wait_seconds: int = 2,
        max_retries: int = 3
    ) -> ChangeVerification:
        """Verify that expected changes appear in the audit log.
        
        Args:
            site_id: Site ID that was modified (optional - if None, queries all org logs)
            site_name: Site name for matching
            expected_actions: List of action keywords to look for (e.g., ["Add Site", "Update Site"])
            wait_seconds: Seconds to wait between retries for log propagation
            max_retries: Maximum retry attempts
        
        Returns:
            ChangeVerification with results.
        """
        found_actions = []
        missing_actions = list(expected_actions)
        all_entries = []
        
        for attempt in range(max_retries):
            if attempt > 0:
                self._logger.debug(f"Verification attempt {attempt + 1}/{max_retries}")
                time.sleep(wait_seconds)
            
            entries = self.get_recent_logs(site_id=site_id, duration="2m", limit=20)
            all_entries = entries
            
            # Check each expected action against audit log messages
            for action in list(missing_actions):
                for entry in entries:
                    # Check if action keyword appears in the audit message
                    # Common patterns: "Created Site", "Updated Site", "Created Gateway Template"
                    if action.lower() in entry.message.lower():
                        if action not in found_actions:
                            found_actions.append(action)
                        if action in missing_actions:
                            missing_actions.remove(action)
                        break
            
            # If all actions found, we're done
            if not missing_actions:
                break
        
        verified = len(missing_actions) == 0
        
        if verified:
            message = f"All {len(expected_actions)} expected changes verified in audit log"
        else:
            message = f"Found {len(found_actions)}/{len(expected_actions)} expected changes. Missing: {missing_actions}"
        
        self._logger.info(f"Change verification: {message}")
        
        return ChangeVerification(
            verified=verified,
            expected_actions=expected_actions,
            found_actions=found_actions,
            missing_actions=missing_actions,
            audit_entries=all_entries,
            message=message
        )
    
    def verify_site_creation(self, site_id: str, site_name: str) -> ChangeVerification:
        """Verify site was created.
        
        Args:
            site_id: ID of created site
            site_name: Name of created site
        
        Returns:
            ChangeVerification result.
        """
        return self.verify_changes(
            site_id=site_id,
            site_name=site_name,
            expected_actions=["Create Site"]
        )
    
    def verify_site_update(self, site_id: str, site_name: str) -> ChangeVerification:
        """Verify site was updated.
        
        Args:
            site_id: ID of updated site
            site_name: Name of updated site
        
        Returns:
            ChangeVerification result.
        """
        return self.verify_changes(
            site_id=site_id,
            site_name=site_name,
            expected_actions=["Update Site"]
        )
    
    def verify_template_creation(self, site_id: str, site_name: str) -> ChangeVerification:
        """Verify gateway template was created.
        
        Args:
            site_id: Related site ID
            site_name: Related site name
        
        Returns:
            ChangeVerification result.
        """
        return self.verify_changes(
            site_id=site_id,
            site_name=site_name,
            expected_actions=["Created Gateway Template"]
        )
    
    def verify_profile_creation(self, site_id: str, site_name: str) -> ChangeVerification:
        """Verify device profile was created.
        
        Args:
            site_id: Related site ID
            site_name: Related site name
        
        Returns:
            ChangeVerification result.
        """
        return self.verify_changes(
            site_id=site_id,
            site_name=site_name,
            expected_actions=["Created Device Profile"]
        )
