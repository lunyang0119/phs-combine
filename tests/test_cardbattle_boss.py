"""몸의 보느와 기믹 (선제 라운드·데몬·블랙 ICE·방화벽 공방·생명 유지·모드 일반화·상태), 💡 팁, 튜토리얼 대본."""
import random

import pytest

from cardbattle import engine as E
from cardbattle import render as R
from cardbattle import strings as S
from cardbattle import tutorial as T

from tests.test_cardbattle_engine import always, enemy_plays, flat_cfg, hp, play, wait_chooser
from tests.test_cardbattle_gig import SeqRng, mk2, mk_cards2


# ---------------------------------------------------------------------------
# 데몬
# ---------------------------------------------------------------------------
def test_scatter_damage_daemon_bite_and_expiry():
    cfg = flat_cfg(daemon_damage=5, daemon_turns=2, daemon_max_infected=4)
    st = mk2(n_players=3, gimmicks=("daemon",), cfg=cfg)
    enemy_plays(st, "ambush")
    rep = play(st, {"1": ("guard", None), "2": ("guard", None), "3": ("guard", None)})
    line = next(l for l in rep["lines"] if l["actor_id"] == "e1")
    dmg = [t for t in line["targets"] if t["kind"] == "damage"]
    dae = [t for t in line["targets"] if t["kind"] == "daemon"]
    assert len(dmg) == 2 and len({t["id"] for t in dmg}) == 2 and all(t["amount"] == 4 for t in dmg)   # 해킹(0단계)이 엄폐보다 먼저
    assert len(dae) == 1 and dae[0]["amount"] == 1 and "daemon" in rep["tips"]
    assert not any(l.get("daemon") for l in rep["lines"])                               # 걸린 라운드에는 물지 않는다
    inf = E.infected(st)
    assert len(inf) == 1 and inf[0].mod("daemon").turns_left == 1                        # 2 → 라운드 끝 틱 → 1
    pid = inf[0].id
    before = inf[0].hp
    rep = play(st, {pid: ("guard", None)})
    me = next(l for l in rep["lines"] if l["actor_id"] == pid)
    assert me["daemon"]["amount"] == 5 and hp(st, pid) == before - 5 and "daemon_bite" in rep["tips"]
    assert S.LOG_DAEMON_BITE.format(amount=5, hp=before - 5, max=40) in R.log_lines(me)[0]
    assert st.players[pid].mod("daemon") is None                                          # 다음 라운드 끝에 만료
    rep = play(st, {pid: ("guard", None)})
    assert "daemon" not in next(l for l in rep["lines"] if l["actor_id"] == pid)


def test_daemon_cap_stacks_cleanse_and_reboot():
    cfg = flat_cfg(daemon_max_infected=2, daemon_damage=5)
    st = mk2(n_players=3, gimmicks=("daemon",), cfg=cfg)
    enemy_plays(st, "seize")
    play(st, {})
    assert len(E.infected(st)) == 2
    enemy_plays(st, "seize")
    play(st, {})
    assert len(E.infected(st)) == 2 and sum(int(p.mod("daemon").value) for p in E.infected(st)) == 4   # 상한 → 중복 감염
    victim = max(E.infected(st), key=lambda p: p.mod("daemon").value)
    rep = play(st, {victim.id: ("guard", None)})
    assert next(l for l in rep["lines"] if l["actor_id"] == victim.id)["daemon"]["amount"] == 10           # 2스택
    other = [p for p in E.infected(st) if p.id != victim.id]
    if other:
        healthy = next(p for p in st.players.values() if p.mod("daemon") is None)
        rep = play(st, {healthy.id: ("wall", other[0].id)})
        wall = next(l for l in rep["lines"] if l["card_id"] == "wall")
        assert any(t["kind"] == "cleanse" for t in wall["targets"]) and other[0].mod("daemon") is None
    enemy_plays(st, "seize")
    play(st, {})
    st.meters["firewall"] = 9
    ev = E.use_firewall(st, "1", "reboot")
    assert not E.infected(st) and ev["targets"]
    # 불발·대상 없음도 행동이다
    st2 = mk2(n_players=1, gimmicks=("daemon",), cfg=cfg, chooser=always("atk"))
    st2.players["1"].modifiers.append(E.Modifier("daemon", 1, 2, 0))
    st2.players["1"].modifiers.append(E.Modifier("misfire", 1.0, 1, 0))
    rep = play(st2, {"1": ("atk", "e1")}, rng=SeqRng(), chooser=always("atk"))
    me = next(l for l in rep["lines"] if l["actor_id"] == "1")
    assert me["result"] == "misfire" and me["daemon"]["amount"] == 5


