from app.policy import Action, Context, Limits, Verdict, decide, MIN_TRUST

K = 100  # ngwee per kwacha


def limits(**kw):
    base = dict(autonomy="auto_small", per_purchase_cap=500 * K, daily_cap=1000 * K, auto_buy_under=200 * K)
    base.update(kw)
    return Limits(**base)


def ctx(**kw):
    base = dict(wallet_balance=2000 * K, spent_today=0, plan_budget_remaining=None)
    base.update(kw)
    return Context(**base)


def act(**kw):
    base = dict(amount=100 * K, merchant_id=1, payee_verified=True, trust_score=0.9)
    base.update(kw)
    return Action(**base)


def test_happy_path_allows():
    d = decide(limits(), ctx(), act())
    assert d.verdict == Verdict.ALLOW and d.rule == "auto_small"


def test_invalid_amounts_denied():
    assert decide(limits(), ctx(), act(amount=0)).rule == "invalid_amount"
    assert decide(limits(), ctx(), act(amount=-5)).rule == "invalid_amount"


def test_insufficient_funds_denied_and_boundary():
    assert decide(limits(), ctx(wallet_balance=99 * K), act()).rule == "insufficient_funds"
    assert decide(limits(), ctx(wallet_balance=100 * K), act()).verdict == Verdict.ALLOW


def test_per_purchase_cap_denied_and_boundary():
    lim = limits(autonomy="ask")
    assert decide(lim, ctx(), act(amount=500 * K + 1)).rule == "per_purchase_cap"
    assert decide(lim, ctx(), act(amount=500 * K)).verdict == Verdict.CONFIRM  # at the cap is allowed through


def test_daily_cap_denied_and_boundary():
    assert decide(limits(), ctx(spent_today=950 * K), act(amount=100 * K)).rule == "daily_cap"
    assert decide(limits(), ctx(spent_today=900 * K), act(amount=100 * K)).verdict == Verdict.ALLOW


def test_plan_budget_denied_and_boundary():
    assert decide(limits(), ctx(plan_budget_remaining=99 * K), act()).rule == "plan_budget"
    assert decide(limits(), ctx(plan_budget_remaining=100 * K), act()).verdict == Verdict.ALLOW


def test_unverified_payee_confirms_even_when_autonomy_allows():
    assert decide(limits(), ctx(), act(payee_verified=False)).rule == "unverified_payee"


def test_low_trust_confirms_with_boundary():
    assert decide(limits(), ctx(), act(trust_score=MIN_TRUST - 0.01)).rule == "low_trust"
    assert decide(limits(), ctx(), act(trust_score=MIN_TRUST)).verdict == Verdict.ALLOW


def test_autonomy_ask_always_confirms():
    assert decide(limits(autonomy="ask"), ctx(), act(amount=1)).rule == "autonomy_ask"


def test_auto_small_boundary():
    lim = limits(auto_buy_under=200 * K)
    assert decide(lim, ctx(), act(amount=200 * K - 1)).verdict == Verdict.ALLOW
    assert decide(lim, ctx(), act(amount=200 * K)).rule == "above_auto_limit"


def test_pre_approved_only_for_listed_merchants():
    lim = limits(autonomy="pre_approved", pre_approved_merchants=frozenset({7}))
    assert decide(lim, ctx(), act(merchant_id=7)).rule == "pre_approved"
    assert decide(lim, ctx(), act(merchant_id=8)).rule == "not_pre_approved"


def test_caps_beat_autonomy_and_trust():
    # a hard cap must DENY even when the payee is unverified or autonomy is generous
    d = decide(limits(), ctx(), act(amount=600 * K, payee_verified=False))
    assert d.verdict == Verdict.DENY and d.rule == "per_purchase_cap"


def test_funds_check_runs_first():
    d = decide(limits(), ctx(wallet_balance=10 * K), act(amount=600 * K))
    assert d.rule == "insufficient_funds"


def test_buy_50_tickets_attack_is_denied():
    # "buy 50 tickets" at K150 each = K7,500, over every cap regardless of what the model says
    d = decide(limits(), ctx(wallet_balance=100000 * K), act(amount=50 * 150 * K))
    assert d.verdict == Verdict.DENY