import logging

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from barkup.config import settings

logger = logging.getLogger(__name__)

TOKEN_URI = "https://oauth2.googleapis.com/token"
SDM_SCOPE = "https://www.googleapis.com/auth/sdm.service"


def get_credentials() -> Credentials:
    """Build OAuth2 credentials from refresh token and auto-refresh if expired."""
    creds = Credentials(
        token=None,
        refresh_token=settings.google_refresh_token,
        token_uri=TOKEN_URI,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=[SDM_SCOPE],
    )
    creds.refresh(Request())
    return creds


def get_access_token() -> str:
    """Return a valid access token, refreshing if needed."""
    creds = get_credentials()
    if not creds.valid:
        creds.refresh(Request())
    return creds.token