# ---------------------------------------------------------------------------
# ICE / 방화벽 공방 / 생명 유지 / 상태
# ---------------------------------------------------------------------------
def test_ice_reduces_damage_hacks_strip_with_cap_and_expose():
    cfg = flat_cfg(ice_layers=3, ice_reduction=0.3, ice_round_cap=2, life_support_rounds=3)
    st = mk2(n_players=3, enemy_hp=30, gimmicks=("ice", "life_support", "nonlethal"), cfg=cfg)
    assert st.meters["ice"] == 3 and st.meters["life"] == 3
    assert "블랙 ICE 3/3" in "\n".join(R.meters_text(st)) and "생명 유지 3/3" in "\n".join(R.meters_text(st))
    rep = play(st, {"1": ("atk", "e1")})
    assert hp(st, "e1") == 29 and rep["lines"][0]["targets"][0]["iced"] == 3          # 10 × 0.1
    assert st.meters["life"] == 2 and any(ev["kind"] == "life" for ev in rep["events"])
    rep = play(st, {"1": ("jam", "e1"), "2": ("mis", "e1"), "3": ("stun", "e1")})
    hacks = [l for l in rep["lines"] if l["kind"] == "hack"]
    assert sum(l["ice"]["amount"] for l in hacks) == 2 and any(l["ice"].get("capped") for l in hacks)
    assert st.meters["ice"] == 1 and "ice_strip" in rep["tips"] and st.meters["life"] == 1
    assert st.enemies["e1"].mod("stun") is not None                                  # 보느와는 기절한다
    st.enemies["e1"].modifiers = []                                                  # 기절·고장·글리치를 걷어내고 과부하를 보게 한다
    E.open_round(st, random.Random(1), wait_chooser)
    enemy_plays(st, "overload")
    rep = play(st, {"1": ("guard", None), "2": ("guard", None), "3": ("guard", None)})
    assert st.meters["ice"] == 0 and any(ev["kind"] == "expose" for ev in rep["events"]) and "exposed" in rep["tips"]
    assert all(hp(st, p) == 38 for p in ("1", "2", "3"))                              # 5 × 엄폐 50%
    assert st.status == E.ENDED and st.winner == E.ALLY and st.end_reason == "collapse"   # 과부하 −1, 라운드 −1 → 0
    assert st.meters["boss_state"] == "critical" and "발작" in R.winner_title(st)


def test_ko_beats_collapse_and_boss_state():
    cfg = flat_cfg(life_support_rounds=1)
    st = mk2(n_players=1, enemy_hp=5, gimmicks=("nonlethal", "life_support"), cfg=cfg)
    play(st, {"1": ("knee", "e1")})
    assert st.end_reason == "ko" and st.meters["boss_state"] == "fine" and "멀쩡" in R.winner_title(st)
    st = mk2(n_players=1, enemy_hp=5, gimmicks=("nonlethal", "life_support"), cfg=flat_cfg(life_support_rounds=9))
    play(st, {"1": ("atk", "e1")})
    assert st.meters["boss_state"] == "hurt"
    st = mk2(n_players=1, enemy_hp=1, gimmicks=("nonlethal", "life_support"), cfg=flat_cfg(life_support_rounds=9, overkill_margin=5))
    rep = play(st, {"1": ("atk", "e1")})
    assert rep["lines"][0]["targets"][0]["overkill"] and st.meters["boss_state"] == "critical"
    assert st.meters["last_ko"]["overkill"] and "overkill" in rep["tips"]


