"""Google Calendar integration tool.

Authentication flow:
  1. Set GOOGLE_CALENDAR_CLIENT_ID and GOOGLE_CALENDAR_CLIENT_SECRET as env vars
     (or Colab secrets). Get these from Google Cloud Console:
     https://console.cloud.google.com/ → APIs & Services → Credentials → OAuth 2.0 Client IDs
     (Application type: "Desktop app")

  2. Call `google_calendar_auth_url` to get the authorization URL.
     Visit the URL, authorize, and copy the authorization code.

  3. Call `google_calendar_auth_code` with the code to exchange it for tokens.
     Tokens are saved to Drive at memory/google_calendar_token.json.

  4. After that, `google_calendar_create_event` and `google_calendar_list_events`
     work automatically (tokens refresh automatically).

Alternative quick setup: Set GOOGLE_CALENDAR_REFRESH_TOKEN as an env var along with
CLIENT_ID and CLIENT_SECRET to skip steps 2-3.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
from datetime import datetime, timezone, timedelta
from typing import List, Optional

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_FILE = "memory/google_calendar_token.json"


def _get_token_path(ctx) -> pathlib.Path:
    return ctx.drive_root / TOKEN_FILE


def _load_credentials(ctx):
    """Load and return valid Google OAuth2 credentials, or (None, error_msg)."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        return None, "google-auth library not available"

    client_id = os.environ.get("GOOGLE_CALENDAR_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CALENDAR_CLIENT_SECRET", "").strip()

    token_path = _get_token_path(ctx)
    creds = None

    # Try loading from stored token file on Drive
    if token_path.exists():
        try:
            token_data = json.loads(token_path.read_text(encoding="utf-8"))
            creds = Credentials.from_authorized_user_info(token_data, SCOPES)
        except Exception as e:
            log.warning("Failed to load stored token: %s", e)

    # Fallback: build from GOOGLE_CALENDAR_REFRESH_TOKEN env var
    if creds is None:
        refresh_token = os.environ.get("GOOGLE_CALENDAR_REFRESH_TOKEN", "").strip()
        if refresh_token and client_id and client_secret:
            creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=SCOPES,
            )

    if creds is None:
        return None, (
            "No credentials found. Call google_calendar_auth_url first, "
            "or set GOOGLE_CALENDAR_REFRESH_TOKEN (+ CLIENT_ID + CLIENT_SECRET) env vars."
        )

    # Refresh if expired
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                _save_token(ctx, creds)
            except Exception as e:
                return None, f"Token refresh failed: {e}"
        else:
            return None, "Credentials invalid and cannot be refreshed. Re-authorize."

    return creds, None


def _save_token(ctx, creds) -> None:
    """Save credentials to Drive."""
    token_path = _get_token_path(ctx)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else SCOPES,
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    }
    token_path.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
    log.info("Saved Google Calendar token to %s", token_path)


def _calendar_auth_url(ctx, redirect_uri: str = "urn:ietf:wg:oauth:2.0:oob") -> str:
    """Generate the OAuth2 authorization URL.

    Args:
        redirect_uri: OAuth2 redirect URI. Default 'urn:ietf:wg:oauth:2.0:oob' for
                      copy-paste flow (code shown on page).
    """
    client_id = os.environ.get("GOOGLE_CALENDAR_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CALENDAR_CLIENT_SECRET", "").strip()

    if not client_id:
        return (
            "⚠️ GOOGLE_CALENDAR_CLIENT_ID not set.\n\n"
            "Setup steps:\n"
            "1. Go to https://console.cloud.google.com/\n"
            "2. Create/select a project, enable Google Calendar API\n"
            "3. APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID\n"
            "4. Application type: Desktop app\n"
            "5. Copy Client ID and Client Secret\n"
            "6. In Colab: os.environ['GOOGLE_CALENDAR_CLIENT_ID'] = '...'\n"
            "7. In Colab: os.environ['GOOGLE_CALENDAR_CLIENT_SECRET'] = '...'\n"
            "8. Call google_calendar_auth_url again"
        )
    if not client_secret:
        return "⚠️ GOOGLE_CALENDAR_CLIENT_SECRET not set."

    import urllib.parse
    scope_str = urllib.parse.quote(" ".join(SCOPES))
    redirect_encoded = urllib.parse.quote(redirect_uri, safe="")
    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_encoded}"
        f"&response_type=code"
        f"&scope={scope_str}"
        f"&access_type=offline"
        f"&prompt=consent"
    )

    return (
        "📋 **Authorization required for Google Calendar.**\n\n"
        f"1. Visit this URL in your browser:\n{auth_url}\n\n"
        "2. Log in with your Google account and click **Allow**.\n\n"
        "3. Copy the authorization code shown (or from the ?code= URL parameter).\n\n"
        "4. Call `google_calendar_auth_code` with that code to complete setup."
    )


