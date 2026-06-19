"""Server-side Pendo Track Event client.

Sends track events to the Pendo Data API via HTTP POST.
Failures are logged but never break application flow.
"""

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

PENDO_TRACK_URL = "https://data.pendo.io/data/track"
PENDO_INTEGRATION_KEY = "5be38481-44dc-4c26-bfe4-2e4e9bdf4e4e"


async def track(
    event: str,
    visitor_id: str = "system",
    account_id: str = "system",
    properties: dict[str, Any] | None = None,
) -> None:
    """Fire a Pendo server-side track event.

    Args:
        event: Descriptive event name.
        visitor_id: Unique user identifier (use actual user ID when available).
        account_id: Unique account identifier (use actual account ID when available).
        properties: Optional metadata dict to attach to the event.
    """
    payload: dict[str, Any] = {
        "type": "track",
        "event": event,
        "visitorId": visitor_id,
        "accountId": account_id,
        "timestamp": int(time.time() * 1000),
    }
    if properties:
        payload["properties"] = properties

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                PENDO_TRACK_URL,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "x-pendo-integration-key": PENDO_INTEGRATION_KEY,
                },
            )
            if resp.status_code >= 400:
                logger.warning(
                    "Pendo track failed: event=%s status=%s body=%s",
                    event,
                    resp.status_code,
                    resp.text[:200],
                )
    except Exception:
        logger.warning("Pendo track request error: event=%s", event, exc_info=True)