def test_firewall_drain_and_lock_next_round_only():
    st = mk2(n_players=2, chooser=wait_chooser)
    st.meters["firewall"] = 5
    enemy_plays(st, "breach", target="1")
    rep = play(st, {"1": ("guard", None), "2": ("guard", None)})
    assert st.meters["firewall"] == 5 - 2 + 2                                             # 깎임 2, 방어 2 로 다시 참
    assert any(ev["kind"] == "firewall_drain" for ev in rep["events"]) and any(ev["kind"] == "firewall_lock" for ev in rep["events"])
    assert st.meters["firewall_lock_round"] == st.round and E.firewall_locked(st)
    with pytest.raises(E.RuleError) as ei:
        E.use_firewall(st, "1", "cover")
    assert ei.value.code == "FIREWALL_LOCKED"
    assert "잠갔습니다" in "\n".join(R.event_lines(rep["events"]))
    play(st, {"1": ("guard", None), "2": ("guard", None)})
    assert not E.firewall_locked(st) and E.use_firewall(st, "1", "reboot")["cost"] == 2


# ---------------------------------------------------------------------------
# 선제 라운드 / 모드 일반화
# ---------------------------------------------------------------------------
def test_first_strike_ambush_round_zero():
    cfg = flat_cfg(first_strike_cards="ambush", daemon_turns=2, daemon_damage=5)
    st = mk2(n_players=3, gimmicks=("first_strike", "daemon"), cfg=cfg, chooser=None)
    assert st.round == 1 and st.status == E.ROUND_OPEN
    amb = st.log[0]
    assert amb["ambush"] and amb["round_no"] == 0 and st.pending_reports == [amb]
    assert not any(l["result"] == "wait" for l in amb["lines"]) and amb["lines"][0]["actor_id"] == "e1"
    hurt = [p for p in st.players.values() if p.hp < 40]
    assert len(hurt) == 2 and all(p.hp == 36 for p in hurt)
    inf = E.infected(st)
    assert len(inf) == 1 and inf[0].mod("daemon").turns_left == 1                     # 0라운드는 틱이 없으니 1
    assert "기습" in R.report_text(amb) and "daemon" in amb["tips"]
    pid = inf[0].id
    rep = play(st, {pid: ("guard", None)})
    assert next(l for l in rep["lines"] if l["actor_id"] == pid)["daemon"]["amount"] == 5
    assert st.players[pid].mod("daemon") is None
    with pytest.raises(E.RuleError) as ei:
        mk2(n_players=1, gimmicks=("first_strike",), cfg=flat_cfg(first_strike_cards="nope"), chooser=None)
    assert ei.value.code == "NO_FIXED_CARD"


def test_modes_trait_uses_modes_config_and_norepeat():
    cfg = flat_cfg(modes="seize:seize:1;overload:overload:1", modes_norepeat="overload")
    st = mk2(n_players=2, enemy_hp=500, gimmicks=("ice",), enemy_traits={"modes": 1.0}, chooser=None, cfg=cfg)
    assert st.intents["e1"]["kind"] == "mode" and st.meters["mode_owner"] == "적1" and st.meters["doll_mode"] in ("seize", "overload")
    assert "🎭 적1:" in "\n".join(R.meters_text(st))
    seq = [st.meters["doll_mode"]]
    rng = random.Random(5)
    for _ in range(14):
        play(st, {"1": ("guard", None), "2": ("guard", None)}, rng=rng, chooser=None)
        if st.status != E.ROUND_OPEN:
            break
        seq.append(st.meters["doll_mode"])
    assert "overload" in seq and "seize" in seq
    assert not any(a == b == "overload" for a, b in zip(seq, seq[1:]))
    # modes 가 비어 있으면 doll_modes 로 폴백
    st2 = mk2(n_players=1, enemy_hp=500, enemy_traits={"modes": 1.0}, chooser=None,
              cfg=flat_cfg(modes="", doll_modes="rampage:doll_claws:1"))
    assert st2.meters["doll_mode"] == "rampage"


