"""
Mist API Connection Management.

Handles API session creation and connectivity verification.
"""

import logging
import os

import mistapi


class MistConnection:
    """Manages Mist API session and connectivity.
    
    Provides lazy initialization of API session and methods to verify
    connectivity to the Mist cloud.
    
    Attributes:
        api_token: Mist API token from environment
        org_id: Mist organization ID from environment
        host: Mist API host (default: api.mist.com)
    """
    
    def __init__(
        self,
        api_token: str | None = None,
        org_id: str | None = None,
        host: str | None = None
    ):
        """Initialize connection with credentials.
        
        Args:
            api_token: Mist API token (defaults to MIST_APITOKEN env var)
            org_id: Mist org ID (defaults to org_id env var)
            host: API host (defaults to MIST_HOST env var or api.mist.com)
        """
        self.api_token = api_token or os.environ.get("MIST_APITOKEN", "")
        self.org_id = org_id or os.environ.get("org_id", "")
        self.host = host or os.environ.get("MIST_HOST", "api.mist.com")
        self._session = None
        self._logger = logging.getLogger(__name__)
    
    @property
    def session(self) -> mistapi.APISession | None:
        """Get or create Mist API session (lazy initialization).
        
        Returns:
            APISession if successful, None if initialization fails.
        """
        if self._session is None and self.api_token:
            try:
                self._session = mistapi.APISession(
                    host=self.host,
                    apitoken=self.api_token
                )
                self._logger.info("Mist API session initialized")
            except Exception as error:
                self._logger.error(f"Failed to initialize Mist API session: {error}")
                self._session = None
        return self._session
    
    def check_connection(self) -> dict:
        """Check Mist API connectivity and return status info.
        
        Returns:
            Dict with keys:
                - connected: bool
                - org_name: str or None
                - org_id: str
                - api_configured: bool
                - error: str or None
        """
        result = {
            "connected": False,
            "org_name": None,
            "org_id": self.org_id,
            "api_configured": bool(self.api_token and self.org_id),
            "error": None
        }
        
        if not self.api_token:
            result["error"] = "MIST_APITOKEN not configured"
            return result
        
        if not self.org_id:
            result["error"] = "org_id not configured"
            return result
        
        session = self.session
        if not session:
            result["error"] = "Failed to create API session"
            return result
        
        try:
            response = mistapi.api.v1.orgs.orgs.getOrg(session, self.org_id)
            if response.status_code == 200:
                org_data = response.data
                result["connected"] = True
                result["org_name"] = org_data.get("name", "Unknown")
                self._logger.debug(f"Mist API connected: {result['org_name']}")
            else:
                result["error"] = f"API returned status {response.status_code}"
        except Exception as error:
            result["error"] = str(error)
            self._logger.error(f"Mist API connection check failed: {error}")
        
        return result
    
    @property
    def is_configured(self) -> bool:
        """Check if API credentials are configured."""
        return bool(self.api_token and self.org_id)
