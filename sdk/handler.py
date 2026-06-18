"""
Lycan SDK — synchronous log handler.

Setup (once, at app startup / middleware level)
-----------------------------------------------
    import logging
    from lycan import RemoteLogHandler

    handler = RemoteLogHandler(api_key="sk_...")
    logging.root.addHandler(handler)

That's it. Every logger in the process is now captured automatically.

Service identification
----------------------
service_label is derived from record.name — the name passed to
logging.getLogger(). So each service just names its logger correctly
and the handler takes care of the rest. No per-service config needed.

    # payment/service.py
    logger = logging.getLogger("payment")
    logger.error("Gateway rejected", extra={"error_code": "ERR_PAYMENT_API"})
    #  → service_label = "payment", error_code = "ERR_PAYMENT_API"

    # auth/service.py
    logger = logging.getLogger("auth")
    logger.error("Token expired", extra={"error_code": "ERR_TOKEN_EXPIRED"})
    #  → service_label = "auth", error_code = "ERR_TOKEN_EXPIRED"

    # db/pool.py
    logger = logging.getLogger("db")
    logger.error("Connection pool exhausted", extra={"error_code": "ERR_DB_CONN"})
    #  → service_label = "db", error_code = "ERR_DB_CONN"

For dotted logger names (e.g. "app.routers.payment") the full name is
sent as-is so alert rules can match as broadly or narrowly as needed.
e.g. match service_label = "app.routers.payment" for a specific router,
or use a message/error_code rule to catch across all services.

Alert matching via error_code
------------------------------
Pass error_code in the extra dict — standard Python logging pattern:

    logger.error("msg", extra={"error_code": "ERR_X"})

Two logs with different error_codes always produce different fingerprints,
so "payment API failure" and "payment DB failure" are never conflated
even if both come from the same logger.
"""

import logging
import requests


class RemoteLogHandler(logging.Handler):

    def __init__(
        self,
        api_key: str,
        timeout: int = 2,
    ):
        super().__init__()

        self.timeout = timeout
        self.api_url = "http://127.0.0.1:8000/logs"

        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = {
                "entries": [
                    {
                        "level": record.levelname.lower(),
                        "message": record.getMessage(),
                        "timestamp": record.created,
                        # record.name is the logger name — "payment", "auth", "db", etc.
                        # For dotted names like "app.routers.payment", the full name is sent.
                        "service_label": record.name,
                        # Set via: logger.error("msg", extra={"error_code": "ERR_X"})
                        "error_code": getattr(record, "error_code", None),
                        "module": record.module,
                        "function": record.funcName,
                        "request_id": getattr(record, "request_id", None),
                        "extra": {},
                    }
                ]
            }

            response = requests.post(
                self.api_url,
                json=payload,
                headers=self.headers,
                timeout=self.timeout,
            )
            response.raise_for_status()

        except Exception:
            self.handleError(record)