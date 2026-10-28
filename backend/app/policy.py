"""Policy engine: plain code, no LLM. Decides ALLOW / CONFIRM / DENY for any spend.

Order (first match wins):
 0 invalid amount -> DENY
 1 insufficient funds -> DENY
 2 over per-purchase cap -> DENY
 3 over daily cap -> DENY
 4 over plan budget -> DENY
 5 unverified payee or trust < MIN_TRUST -> CONFIRM
 6 autonomy 'ask' -> CONFIRM
 7 autonomy 'auto_small' and amount >= auto_buy_under -> CONFIRM
 8 autonomy 'pre_approved' and merchant not pre-approved -> CONFIRM
 9 otherwise -> ALLOW
Caps are inclusive limits: amount == cap is allowed.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .money import fmt_k

MIN_TRUST = 0.5


class Verdict(str, Enum):
    ALLOW = "ALLOW"
    CONFIRM = "CONFIRM"
    DENY = "DENY"


@dataclass(frozen=True)
class Limits:
    autonomy: str = "ask"  # ask | auto_small | pre_approved
    per_purchase_cap: int = 0
    daily_cap: int = 0
    auto_buy_under: int = 0
    pre_approved_merchants: frozenset = field(default_factory=frozenset)


@dataclass(frozen=True)
class Context:
    wallet_balance: int
    spent_today: int
    plan_budget_remaining: Optional[int] = None  # None = no plan budget


@dataclass(frozen=True)
class Action:
    amount: int
    merchant_id: Optional[int]
    payee_verified: bool
    trust_score: float


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    rule: str
    reason: str  # plain language, shown to the user and stored in the audit log


def decide(limits: Limits, ctx: Context, action: Action) -> Decision:
    a = action.amount
    if a <= 0:
        return Decision(Verdict.DENY, "invalid_amount", "The amount must be more than zero.")
    if a > ctx.wallet_balance:
        return Decision(Verdict.DENY, "insufficient_funds",
                        f"Your wallet has {fmt_k(ctx.wallet_balance)} but this costs {fmt_k(a)}.")
    if a > limits.per_purchase_cap:
        return Decision(Verdict.DENY, "per_purchase_cap",
                        f"This is more than your {fmt_k(limits.per_purchase_cap)} limit per purchase.")
    if ctx.spent_today + a > limits.daily_cap:
        return Decision(Verdict.DENY, "daily_cap",
                        f"This would take you over your {fmt_k(limits.daily_cap)} daily limit "
                        f"({fmt_k(ctx.spent_today)} already spent today).")
    if ctx.plan_budget_remaining is not None and a > ctx.plan_budget_remaining:
        return Decision(Verdict.DENY, "plan_budget",
                        f"This is more than what is left of your plan budget ({fmt_k(ctx.plan_budget_remaining)}).")
    if not action.payee_verified:
        return Decision(Verdict.CONFIRM, "unverified_payee", "This seller is not verified, so you need to approve it.")
    if action.trust_score < MIN_TRUST:
        return Decision(Verdict.CONFIRM, "low_trust", "This listing has a low trust score, so you need to approve it.")
    if limits.autonomy == "ask":
        return Decision(Verdict.CONFIRM, "autonomy_ask",
                        "Your autonomy setting is 'ask', so every purchase needs your approval.")
    if limits.autonomy == "auto_small":
        if a >= limits.auto_buy_under:
            return Decision(Verdict.CONFIRM, "above_auto_limit",
                            f"This is {fmt_k(limits.auto_buy_under)} or more, so you need to approve it.")
        return Decision(Verdict.ALLOW, "auto_small",
                        f"Under your {fmt_k(limits.auto_buy_under)} auto-buy limit and within all your limits.")
    if limits.autonomy == "pre_approved":
        if action.merchant_id not in limits.pre_approved_merchants:
            return Decision(Verdict.CONFIRM, "not_pre_approved", "This seller is not on your pre-approved list.")
        return Decision(Verdict.ALLOW, "pre_approved", "Pre-approved seller and within all your limits.")
    # Unknown autonomy value: fail safe, never auto-allow.
    return Decision(Verdict.CONFIRM, "unknown_autonomy",
                    "Your autonomy setting is not recognised, so you need to approve it.")