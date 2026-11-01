from app import contact
from app.agent.cards import contact_card


def base(**over):
    c = {"name": "Glam Nails", "message": "hi", "channels": [], "phone": "260977123456",
         "source_url": "https://example.com/p", "verified": False}
    c.update(over)
    return c


def test_unverified_contact_gets_invite_with_its_own_whatsapp_link():
    card = contact_card(base())
    assert "Glam Nails" in card["invite_message"] and "no real payments yet" in card["invite_message"]
    kinds = [ch["kind"] for ch in card["invite_channels"]]
    assert "whatsapp" in kinds and "call" in kinds
    wa = next(ch["url"] for ch in card["invite_channels"] if ch["kind"] == "whatsapp")
    assert wa.startswith("https://wa.me/260977123456?text=") and "Konsyer" in wa.replace("%20", " ")


def test_verified_contact_gets_no_invite():
    card = contact_card(base(verified=True))
    assert "invite_message" not in card and "invite_channels" not in card


def test_invite_strips_control_characters_and_length():
    msg = contact.build_invite("Evil\nName\x00 " + "x" * 200)
    assert "\n" not in msg and "\x00" not in msg and len(msg) < 600

def test_message_drops_a_model_written_greeting_and_sign_off():
    m = contact.build_message("Glow", "Hi, I'd like braids on Saturday afternoon in Kabulonga. Budget K300. Thanks!")
    assert m.count("Hello") == 1 and "Thanks!." not in m and "Thank you." in m
    assert "I'd like braids on Saturday afternoon in Kabulonga. Budget K300." in m
    assert "booking assistant" not in m and "I'm looking for: I" not in m


def test_message_with_a_plain_request_uses_looking_for():
    m = contact.build_message("Glow", "gel nails, Saturday")
    assert "I'm looking for: gel nails, Saturday." in m


def test_message_without_details_stays_generic_and_has_no_payment_talk():
    m = contact.build_message("Glow", "")
    assert "interested in your services" in m and "pay" not in m.lower().replace("price", "")
