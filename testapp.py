"""
Lycan SDK — FastAPI middleware test app.

Run:
    pip install fastapi uvicorn requests
    uvicorn lycan_test_app:app --reload

Then hit the routes:
    curl -X POST http://localhost:8000/payment/charge
    curl -X POST http://localhost:8000/auth/login
    curl -X GET  http://localhost:8000/orders/list
    curl -X GET  http://localhost:8000/unknown/route
"""

import logging

from fastapi import FastAPI

from lycan import LycanMiddleware, RemoteLogHandler

# ---------------------------------------------------------------------------
# 1. One handler on root logger — captures everything in the process
# ---------------------------------------------------------------------------

API_KEY = "sk_B0U325_JlvXvc7waseSBbjdqUgYGWLl4nyZ0-fWWrNM"

logging.root.setLevel(logging.DEBUG)
logging.root.addHandler(RemoteLogHandler(api_key=API_KEY))

# ---------------------------------------------------------------------------
# 2. FastAPI app + middleware
# ---------------------------------------------------------------------------

app = FastAPI()

app.add_middleware(
    LycanMiddleware,
    api_key=API_KEY,
    services={
        "/payment": "payment",
        "/auth":    "auth",
        "/orders":  "orders",
    },
)

# ---------------------------------------------------------------------------
# 3. Route handlers — use any logger name, service_label comes from middleware
# ---------------------------------------------------------------------------

# It doesn't matter what name you give the logger here.
# The middleware sets service_label from the route prefix, not the logger name.
logger = logging.getLogger(__name__)


@app.post("/payment/charge")
async def payment_charge():
    logger.info("Processing card charge")

    # Simulate a payment failure — this error_code matches an alert rule:
    #   service_label="payment", match_field="error_code", match_value="ERR_PAYMENT_API"
    logger.error(
        "Stripe returned 402 — card declined",
        extra={"error_code": "ERR_PAYMENT_API", "request_id": "req-001"},
    )
    return {"status": "failed", "reason": "card_declined"}


@app.post("/payment/refund")
async def payment_refund():
    # Still tagged service_label="payment" because path starts with /payment
    logger.info("Refund initiated")
    logger.warning("Refund amount exceeds original charge")
    return {"status": "ok"}


@app.post("/auth/login")
async def auth_login():
    # Tagged service_label="auth" — same logger instance as above, different route
    logger.info("Validating credentials")
    logger.error(
        "Token signing key missing",
        extra={"error_code": "ERR_TOKEN_KEY"},
    )
    return {"status": "error"}


@app.get("/orders/list")
async def orders_list():
    # Tagged service_label="orders"
    logger.info("Fetching order list")
    logger.warning("Order list query took 3200ms — slow")
    return {"orders": []}


@app.get("/unknown/route")
async def unknown_route():
    # No prefix match → middleware falls back to "unknown" as service_label
    logger.info("Hit an unmapped route")
    return {"ok": True}


@app.get("/health")
async def health():
    # /health doesn't match any prefix → service_label = "health" (first segment fallback)
    return {"status": "ok"}