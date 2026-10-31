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