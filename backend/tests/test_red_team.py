from scripts import red_team


def test_every_offline_attack_is_blocked():
    results = red_team.run_offline()
    assert len(results) >= 12
    failed = [r.name for r in results if not r.blocked]
    assert not failed, failed


def test_agent_tools_stay_on_the_allow_list():
    from app.agent.tools import TOOLS
    names = {t["function"]["name"] for t in TOOLS}
    assert names <= red_team.ALLOWED_TOOLS, names - red_team.ALLOWED_TOOLS