def test_boss_stun_cancels_mode_action_same_round():
    cfg = flat_cfg(modes="overload:overload:1", modes_norepeat="")
    st = mk2(n_players=1, enemy_hp=500, gimmicks=("ice",), enemy_traits={"modes": 1.0}, chooser=None, cfg=cfg)
    assert st.meters["doll_mode"] == "overload"
    rep = play(st, {"1": ("stun", "e1")}, chooser=None)              # 해킹이 먼저 → 과부하 취소
    assert any(l["result"] == "stunned" and l["actor_id"] == "e1" for l in rep["lines"]) and hp(st, "1") == 40
    assert st.enemies["e1"].mod("stun") is None and st.meters["ice"] == 2 and "hack_cancel" in rep["tips"]
    rep = play(st, {"1": ("guard", None)}, chooser=None)              # 다음 라운드엔 과부하가 들어온다
    assert hp(st, "1") == 40 - 2 and st.meters["ice"] == 0


# ---------------------------------------------------------------------------
# 💡 팁
# ---------------------------------------------------------------------------
def test_tips_fire_once_per_battle_and_can_be_off():
    st = mk2(n_players=2, enemy_hp=50, chooser=always("atk"))
    rep = play(st, {"1": ("atk", "e1"), "2": ("stun", "e1")}, chooser=always("atk"))
    assert "combo" in rep["tips"] and "hack_cancel" in rep["tips"] and "combo" in st.meters["tips_seen"]
    assert "💡" in R.tip_lines(rep["tips"])[0]
    rep = play(st, {"1": ("atk", "e1"), "2": ("stun", "e1")}, chooser=always("atk"))
    assert "combo" not in rep["tips"] and "hack_cancel" not in rep["tips"]
    rep = play(st, {"1": ("guard", None), "2": ("guard", None)}, chooser=always("atk"))
    assert "shield" in rep["tips"] and "firewall_ready" in rep["tips"]          # 방어 2 → 게이지 2 ≥ 스캔 비용 1
    rep = play(st, {"1": ("guard", None), "2": ("guard", None)}, chooser=always("atk"))
    assert "shield" not in rep["tips"] and "firewall_ready" not in rep["tips"]
    st2 = mk2(n_players=2, enemy_hp=50, cfg=flat_cfg(tips=False))
    rep = play(st2, {"1": ("atk", "e1"), "2": ("stun", "e1")})
    assert rep["tips"] == [] and "tips_seen" in st2.meters


# ---------------------------------------------------------------------------
# 튜토리얼
# ---------------------------------------------------------------------------
def _rows():
    return [
        {"script_id": "t", "round": "1", "coach": "첫 수업", "enemy_script": "e1:atk", "guaranteed": "stun", "setup": "",
         "goal": "card:stun", "success": "잘했어요", "fail": "아쉽네요"},
        {"script_id": "t", "round": "2", "coach": "콤보 수업", "enemy_script": "", "guaranteed": "atk",
         "setup": "enemy_mod:e1:stun;firewall:3", "goal": "combo", "success": "콤보!", "fail": "콤보 없음"},
    ]


def test_tutorial_script_parse_and_validation():
    cards = mk_cards2()
    script = T.parse_script(_rows(), cards)["t"]
    assert [s["round"] for s in script] == [1, 2] and script[0]["enemies"] == {"e1": "atk"} and script[1]["setup"][0]["kind"] == "enemy_mod"
    bad = _rows(); bad[0]["goal"] = "fly"
    with pytest.raises(T.ScriptError):
        T.parse_script(bad, cards)
    bad = _rows(); bad[1]["round"] = "3"
    with pytest.raises(T.ScriptError):
        T.parse_script(bad, cards)
    bad = _rows(); bad[0]["guaranteed"] = "nope"
    with pytest.raises(T.ScriptError):
        T.parse_script(bad, cards)
    bad = _rows(); bad[1]["setup"] = "enemy_mod:e1:fly"
    with pytest.raises(T.ScriptError):
        T.parse_script(bad, cards)
    assert T.parse_script([], cards) == {}


