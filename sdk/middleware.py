"""
Lycan SDK — FastAPI middleware for plug-and-play service identification.

How it works
------------
At request start, the middleware matches the incoming URL path against the
user-provided ``services`` prefix map and sets a ContextVar with the resolved
service label. RemoteLogHandler.emit() reads that ContextVar — so every log
line emitted during the request is automatically tagged with the right
service_label, regardless of which logger was used.

At request end, the ContextVar is reset so there is zero bleed between requests.

Setup
-----
    import logging
    from fastapi import FastAPI
    from lycan import RemoteLogHandler, LycanMiddleware

    app = FastAPI()

    # 1. Add the handler once to the root logger
    logging.root.setLevel(logging.DEBUG)
    logging.root.addHandler(RemoteLogHandler(api_key="sk_..."))

    # 2. Add the middleware with your prefix → service name map
    app.add_middleware(
        LycanMiddleware,
        api_key="sk_...",        # same key, used for request metadata logs
        services={
            "/payment": "payment",
            "/auth":    "auth",
            "/orders":  "orders",
            "/db":      "db",
        },
    )

Prefix matching
---------------
Longest prefix wins, so:
    "/payment/refund"  → matches "/payment"  → service = "payment"
    "/auth/login"      → matches "/auth"     → service = "auth"
    "/unknown/path"    → no match            → service = "unknown" (path[1] segment)

Routes that don't match any prefix fall back to the first path segment,
so you never lose a log — it just gets a best-effort label.

What gets logged automatically
-------------------------------
At the END of every request the middleware logs one structured entry:
    {
        "level": "info" | "error",
        "message": "POST /payment/charge 200 (143ms)",
        "service_label": "payment",
        "extra": {"method": "POST", "path": "/payment/charge",
                  "status_code": 200, "duration_ms": 143}
    }

This gives you request-level visibility without any extra logger setup
in your route handlers.
"""

import logging
import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from lycan.context import get_service_label, reset_service_label, set_service_label

logger = logging.getLogger("lycan.middleware")


class LycanMiddleware(BaseHTTPMiddleware):

    def __init__(
        self,
        app,
        api_key: str,
        services: dict[str, str],
    ):
        """
        Parameters
        ----------
        app
            The FastAPI/Starlette app instance (passed automatically by add_middleware).
        api_key
            Your Lycan project API key — same one used for RemoteLogHandler.
        services
            Mapping of URL path prefixes to service names.
            e.g. {"/payment": "payment", "/auth": "auth"}
            Longest prefix wins on overlap.
        """
        super().__init__(app)
        self.api_key = api_key
        # Sort by prefix length descending so longest match wins
        self._prefix_map: list[tuple[str, str]] = sorted(
            services.items(), key=lambda x: len(x[0]), reverse=True
        )

    def _resolve_service(self, path: str) -> str:
        """
        Match path against prefix map.
        Falls back to first path segment (e.g. "/payment/charge" → "payment").
        """
        for prefix, service_name in self._prefix_map:
            if path == prefix or path.startswith(prefix + "/"):
                return service_name

        # Fallback — use first segment of path
        parts = path.strip("/").split("/")
        return parts[0] if parts and parts[0] else "unknown"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        service = self._resolve_service(path)

        # Set ContextVar — all logs during this request will carry this label
        token = set_service_label(service)

        start = time.perf_counter()
        status_code = 500  # default in case of unhandled exception

        try:
            response: Response = await call_next(request)
            status_code = response.status_code
            return response

        finally:
            duration_ms = round((time.perf_counter() - start) * 1000)

            # Emit one request-summary log line automatically
            level = logging.ERROR if status_code >= 500 else logging.INFO
            logger.log(
                level,
                f"{request.method} {path} {status_code} ({duration_ms}ms)",
                extra={
                    "error_code": f"HTTP_{status_code}" if status_code >= 400 else None,
                    "request_id": request.headers.get("x-request-id"),
                },
            )

            # Always reset the ContextVar — even if the handler raised
            reset_service_label(token)