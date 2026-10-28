"""Provider interfaces. The public repo must run with Mock adapters only."""
from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class PaymentResult:
    id: str
    status: str  # pending | succeeded | failed | refunded
    amount: int
    idempotency_key: str


class PaymentProvider(Protocol):
    def create_payment(self, amount: int, idempotency_key: str, reference: str) -> PaymentResult: ...
    def check_status(self, payment_id: str) -> PaymentResult: ...
    def refund(self, payment_id: str) -> PaymentResult: ...


@dataclass
class RideQuote:
    price: int  # ngwee
    eta_minutes: int
    provider: str  # always labeled simulated for the mock


class RideProvider(Protocol):
    def estimate(self, from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> RideQuote: ...
    def book(self, quote: RideQuote, pickup_at_iso: str, idempotency_key: str) -> dict: ...