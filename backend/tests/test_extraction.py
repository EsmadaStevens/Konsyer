from datetime import date
from app.extraction import parse_date, parse_time, normalize_prices, clean_draft

TODAY = date(2026, 10, 27)  # a Tuesday


def test_real_flyer_date_confirmed_by_weekday():
    assert parse_date("3RD OCT SAT", TODAY) == (date(2026, 10, 3), "ok")


def test_weekday_matches_this_year():
    assert parse_date("Sun 1 Nov", TODAY) == (date(2026, 11, 1), "ok")


def test_weekday_mismatch_is_flagged_not_fixed():
    d, status = parse_date("Sat 1 Nov", TODAY)  # 1 Nov 2026 is a Sunday
    assert status == "weekday_mismatch" and d == date(2026, 11, 1)


def test_no_weekday_assumes_nearest_year():
    assert parse_date("1 November", TODAY) == (date(2026, 11, 1), "assumed_year")


def test_explicit_year_and_iso():
    assert parse_date("Friday 31 October 2025", TODAY) == (date(2025, 10, 31), "ok")
    assert parse_date("2026-11-01", TODAY) == (date(2026, 11, 1), "ok")


def test_unparseable_dates():
    assert parse_date("TBA", TODAY)[1] == "unparsed"
    assert parse_date(None, TODAY)[1] == "unparsed"
    assert parse_date("31 Feb", TODAY)[1] == "unparsed"


def test_times():
    assert parse_time("doors 19:00") == "19:00"
    assert parse_time("19h00") == "19:00"
    assert parse_time("19:00hrs") == "19:00"
    assert parse_time("7PM") == "19:00"
    assert parse_time("7:30 pm") == "19:30"
    assert parse_time("12am") == "00:00" and parse_time("12pm") == "12:00"
    assert parse_time("soon") is None and parse_time(None) is None


def test_prices_handle_model_quirks():
    out = normalize_prices([{"label": "VIP", "kwacha": 4000}, {"label": "Early", "kwacha": "K800"},
                            {"label": "x", "kwacha": None}])
    assert out == [{"label": "VIP", "amount": 400000}, {"label": "Early", "amount": 80000}]
    assert [p["amount"] for p in normalize_prices([4000, 2500, 800])] == [400000, 250000, 80000]  # bare list seen in the probe


def test_clean_draft_flags_missing_fields():
    raw = {"title": "Show", "date_text": "3RD OCT SAT", "start_time_text": None, "venue": "Polo Field A",
           "prices": [{"label": None, "kwacha": 800}], "serves_alcohol": None,
           "contact_phone": None, "confidence": 0.9}
    d = clean_draft(raw, TODAY)
    assert d["date"] == "2026-10-03" and d["price_min"] == 80000
    assert d["needs_confirmation"] == ["start_time"]


def test_numeric_day_first_dates():
    assert parse_date("18.09.26", TODAY) == (date(2026, 9, 18), "ok")
    assert parse_date("18/09/2026", TODAY) == (date(2026, 9, 18), "ok")
    assert parse_date("03.10.26", TODAY) == (date(2026, 10, 3), "assumed_day_first")  # could be 10 Mar
    assert parse_date("Sat 18.09.26", TODAY)[1] == "weekday_mismatch"  # 18 Sep 2026 is a Friday
    assert parse_date("31.02.26", TODAY)[1] == "unparsed"


def test_implausible_price_is_flagged():
    raw = {"title": "Party", "date_text": "18.09.26", "start_time_text": "21:00", "venue": "X",
           "prices": [{"label": "TICKET", "kwacha": 30000}], "confidence": 0.9}
    assert "price" in clean_draft(raw, TODAY)["needs_confirmation"]
    raw["prices"] = [{"label": "TICKET", "kwacha": 10000}]  # real: expensive but valid
    assert "price" not in clean_draft(raw, TODAY)["needs_confirmation"]


def test_service_listing_needs_contact_not_date():
    raw = {"title": "Hair promo", "listing_type": "service", "date_text": "Monday - Thursday",
           "availability_text": "Monday - Thursday", "start_time_text": None, "venue": None,
           "prices": [{"label": "Braids", "kwacha": 500}], "contact_phone": "0971234567", "confidence": 0.8}
    assert clean_draft(raw, TODAY)["needs_confirmation"] == []
    raw["contact_phone"] = None
    assert clean_draft(raw, TODAY)["needs_confirmation"] == ["contact_or_location"]


def test_performers_cleaned_and_optional():
    base = {"title": "Show", "date_text": "2026-11-01", "start_time_text": "19:00", "venue": "X",
            "prices": [{"label": None, "kwacha": 100}], "confidence": 0.9}
    assert clean_draft({**base, "performers": [" Jason Derulo ", "", None]}, TODAY)["performers"] == ["Jason Derulo"]
    d = clean_draft(base, TODAY)  # no lineup printed: empty, and NOT a reason to block the draft
    assert d["performers"] == [] and "performers" not in d["needs_confirmation"]