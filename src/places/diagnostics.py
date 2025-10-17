"""Diagnostics helpers for Google Places tooling."""

from __future__ import annotations

from typing import Optional

__all__ = ["permission_hint"]


def permission_hint(details: str) -> Optional[str]:
    """Return a remediation hint for common permission error messages."""

    message = details.lower()

    if "are blocked" in message or "blocked" in message:
        return (
            "Check the Google Cloud project: enable Places API (New) and allow the API key to call "
            "https://places.googleapis.com in its API restrictions."
        )

    if "api key" in message and "invalid" in message:
        return (
            "The configured GOOGLE_ACCESS_ID does not look like a valid Maps API key. "
            "Provide a key that starts with 'AIza' or update the secret."
        )

    if "ip" in message and "not authorized" in message:
        return "Update the API key restrictions to allow requests from GitHub Actions IP ranges."

    if "service has been disabled" in message or "api has not been used" in message:
        return "Enable Places API (New) in the Google Cloud console for the project tied to the API key."

    return None
