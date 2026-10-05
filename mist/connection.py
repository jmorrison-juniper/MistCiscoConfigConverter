"""
Mist API Connection Management.

Handles API session creation and connectivity verification.
"""

import logging
import os

import mistapi
from requests.adapters import HTTPAdapter


# Default timeout for Mist API requests (connect timeout, read timeout)
# Loose timeout (2 min) to handle slow API responses while preventing indefinite hangs
DEFAULT_TIMEOUT = (30, 120)

CONNECTION_FAILURE_MESSAGE = "Mist API connection check failed. See the server log for details."  # Safe text for API clients.


class TimeoutHTTPAdapter(HTTPAdapter):
    """HTTP adapter that forces timeout on all requests.

    Uses forced override (not setdefault) to ensure timeout is always
    applied even if the calling code passes timeout=None.
    """

    def __init__(self, *args, timeout=DEFAULT_TIMEOUT, **kwargs):
        self.timeout = timeout
        super().__init__(*args, **kwargs)

    def send(self, request, **kwargs):
        """Send request with forced timeout to prevent indefinite hangs."""
        # Force override: if timeout is None or missing, apply our default.
        # This prevents upstream code from accidentally passing timeout=None.
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self.timeout
        return super().send(request, **kwargs)


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
                # Mount timeout adapter to prevent indefinite hangs on API calls
                timeout_adapter = TimeoutHTTPAdapter()
                self._session._session.mount("https://", timeout_adapter)
                self._session._session.mount("http://", timeout_adapter)
                # Verify adapter is actually mounted on the requests session
                active_adapter = self._session._session.get_adapter("https://")
                adapter_timeout = getattr(active_adapter, "timeout", "NOT SET")
                self._logger.info(
                    f"Mist API session initialized "
                    f"(timeout adapter: {type(active_adapter).__name__}, "
                    f"timeout: {adapter_timeout})"
                )
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
            result["error"] = CONNECTION_FAILURE_MESSAGE  # Give the client no exception text.
            self._logger.exception(f"Mist API connection check failed: {error}")  # Keep the traceback in the server log.
        
        return result
    
    @property
    def is_configured(self) -> bool:
        """Check if API credentials are configured."""
        return bool(self.api_token and self.org_id)