def test_tutorial_scripted_enemies_guaranteed_cards_setup_goals_and_end():
    cards = mk_cards2()
    script = T.parse_script(_rows(), cards)["t"]
    cfg = flat_cfg(hand_size=3)
    st = mk2(n_players=1, enemy_hp=500, gimmicks=("tutorial",), cfg=cfg, chooser=None, script=script)
    assert st.script and st.intents["e1"]["kind"] == "attack" and st.enemy_choices["e1"].card_id == "atk"
    assert T.coach_text(st) == "첫 수업"
    hand = E.draw_hand(st, "1", random.Random(1))
    assert hand[0] == "stun" and len(hand) == 3 and E.hand_for(st, "1")[0] == "stun"
    rep = play(st, {"1": ("stun", "e1")}, chooser=None)
    assert rep["tutorial"] == {"round": 1, "goal": "card", "ok": True, "text": "잘했어요"}
    assert "과제 성공" in R.tutorial_line(rep["tutorial"])
    # 2라운드: setup 이 적을 기절시키고 방화벽을 채운다, 보장 카드 atk
    assert st.round == 2 and st.intents["e1"]["kind"] == "stunned" and st.meters["firewall"] == 3
    assert E.draw_hand(st, "1", random.Random(1))[0] == "atk" and T.coach_text(st) == "콤보 수업"
    rep = play(st, {"1": ("atk", "e1")}, chooser=None)
    assert rep["tutorial"]["ok"] and rep["tutorial"]["text"] == "콤보!"
    assert st.status == E.ENDED and st.winner == E.ALLY and st.end_reason == "tutorial" and "튜토리얼" in R.winner_title(st)
    # 실패 문장
    st = mk2(n_players=1, enemy_hp=500, gimmicks=("tutorial",), cfg=cfg, chooser=None, script=script)
    rep = play(st, {"1": ("guard", None)}, chooser=None)
    assert rep["tutorial"]["ok"] is False and rep["tutorial"]["text"] == "아쉽네요"
    with pytest.raises(E.RuleError) as ei:
        mk2(n_players=1, gimmicks=("tutorial",), cfg=cfg, chooser=None)
    assert ei.value.code == "NO_SCRIPT"


def test_tutorial_goal_evaluators():
    cards = mk_cards2()
    rows = [
        {"script_id": "g", "round": "1", "coach": "", "enemy_script": "e1:guard", "guaranteed": "", "setup": "", "goal": "kind:defend", "success": "s", "fail": "f"},
        {"script_id": "g", "round": "2", "coach": "", "enemy_script": "e1:guard", "guaranteed": "", "setup": "", "goal": "firewall_used", "success": "s", "fail": "f"},
        {"script_id": "g", "round": "3", "coach": "", "enemy_script": "e1:guard", "guaranteed": "", "setup": "", "goal": "all_submitted", "success": "s", "fail": "f"},
        {"script_id": "g", "round": "4", "coach": "", "enemy_script": "e1:heal", "guaranteed": "", "setup": "enemy_hp:e1:3", "goal": "finish_nonlethal", "success": "s", "fail": "f"},
    ]
    script = T.parse_script(rows, cards)["g"]
    st = mk2(n_players=2, enemy_hp=500, gimmicks=("tutorial", "nonlethal"), cfg=flat_cfg(), chooser=None, script=script)
    assert play(st, {"1": ("guard", None)}, chooser=None)["tutorial"]["ok"]
    st.meters["firewall"] = 9
    E.use_firewall(st, "1", "cover")
    assert play(st, {"1": ("guard", None)}, chooser=None)["tutorial"]["ok"]
    st.enemies["e1"].hp = 3
    rep = play(st, {"1": ("atk", "e1")}, chooser=None)
    assert rep["tutorial"]["ok"] is False                                   # P2 가 대기 → 전원 제출 아님
    atk = next(l for l in rep["lines"] if l["card_id"] == "atk")
    assert atk["targets"][0]["hp"] == 1 and not atk["targets"][0]["killed"]   # 마지막 수업 전엔 쓰러지지 않는다 (HP 바닥 1)
    assert st.round == 4 and st.enemies["e1"].hp == 3                      # 4라운드 setup 이 HP 를 3 으로 맞춘다
    rep = play(st, {"1": ("knee", "e1"), "2": ("guard", None)}, chooser=None)
    assert rep["tutorial"]["ok"] and st.status == E.ENDED and st.end_reason == "ko"
