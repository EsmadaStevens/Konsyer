import json

from app import dispute_reference, disputes

JPG = b"\xff\xd8\xff" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def fake_chat(answer, seen):
    def chat(messages, **kw):
        seen.append(messages)
        return {"message": {"content": json.dumps(answer)}}
    return chat


def answer(verdict, diffs, conf=0.9):
    return {"verdict": verdict, "promised": "p", "observed": "o", "differences": diffs,
            "image_quality": "good", "confidence": conf}


def test_the_model_gets_the_seller_picture_first_and_the_customer_photo_second():
    seen = []
    out = dispute_reference.assess_with_reference("French tips", "", JPG, PNG, fake_chat(answer("match", []), seen))
    parts = seen[0][0]["content"]
    assert [p["type"] for p in parts] == ["text", "image_url", "image_url"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png")    # the seller's picture (reference)
    assert parts[2]["image_url"]["url"].startswith("data:image/jpeg")   # the customer's photo
    assert "IMAGE 1" in parts[0]["text"] and "LISTING TEXT remains the main promise" in parts[0]["text"]
    assert out["verdict"] == "match"


def test_the_same_safety_rules_apply():
    seen = []
    # a mismatch with only minor differences is not a mismatch
    out = dispute_reference.assess_with_reference("x", "", JPG, PNG, fake_chat(
        answer("mismatch", [{"text": "shade", "severity": "minor"}]), seen))
    assert out["verdict"] != "mismatch"
    # low confidence cannot become a verdict
    low = dispute_reference.assess_with_reference("x", "", JPG, PNG, fake_chat(
        answer("mismatch", [{"text": "no tips", "severity": "major"}], conf=0.3), seen))
    assert low["verdict"] == "unclear"


def test_bad_images_and_model_failures_are_clean_errors():
    import pytest
    with pytest.raises(disputes.DisputeError) as e:
        dispute_reference.assess_with_reference("x", "", b"nope", PNG, fake_chat(answer("match", []), []))
    assert e.value.code == "invalid"

    def boom(messages, **kw):
        raise RuntimeError("secret detail")
    with pytest.raises(disputes.DisputeError) as e:
        dispute_reference.assess_with_reference("x", "", JPG, PNG, boom)
    assert e.value.code == "model_unavailable" and "secret" not in e.value.message
