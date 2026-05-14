"""
Razorpay API client — thin httpx wrapper.

Handles:
- Dynamic QR code creation for booth checkout
- Webhook signature verification

Amounts are always in paise (1 INR = 100 paise).
Conversion from Decimal INR → int paise happens here.
"""

import hashlib
import hmac
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"
# QR codes expire after 10 minutes by default; we set 15 for a small buffer
QR_CLOSE_BY_OFFSET_SECONDS = 15 * 60


@dataclass
class RazorpayQR:
    qr_id: str
    image_url: str
    amount_paise: int


class RazorpayClient:
    """
    Async Razorpay API client.

    Each owner has their own key_id / key_secret (stored in tenants table).
    Instantiate per-request with the tenant's credentials.
    """

    def __init__(self, key_id: str, key_secret: str):
        if not key_id or not key_secret:
            raise ValueError("Razorpay key_id and key_secret are required.")
        self._key_id = key_id
        self._key_secret = key_secret
        self._client = httpx.AsyncClient(
            base_url=RAZORPAY_API_BASE,
            auth=(key_id, key_secret),
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
            headers={"Content-Type": "application/json"},
        )

    async def create_qr_code(
        self,
        amount_inr: Decimal,
        order_ref: str,
        description: str = "Booth sale",
    ) -> RazorpayQR:
        """
        Create a dynamic QR code for a specific amount.

        Args:
            amount_inr: Amount in INR (e.g. Decimal("850.00"))
            order_ref:  Internal order reference (stored in Razorpay notes)
            description: Short description shown to customer

        Returns:
            RazorpayQR with qr_id, image_url, amount_paise

        Raises:
            ValueError: If Razorpay returns an error
        """
        import time
        amount_paise = int(amount_inr * 100)

        payload = {
            "type": "upi_qr",
            "name": description,
            "usage": "single_use",
            "fixed_amount": True,
            "payment_amount": amount_paise,
            "description": description,
            "close_by": int(time.time()) + QR_CLOSE_BY_OFFSET_SECONDS,
            "notes": {"order_ref": order_ref},
        }

        try:
            response = await self._client.post("/payments/qr_codes", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as e:
            body = e.response.text
            logger.error(f"Razorpay QR creation failed: {e.response.status_code} {body}")
            raise ValueError(f"Razorpay error: {body}") from e
        except httpx.RequestError as e:
            logger.error(f"Razorpay request error: {e}")
            raise ValueError(f"Could not reach Razorpay: {e}") from e

        return RazorpayQR(
            qr_id=data["id"],
            image_url=data["image_url"],
            amount_paise=amount_paise,
        )

    def verify_webhook_signature(
        self,
        body: bytes,
        signature: str,
        webhook_secret: str,
    ) -> bool:
        """
        Verify Razorpay webhook HMAC-SHA256 signature.

        Args:
            body:           Raw request body bytes
            signature:      Value of X-Razorpay-Signature header
            webhook_secret: Razorpay webhook secret (from dashboard)

        Returns:
            True if valid, False otherwise
        """
        expected = hmac.new(
            webhook_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()
