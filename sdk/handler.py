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

    def emit(self, record):
        try:
            payload = {
                "entries": [
                    {
                        "level": record.levelname.lower(),
                        "message": record.getMessage(),
                        "timestamp": record.created,
                        "module": record.module,
                        "function": record.funcName,
                        "request_id": getattr(
                            record,
                            "request_id",
                            None,
                        ),
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