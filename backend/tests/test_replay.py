import json

import pytest
from fastapi.testclient import TestClient

from app import disputes, llm, main
from app.db import get_conn, init_db
from app.seed import seed
from app.timeutil import lusaka_today

JPG = b"\xff\xd8\xff" + b"\x00" * 40


class FakeResp:
    status_code = 200

    def __init__(self, content):
        self._c = content

    def json(self):
        return {"choices": [{"message": {"role": "assistant", "content": self._c}, "finish_reason": "stop"}]}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in ("KONSYER_RECORD", "KONSYER_REPLAY", "KONSYER_STUB", "LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def say(text, system="You are Konsyer. Today is Monday."):
    return [{"role": "system", "content": system}, {"role": "user", "content": text}]


def record(monkeypatch, tmp_path, calls):
    """Run live calls against a fake model, recording them. calls = [(job, messages, answer)]."""
    path = tmp_path / "trace.jsonl"
    monkeypatch.setenv("LLM_API_KEY", "super-secret-key")
    monkeypatch.setenv("KONSYER_RECORD", str(path))
    for job, messages, answer in calls:
        monkeypatch.setattr(llm.httpx, "post", lambda *a, _r=FakeResp(answer), **k: _r)
        with llm.as_job(job):
            llm.chat(messages)
    monkeypatch.delenv("KONSYER_RECORD")
    monkeypatch.delenv("LLM_API_KEY")
    monkeypatch.setenv("KONSYER_REPLAY", str(path))
    return path


def test_a_recorded_answer_is_replayed_without_a_key_and_the_trace_holds_no_secret(monkeypatch, tmp_path):
    path = record(monkeypatch, tmp_path, [("agent", say("hello there"), "recorded hi")])
    assert llm.chat(say("hello there"))["message"]["content"] == "recorded hi"
    assert "super-secret-key" not in path.read_text()


def test_the_key_ignores_wording_of_prompts_dates_case_and_spacing(monkeypatch, tmp_path):
    record(monkeypatch, tmp_path, [("agent", say("Hello   THERE"), "same")])
    other_day = say("hello there", system="A different system prompt on another date")
    assert llm.chat(other_day)["message"]["content"] == "same"


def test_an_input_nobody_recorded_says_so_plainly(monkeypatch, tmp_path):
    record(monkeypatch, tmp_path, [("agent", say("known"), "x")])
    with pytest.raises(llm.ReplayMiss) as e:
        llm.chat(say("something new"))
    assert "no recording" in str(e.value) and "LLM_API_KEY" in str(e.value)


def test_steps_of_one_turn_are_told_apart_by_the_tool_results_before_them(monkeypatch, tmp_path):
    step0 = say("find nails")
    step1 = step0 + [{"role": "assistant", "content": "", "tool_calls": []}, {"role": "tool", "content": "{}"}]
    record(monkeypatch, tmp_path, [("agent", step0, "call a tool"), ("agent", step1, "the final reply")])
    assert llm.chat(step0)["message"]["content"] == "call a tool"
    assert llm.chat(step1)["message"]["content"] == "the final reply"


def test_the_same_question_asked_again_gets_the_next_answer_then_cycles(monkeypatch, tmp_path):
    record(monkeypatch, tmp_path, [("agent", say("again"), "first"), ("agent", say("again"), "second")])
    got = [llm.chat(say("again"))["message"]["content"] for _ in range(3)]
    assert got == ["first", "second", "first"]


def photo(name_bytes):
    return [{"role": "user", "content": [{"type": "text", "text": "compare"},
                                         {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + name_bytes}}]}]


