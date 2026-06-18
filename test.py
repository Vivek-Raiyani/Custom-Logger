"""
Lycan SDK — test script demonstrating both usage modes.

Mode 1: Standalone  — logger name = service_label
Mode 2: Middleware  — route prefix = service_label (simulated below)
"""

import atexit
import logging
import queue
import threading
import time

import requests

from lycan.context import get_service_label, reset_service_label, set_service_label


# ---------------------------------------------------------------------------
# Async-batching handler (same as before, updated emit for contextvar)
# ---------------------------------------------------------------------------

class RemoteLogHandler(logging.Handler):
    def __init__(
        self,
        api_key: str,
        timeout: int = 2,
        batch_size: int = 10,
        flush_interval: float = 2.0,
    ):
        super().__init__()

        self.api_url = "http://127.0.0.1:8000/logs"
        self.timeout = timeout
        self.batch_size = batch_size
        self.flush_interval = flush_interval

        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        self.session = requests.Session()
        self.queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()

        self.worker = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker.start()

        atexit.register(self.close)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # 1. Middleware ContextVar (set per-request by LycanMiddleware)
            # 2. Fallback to record.name (logger name, standalone mode)
            service_label = get_service_label() or record.name

            log_entry = {
                "level": record.levelname.lower(),
                "message": record.getMessage(),
                "timestamp": record.created,
                "service_label": service_label,
                "error_code": getattr(record, "error_code", None),
                "module": record.module,
                "function": record.funcName,
                "request_id": getattr(record, "request_id", None),
                "extra": {"sdk": "lycan-python-test"},
            }
            self.queue.put_nowait(log_entry)

        except Exception:
            self.handleError(record)

    def _worker_loop(self) -> None:
        batch = []
        last_flush = time.time()

        while not self.stop_event.is_set():
            try:
                timeout = max(0, self.flush_interval - (time.time() - last_flush))
                item = self.queue.get(timeout=timeout)
                batch.append(item)

                if len(batch) >= self.batch_size:
                    self._send_batch(batch)
                    batch.clear()
                    last_flush = time.time()

            except queue.Empty:
                if batch:
                    self._send_batch(batch)
                    batch.clear()
                last_flush = time.time()

        # Final flush on shutdown
        while not self.queue.empty():
            try:
                batch.append(self.queue.get_nowait())
            except queue.Empty:
                break
        if batch:
            self._send_batch(batch)

    def _send_batch(self, batch: list) -> None:
        try:
            response = self.session.post(
                self.api_url,
                json={"entries": batch},
                headers=self.headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            print(f"  [lycan] sent {len(batch)} logs ({response.status_code})")
        except Exception as e:
            print(f"  [lycan] batch failed: {e}")

    def flush(self) -> None:
        batch = []
        while not self.queue.empty():
            try:
                batch.append(self.queue.get_nowait())
            except queue.Empty:
                break
        if batch:
            self._send_batch(batch)

    def close(self) -> None:
        self.stop_event.set()
        if self.worker.is_alive():
            self.worker.join(timeout=5)
        self.flush()
        self.session.close()
        super().close()


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def main():
    handler = RemoteLogHandler(
        api_key="sk_B0U325_JlvXvc7waseSBbjdqUgYGWLl4nyZ0-fWWrNM",
        batch_size=5,
        flush_interval=2,
    )
    logging.root.setLevel(logging.DEBUG)
    logging.root.addHandler(handler)

    # ----------------------------------------------------------------
    # MODE 1 — Standalone: logger name → service_label
    # No middleware, no ContextVar set.
    # ----------------------------------------------------------------
    print("\n--- Mode 1: Standalone (logger name = service_label) ---")

    payment = logging.getLogger("payment")
    auth    = logging.getLogger("auth")
    db      = logging.getLogger("db")

    payment.info("Checkout initiated", extra={"request_id": "req-001"})
    payment.error(
        "Payment gateway rejected transaction",
        extra={"error_code": "ERR_PAYMENT_API", "request_id": "req-001"},
    )
    payment.error(
        "Payment gateway rejected transaction",
        extra={"error_code": "ERR_PAYMENT_API", "request_id": "req-002"},
    )
    payment.error(
        "Payment gateway rejected transaction",
        extra={"error_code": "ERR_PAYMENT_API", "request_id": "req-003"},
    )
    # ↑ 3 hits with same fingerprint → alert fires if rule has threshold=3

    # Different error_code → different fingerprint → won't trigger the above rule
    payment.error("DB pool exhausted", extra={"error_code": "ERR_DB_CONN"})

    auth.info("User logged in", extra={"request_id": "req-100"})
    auth.warning("Failed login attempt")
    auth.error("Token signing key missing", extra={"error_code": "ERR_TOKEN_KEY"})

    db.warning("Slow query — 4200ms")
    db.critical("Replica lag exceeded 30s")

    # ----------------------------------------------------------------
    # MODE 2 — Middleware simulation: ContextVar → service_label
    # In real usage LycanMiddleware sets/resets this per request.
    # Here we simulate two requests manually to show the behaviour.
    # ----------------------------------------------------------------
    print("\n--- Mode 2: Middleware simulation (route prefix = service_label) ---")

    # Simulate a request to POST /payment/charge
    # LycanMiddleware would call set_service_label("payment") here
    token = set_service_label("payment")
    try:
        generic = logging.getLogger("uvicorn.access")   # name doesn't matter
        generic.info("Processing card charge")
        generic.error(
            "Stripe returned 402",
            extra={"error_code": "ERR_PAYMENT_API"},
        )
        # service_label = "payment" because ContextVar is set, not because of logger name
    finally:
        reset_service_label(token)

    # Simulate a request to POST /auth/login — different service entirely
    token = set_service_label("auth")
    try:
        generic.info("Validating credentials")
        generic.warning("Rate limit approaching for IP 1.2.3.4")
        # service_label = "auth" — same logger instance, different ContextVar value
    finally:
        reset_service_label(token)

    # Outside request context — ContextVar is None → falls back to record.name
    generic.info("Background task heartbeat")
    # service_label = "uvicorn.access" (record.name fallback)

    print("\nLogs queued, waiting for flush...")
    time.sleep(3)
    print("Done.")


if __name__ == "__main__":
    main()