def _calendar_auth_code(ctx, code: str, redirect_uri: str = "urn:ietf:wg:oauth:2.0:oob") -> str:
    """Exchange authorization code for OAuth2 tokens and save them.

    Args:
        code:         Authorization code from the consent page.
        redirect_uri: Must match the redirect_uri used in google_calendar_auth_url.
    """
    try:
        import requests as _requests
    except ImportError:
        return "⚠️ 'requests' library not available."

    client_id = os.environ.get("GOOGLE_CALENDAR_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CALENDAR_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        return "⚠️ GOOGLE_CALENDAR_CLIENT_ID and GOOGLE_CALENDAR_CLIENT_SECRET must be set."

    resp = _requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code.strip(),
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )

    if resp.status_code != 200:
        return f"⚠️ Token exchange failed (HTTP {resp.status_code}): {resp.text}"

    token_data = resp.json()

    if "error" in token_data:
        return (
            f"⚠️ Token exchange error: {token_data.get('error')}: "
            f"{token_data.get('error_description', '')}"
        )

    stored = {
        "token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": SCOPES,
        "expiry": None,
    }

    token_path = _get_token_path(ctx)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(json.dumps(stored, indent=2), encoding="utf-8")

    refresh_token = token_data.get("refresh_token", "(none)")
    return (
        "✅ Google Calendar authorization successful! Token saved to Drive.\n"
        "You can now use google_calendar_create_event and google_calendar_list_events.\n\n"
        f"💡 Tip: Save this refresh token as a Colab secret for future sessions:\n"
        f"   Secret name: GOOGLE_CALENDAR_REFRESH_TOKEN\n"
        f"   Value: {refresh_token}"
    )


def _parse_datetime(s: str) -> Optional[datetime]:
    """Parse ISO 8601 or simple datetime string to datetime object."""
    s = s.strip()
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _calendar_create_event(
    ctx,
    summary: str,
    start_time: str,
    end_time: str = "",
    description: str = "",
    location: str = "",
    attendees: str = "",
    calendar_id: str = "primary",
) -> str:
    """Create a Google Calendar event.

    Args:
        summary:     Event title.
        start_time:  ISO 8601 datetime, e.g. '2026-03-05T11:00:00'.
        end_time:    ISO 8601 datetime. Defaults to 1 hour after start.
        description: Event notes.
        location:    Event location.
        attendees:   Comma-separated email addresses to invite.
        calendar_id: Calendar ID (default: 'primary').
    """
    try:
        from googleapiclient.discovery import build
    except ImportError:
        return "⚠️ google-api-python-client not installed. Run: pip install google-api-python-client"

    creds, err = _load_credentials(ctx)
    if err:
        return f"⚠️ {err}"

    start_dt = _parse_datetime(start_time)
    if start_dt is None:
        return f"⚠️ Could not parse start_time: {start_time!r}. Use ISO 8601, e.g. '2026-03-05T11:00:00'."

    end_dt = _parse_datetime(end_time) if end_time else start_dt + timedelta(hours=1)
    if end_dt is None:
        return f"⚠️ Could not parse end_time: {end_time!r}."

    # Format as RFC3339 with UTC offset
    start_str = start_dt.strftime("%Y-%m-%dT%H:%M:%S") + "+00:00"
    end_str = end_dt.strftime("%Y-%m-%dT%H:%M:%S") + "+00:00"

    event_body: dict = {
        "summary": summary,
        "start": {"dateTime": start_str, "timeZone": "UTC"},
        "end": {"dateTime": end_str, "timeZone": "UTC"},
    }

    if description:
        event_body["description"] = description
    if location:
        event_body["location"] = location
    if attendees:
        event_body["attendees"] = [
            {"email": e.strip()} for e in attendees.split(",") if e.strip()
        ]

    try:
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        link = event.get("htmlLink", "")
        event_id = event.get("id", "")
        return (
            f"✅ Event created: **{summary}**\n"
            f"📅 {start_str} → {end_str} (UTC)\n"
            f"🔗 {link}\n"
            f"ID: {event_id}"
        )
    except Exception as e:
        log.warning("Failed to create calendar event", exc_info=True)
        return f"⚠️ Calendar API error: {e}"


