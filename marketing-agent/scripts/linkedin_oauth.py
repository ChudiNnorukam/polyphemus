#!/usr/bin/env python3
"""LinkedIn OAuth 2.0 Authorization Code Flow.

Usage:
    1. Add http://localhost:8080/callback as redirect URL in LinkedIn app settings
    2. Run: python3 scripts/linkedin_oauth.py
    3. Browser opens -> authorize -> token is saved to .env
"""

import http.server
import json
import os
import sys
import urllib.parse
import webbrowser
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# Load .env file
ENV_FILE = Path(__file__).parent.parent / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

# LinkedIn App Credentials
CLIENT_ID = os.environ.get("LINKEDIN_CLIENT_ID", "86a2lm1nvzmrbp")
CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET", "")

REDIRECT_URI = "http://localhost:8080/callback"
SCOPES = "openid profile w_member_social email"
AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"

ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """Handle the OAuth callback and exchange code for token."""

    auth_code = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            OAuthCallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>Authorization successful!</h1>"
                b"<p>You can close this tab and return to the terminal.</p></body></html>"
            )
        elif "error" in params:
            error = params.get("error", ["unknown"])[0]
            desc = params.get("error_description", [""])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                f"<html><body><h1>Error: {error}</h1><p>{desc}</p></body></html>".encode()
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress logs


def exchange_code_for_token(code: str) -> dict:
    """Exchange authorization code for access token."""
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    return resp.json()


def get_user_info(access_token: str) -> dict:
    """Get LinkedIn user info (includes sub = person ID)."""
    resp = requests.get(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    resp.raise_for_status()
    return resp.json()


def update_env(token: str, person_urn: str, expires_at: str):
    """Update .env file with LinkedIn credentials."""
    env_path = os.path.abspath(ENV_PATH)
    lines = []
    if os.path.exists(env_path):
        with open(env_path) as f:
            lines = f.readlines()

    # Keys to set
    updates = {
        "LINKEDIN_ACCESS_TOKEN": token,
        "LINKEDIN_PERSON_URN": person_urn,
        "LINKEDIN_TOKEN_EXPIRES_AT": expires_at,
    }

    # Update existing or append
    existing_keys = set()
    new_lines = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}\n")
            existing_keys.add(key)
        else:
            new_lines.append(line)

    for key, value in updates.items():
        if key not in existing_keys:
            new_lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(new_lines)

    print(f"\n✓ Updated {env_path}")


def main():
    if not CLIENT_SECRET:
        print("ERROR: Set LINKEDIN_CLIENT_SECRET environment variable first.")
        print("Find it at: https://www.linkedin.com/developers/apps/231079653/auth")
        print()
        print("Usage:")
        print("  LINKEDIN_CLIENT_SECRET='your_secret' python3 scripts/linkedin_oauth.py")
        sys.exit(1)

    # Build authorization URL
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": "linkedin_oauth_chudi",
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    print("=" * 60)
    print("LinkedIn OAuth 2.0 Flow")
    print("=" * 60)
    print()
    print("Opening browser for authorization...")
    print(f"If browser doesn't open, visit:\n{auth_url}")
    print()

    # Start local server
    server = http.server.HTTPServer(("localhost", 8080), OAuthCallbackHandler)
    webbrowser.open(auth_url)

    print("Waiting for callback on http://localhost:8080/callback ...")

    # Wait for the callback
    while OAuthCallbackHandler.auth_code is None:
        server.handle_request()

    code = OAuthCallbackHandler.auth_code
    server.server_close()
    print(f"\n✓ Got authorization code: {code[:10]}...")

    # Exchange for token
    print("\nExchanging code for access token...")
    token_data = exchange_code_for_token(code)
    access_token = token_data["access_token"]
    expires_in = token_data.get("expires_in", 5184000)  # Default 60 days
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    ).isoformat()

    print(f"✓ Access token obtained (expires in {expires_in // 86400} days)")

    # Get user info
    print("\nFetching user profile...")
    user_info = get_user_info(access_token)
    sub = user_info.get("sub", "")
    name = user_info.get("name", "Unknown")
    email = user_info.get("email", "")
    person_urn = f"urn:li:person:{sub}"

    print(f"✓ Authenticated as: {name} ({email})")
    print(f"  Person URN: {person_urn}")

    # Save to .env
    update_env(access_token, person_urn, expires_at)

    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Name:       {name}")
    print(f"  Email:      {email}")
    print(f"  Person URN: {person_urn}")
    print(f"  Token:      {access_token[:20]}...")
    print(f"  Expires:    {expires_at}")
    print()
    print("LinkedIn OAuth setup complete! You can now use publisher.py.")
    print()

    # Print token data for reference
    print("Full token response:")
    print(json.dumps(token_data, indent=2))


if __name__ == "__main__":
    main()
