import math
import uuid

from .base import PaymentResult, RideQuote


class MockPayment:
    """In-memory payment provider. Same idempotency key returns the same payment."""

    def __init__(self):
        self._by_key, self._by_id = {}, {}

    def create_payment(self, amount, idempotency_key, reference):
        if idempotency_key in self._by_key:
            return self._by_key[idempotency_key]
        p = PaymentResult(id=f"mock_{uuid.uuid4().hex[:12]}", status="pending",
                          amount=amount, idempotency_key=idempotency_key)
        self._by_key[idempotency_key] = self._by_id[p.id] = p
        return p

    def settle(self, payment_id, ok=True):
        """Test/demo helper standing in for the provider's webhook."""
        self._by_id[payment_id].status = "succeeded" if ok else "failed"
        return self._by_id[payment_id]

    def check_status(self, payment_id):
        return self._by_id[payment_id]

    def refund(self, payment_id):
        self._by_id[payment_id].status = "refunded"
        return self._by_id[payment_id]


class MockRide:
    """SIMULATED ride provider. Clearly labeled in the UI. Not a real service."""
    PER_KM = 1000  # K10 per km, a made-up demo rate
    BASE = 2000    # K20 base fare

    def estimate(self, from_lat, from_lng, to_lat, to_lng):
        km = _haversine_km(from_lat, from_lng, to_lat, to_lng)
        return RideQuote(price=self.BASE + int(km * self.PER_KM),
                         eta_minutes=max(5, int(km * 3) + 4),
                         provider="Simulated ride provider")

    def book(self, quote, pickup_at_iso, idempotency_key):
        return {"ride_id": f"simride_{uuid.uuid4().hex[:8]}", "pickup_at": pickup_at_iso,
                "price": quote.price, "provider": quote.provider, "simulated": True}


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))