def _calendar_list_events(
    ctx,
    max_results: int = 10,
    time_min: str = "",
    calendar_id: str = "primary",
) -> str:
    """List upcoming Google Calendar events.

    Args:
        max_results: Maximum events to return (default: 10).
        time_min:    Show events after this time (ISO 8601). Default: now.
        calendar_id: Calendar ID (default: 'primary').
    """
    try:
        from googleapiclient.discovery import build
    except ImportError:
        return "⚠️ google-api-python-client not installed."

    creds, err = _load_credentials(ctx)
    if err:
        return f"⚠️ {err}"

    if time_min:
        time_min_dt = _parse_datetime(time_min)
        time_min_str = (
            time_min_dt.strftime("%Y-%m-%dT%H:%M:%S+00:00") if time_min_dt else time_min
        )
    else:
        time_min_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    try:
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min_str,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        events = result.get("items", [])

        if not events:
            return "No upcoming events found."

        lines = [f"📅 {len(events)} upcoming events:"]
        for ev in events:
            start = ev["start"].get("dateTime", ev["start"].get("date", "?"))
            title = ev.get("summary", "(no title)")
            link = ev.get("htmlLink", "")
            lines.append(f"  • {start} — {title}  {link}")

        return "\n".join(lines)
    except Exception as e:
        log.warning("Failed to list calendar events", exc_info=True)
        return f"⚠️ Calendar API error: {e}"


def get_tools() -> List:
    from ouroboros.tools.registry import ToolEntry
    return [
        ToolEntry(
            "google_calendar_auth_url",
            {
                "name": "google_calendar_auth_url",
                "description": (
                    "Generate OAuth2 authorization URL for Google Calendar access. "
                    "Requires GOOGLE_CALENDAR_CLIENT_ID + GOOGLE_CALENDAR_CLIENT_SECRET env vars. "
                    "Returns a URL the user visits to grant access. "
                    "Then call google_calendar_auth_code with the resulting code."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "redirect_uri": {
                            "type": "string",
                            "description": (
                                "OAuth2 redirect URI. Default: 'urn:ietf:wg:oauth:2.0:oob' "
                                "(copy-paste flow — code is shown on the page)."
                            ),
                        },
                    },
                    "required": [],
                },
            },
            _calendar_auth_url,
            timeout_sec=30,
        ),
        ToolEntry(
            "google_calendar_auth_code",
            {
                "name": "google_calendar_auth_code",
                "description": (
                    "Exchange OAuth2 authorization code for access/refresh tokens. "
                    "Call after visiting the URL from google_calendar_auth_url. "
                    "Saves tokens to Drive for automatic future use."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "Authorization code from the OAuth2 consent page.",
                        },
                        "redirect_uri": {
                            "type": "string",
                            "description": "Must match redirect_uri used in google_calendar_auth_url.",
                        },
                    },
                    "required": ["code"],
                },
            },
            _calendar_auth_code,
            timeout_sec=30,
        ),
        ToolEntry(
            "google_calendar_create_event",
            {
                "name": "google_calendar_create_event",
                "description": (
                    "Create an event in Google Calendar. "
                    "Requires prior OAuth2 authorization (google_calendar_auth_url + google_calendar_auth_code) "
                    "or GOOGLE_CALENDAR_REFRESH_TOKEN env var."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "Event title/summary.",
                        },
                        "start_time": {
                            "type": "string",
                            "description": "Start time in ISO 8601 format, e.g. '2026-03-05T11:00:00'.",
                        },
                        "end_time": {
                            "type": "string",
                            "description": "End time in ISO 8601 format. Defaults to 1 hour after start.",
                        },
                        "description": {
                            "type": "string",
                            "description": "Event description/notes.",
                        },
                        "location": {
                            "type": "string",
                            "description": "Event location.",
                        },
                        "attendees": {
                            "type": "string",
                            "description": "Comma-separated email addresses to invite.",
                        },
                        "calendar_id": {
                            "type": "string",
                            "description": "Google Calendar ID (default: 'primary').",
                        },
                    },
                    "required": ["summary", "start_time"],
                },
            },
            _calendar_create_event,
            timeout_sec=60,
        ),
        ToolEntry(
            "google_calendar_list_events",
            {
                "name": "google_calendar_list_events",
                "description": (
                    "List upcoming events from Google Calendar. "
                    "Requires prior Google Calendar authorization."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "max_results": {
                            "type": "integer",
                            "description": "Max number of events to return (default: 10).",
                        },
                        "time_min": {
                            "type": "string",
                            "description": "Show events after this time (ISO 8601). Default: now.",
                        },
                        "calendar_id": {
                            "type": "string",
                            "description": "Google Calendar ID (default: 'primary').",
                        },
                    },
                    "required": [],
                },
            },
            _calendar_list_events,
            timeout_sec=60,
        ),
    ]