def test_photos_are_matched_by_file_name_because_the_app_re_encodes_them(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("KONSYER_RECORD", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: FakeResp("verdict A"))
    with llm.as_job("dispute_vision"), llm.input_name("C:/photos/Red_Nails.JPG"):
        llm.chat(photo("AAAA"))
    monkeypatch.delenv("KONSYER_RECORD")
    monkeypatch.delenv("LLM_API_KEY")
    monkeypatch.setenv("KONSYER_REPLAY", str(tmp_path / "t.jsonl"))
    with llm.as_job("dispute_vision"), llm.input_name("red_nails.jpg"):      # different bytes, same name
        assert llm.chat(photo("BBBB"))["message"]["content"] == "verdict A"
    with llm.as_job("dispute_vision"), llm.input_name("other.jpg"):
        with pytest.raises(llm.ReplayMiss):
            llm.chat(photo("AAAA"))


def test_a_trace_never_contains_a_photo(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("KONSYER_RECORD", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: FakeResp("ok"))
    with llm.as_job("dispute_vision"):
        llm.chat(photo("SECRETPIXELS" * 20))
    assert "SECRETPIXELS" not in (tmp_path / "t.jsonl").read_text()


def verdict_json():
    return json.dumps({"verdict": "mismatch", "promised": "p", "observed": "red nails", "image_quality": "good",
                       "confidence": 0.95, "differences": [{"text": "no white tips", "severity": "major"}]})


def test_the_photo_check_works_from_a_recording_and_unknown_photos_get_a_clear_message(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("KONSYER_RECORD", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: FakeResp(verdict_json()))
    with llm.input_name("red_nails.jpg"):
        disputes.assess("French tips", "", JPG)
    monkeypatch.delenv("KONSYER_RECORD")
    monkeypatch.delenv("LLM_API_KEY")
    monkeypatch.setenv("KONSYER_REPLAY", str(tmp_path / "t.jsonl"))
    with llm.input_name("red_nails.jpg"):
        out = disputes.assess("French tips", "", JPG)
    assert out["verdict"] == "mismatch" and out["differences"][0]["severity"] == "major"
    with llm.input_name("mystery.jpg"), pytest.raises(disputes.DisputeError) as e:
        disputes.assess("French tips", "", JPG)
    assert "no recording" in e.value.message


def test_the_app_chats_without_a_key_in_replay_mode_and_says_when_it_has_no_recording(monkeypatch, tmp_path):
    monkeypatch.setenv("KONSYER_DB", str(tmp_path / "t.db"))
    c = get_conn(str(tmp_path / "t.db"))
    init_db(c)
    seed(c, lusaka_today())
    c.close()
    record(monkeypatch, tmp_path, [("agent", [{"role": "system", "content": "s"}, {"role": "user", "content": "hello"}], "Hi from the recording")])
    client = TestClient(main.app)
    assert client.get("/mode").json() == {"mode": "replay"}
    ok = client.post("/chat", json={"message": "hello", "user_id": 1, "history": []}).json()
    assert ok["stub"] is False and ok["reply"] == "Hi from the recording"
    miss = client.post("/chat", json={"message": "never recorded", "user_id": 1, "history": []}).json()
    assert "no recording" in miss["reply"]


def test_the_modes():
    assert llm.mode() == "stub"


def test_the_recording_script_records_every_scenario_and_the_result_replays(monkeypatch, tmp_path):
    import sys

    from scripts import record_demo

    def fake_post(url, headers=None, json=None, timeout=None):
        content = verdict_json() if json.get("response_format") else "A recorded reply."
        return FakeResp(content)

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    monkeypatch.setenv("LLM_API_KEY", "k")
    for name in ("KONSYER_RECORD", "DISCOVERY_PROVIDER", "KONSYER_DB"):  # the script sets these: restore them after
        monkeypatch.setenv(name, "placeholder")
    img = tmp_path / "red_nails.jpg"
    img.write_bytes(JPG)
    out = tmp_path / "demo" / "trace.jsonl"
    monkeypatch.setattr(sys, "argv", ["record_demo", "--out", str(out), "--dispute-photo", str(img),
                                      "--extra", "one more request"])
    record_demo.main()
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    labels = [r["label"] for r in rows]
    assert len(rows) == len(record_demo.SCENARIOS) + 2
    assert any("one more request" in l for l in labels) and any("red_nails.jpg" in l for l in labels)
    assert "LLM_API_KEY" not in out.read_text()

    monkeypatch.delenv("LLM_API_KEY")
    monkeypatch.delenv("KONSYER_RECORD", raising=False)
    monkeypatch.setenv("KONSYER_REPLAY", str(out))
    got = llm.chat(say(record_demo.SCENARIOS[0]))
    assert got["message"]["content"] == "A recorded reply."
