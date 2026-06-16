import atexit
import logging
import queue
import threading
import time

import requests


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

        self.worker = threading.Thread(
            target=self._worker_loop,
            daemon=True,
        )
        self.worker.start()

        atexit.register(self.close)

    def emit(self, record):
        """
        Fast, non-blocking.
        Just enqueue the log record.
        """

        try:
            log_entry = {
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
                "extra": {
                    "sdk": "lycan-python-test",
                },
            }

            self.queue.put_nowait(log_entry)

        except Exception:
            self.handleError(record)

    def _worker_loop(self):
        """
        Background thread that batches logs.
        """

        batch = []
        last_flush = time.time()

        while not self.stop_event.is_set():
            try:
                timeout = max(
                    0,
                    self.flush_interval
                    - (time.time() - last_flush),
                )

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

        # Final flush during shutdown
        while not self.queue.empty():
            try:
                batch.append(
                    self.queue.get_nowait()
                )
            except queue.Empty:
                break

        if batch:
            self._send_batch(batch)

    def _send_batch(self, batch):
        try:
            payload = {
                "entries": batch,
            }

            response = self.session.post(
                self.api_url,
                json=payload,
                headers=self.headers,
                timeout=self.timeout,
            )

            response.raise_for_status()

            print(
                f"Sent batch of {len(batch)} logs "
                f"({response.status_code})"
            )

        except Exception as e:
            print(
                f"Batch submission failed: {e}"
            )

    def flush(self):
        """
        Force immediate flush.
        """

        batch = []

        while not self.queue.empty():
            try:
                batch.append(
                    self.queue.get_nowait()
                )
            except queue.Empty:
                break

        if batch:
            self._send_batch(batch)

    def close(self):
        """
        Graceful shutdown.
        """

        self.stop_event.set()

        if self.worker.is_alive():
            self.worker.join(timeout=5)

        self.flush()

        self.session.close()

        super().close()


def main():
    logger = logging.getLogger("test-app")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    handler = RemoteLogHandler(
        api_key="sk_B0U325_JlvXvc7waseSBbjdqUgYGWLl4nyZ0-fWWrNM",
        batch_size=5,
        flush_interval=2,
    )

    logger.addHandler(handler)

    logger.debug("Debug test message")
    logger.info("User logged in")
    logger.warning("Low disk space")
    logger.error("Database connection failed")
    logger.critical("Critical system error")

    print("Logs queued...")

    time.sleep(3)

    print("Finished sending logs.")


if __name__ == "__main__":
    main()