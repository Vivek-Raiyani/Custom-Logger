"""
Lycan SDK

Exports
-------
RemoteLogHandler  — logging.Handler that ships logs to Lycan.
LycanMiddleware   — optional FastAPI middleware for route-based service labeling.

Quick start (standalone)
------------------------
    import logging
    from lycan import RemoteLogHandler

    logging.root.addHandler(RemoteLogHandler(api_key="sk_..."))

    payment = logging.getLogger("payment")
    payment.error("Gateway rejected", extra={"error_code": "ERR_PAYMENT_API"})

Quick start (middleware / plug-and-play)
-----------------------------------------
    import logging
    from fastapi import FastAPI
    from lycan import RemoteLogHandler, LycanMiddleware

    app = FastAPI()

    logging.root.setLevel(logging.DEBUG)
    logging.root.addHandler(RemoteLogHandler(api_key="sk_..."))

    app.add_middleware(
        LycanMiddleware,
        api_key="sk_...",
        services={
            "/payment": "payment",
            "/auth":    "auth",
            "/orders":  "orders",
        },
    )
"""

from lycan.handler import RemoteLogHandler
from lycan.middleware import LycanMiddleware

__all__ = ["RemoteLogHandler", "LycanMiddleware"]