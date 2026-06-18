"""
Lycan SDK — synchronous log handler.

Mode 1: Standalone (manual service naming)
------------------------------------------
    import logging
    from lycan import RemoteLogHandler

    handler = RemoteLogHandler(api_key="sk_...")
    logging.root.addHandler(handler)

    payment_log = logging.getLogger("payment")
    payment_log.error("Gateway rejected", extra={"error_code": "ERR_PAYMENT_API"})

Mode 2: Plug-and-play with LycanMiddleware (route-based)
---------------------------------------------------------
    from lycan import RemoteLogHandler, LycanMiddleware

    logging.root.addHandler(RemoteLogHandler(api_key="sk_..."))

    app.add_middleware(LycanMiddleware, api_key="sk_...", services={
        "/payment": "payment",
        "/auth":    "auth",
    })

    # Any logger used during a /payment/* request is automatically tagged:
    #   service_label = "payment"
    #   request_id    = "<uuid>"   ← same for every log in that request

Resolution order in emit()
---------------------------
  service_label → ContextVar (middleware) → record.name (standalone)
  request_id    → ContextVar (middleware) → record.request_id attr (manual)
"""

import logging
import requests

from lycan.context import get_request_id, get_service_label


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
            # service_label: middleware ContextVar → logger name fallback
            service_label = get_service_label() or record.name

            # request_id: middleware ContextVar → manual extra attr fallback
            request_id = get_request_id() or getattr(record, "request_id", None)

            payload = {
                "entries": [
                    {
                        "level": record.levelname.lower(),
                        "message": record.getMessage(),
                        "timestamp": record.created,
                        "service_label": service_label,
                        "request_id": request_id,
                        "error_code": getattr(record, "error_code", None),
                        "module": record.module,
                        "function": record.funcName,
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
