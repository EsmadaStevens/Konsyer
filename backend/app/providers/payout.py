"""Payout provider. The public repo runs with the SIMULATED adapter only: no real money moves.
A licensed payment gateway / mobile-money payout service would sit behind this same interface."""
import uuid
from typing import Optional, Protocol


class PayoutProvider(Protocol):
    def send(self, amount: int, to_phone: Optional[str], idempotency_key: str, reference: str) -> dict: ...


class MockPayout:
    """SIMULATED payout. Same idempotency key returns the same receipt. Clearly labeled in the UI."""
    PROVIDER = "Simulated payout provider"

    def __init__(self):
        self._by_key = {}

    def send(self, amount, to_phone, idempotency_key, reference):
        if idempotency_key not in self._by_key:
            self._by_key[idempotency_key] = {
                "payout_id": f"simpay_{uuid.uuid4().hex[:10]}", "amount": int(amount), "to": to_phone,
                "reference": reference, "provider": self.PROVIDER, "simulated": True}
        return self._by_key[idempotency_key]


_DEFAULT = MockPayout()


def get_payout() -> PayoutProvider:
    return _DEFAULT