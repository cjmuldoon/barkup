#!/usr/bin/env python3
"""Interactive OAuth2 setup to obtain a refresh token for Google SDM API.

Run this once locally to get the refresh token, then add it to .env on the server.

Usage:
    python scripts/setup_google_auth.py

Prerequisites:
    1. Create a project at https://console.cloud.google.com
    2. Enable the Smart Device Management API
    3. Create OAuth 2.0 credentials (Web application type)
    4. Add https://www.google.com as an authorized redirect URI
    5. Register at https://console.nest.google.com/device-access
       and link your Google Cloud project
"""

import os
import sys

# Add parent dir to path so we can import without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from google_auth_oauthlib.flow import InstalledAppFlow


SDM_SCOPE = "https://www.googleapis.com/auth/sdm.service"


def main():
    print("=" * 60)
    print("Barkup - Google OAuth2 Setup")
    print("=" * 60)

    client_id = input("\nEnter your Google OAuth Client ID: ").strip()
    client_secret = input("Enter your Google OAuth Client Secret: ").strip()

    if not client_id or not client_secret:
        print("Error: Client ID and secret are required.")
        sys.exit(1)

    # Get the SDM project ID for the authorization URL
    sdm_project_id = input("Enter your SDM Project ID: ").strip()

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(
        client_config,
        scopes=[SDM_SCOPE],
    )

    # Use the SDM-specific authorization URL
    flow.oauth2session.auth_url = (
        f"https://nestservices.google.com/partnerconnections/{sdm_project_id}/auth"
    )

    print("\nA browser window will open for Google authorization.")
    print("Grant access to your Nest devices.\n")

    creds = flow.run_local_server(port=8080)

    print("\n" + "=" * 60)
    print("SUCCESS! Add these to your .env file:")
    print("=" * 60)
    print(f"\nGOOGLE_CLIENT_ID={client_id}")
    print(f"GOOGLE_CLIENT_SECRET={client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print(f"SDM_PROJECT_ID={sdm_project_id}")
    print()


if __name__ == "__main__":
    main()
