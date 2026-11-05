"""긱 경제 (무기·준비 단계·현장 개조·보수), 규칙 단순화 (글리치 1회·⚠ 과잉 위험·방화벽 옵션), 기절 콤보."""
import json
import random

import pytest

from cardbattle import engine as E
from cardbattle import gig
from cardbattle import render as R

from tests.test_cardbattle_engine import always, enemy_plays, flat_cfg, hp, mk_cards, play, wait_chooser


class SeqRng:
    """randint 는 미리 준 순서대로, 나머지는 결정적."""

    def __init__(self, ints=(), rnd=0.5):
        self.ints = list(ints)
        self.rnd = rnd

    def random(self):
        return self.rnd

    def randint(self, a, b):
        v = self.ints.pop(0) if self.ints else a
        return min(b, max(a, v))

    def choice(self, seq):
        return seq[0]

    def choices(self, population, weights=None, k=1):
        return [population[0]]


def mk_cards2():
    cards = mk_cards()
    cards["wall"].effects.append(E.Effect("cleanse", 1))
    extra = [
        E.Card(E.FIELD_MOD_CARD, "현장 개조", "special", "self", "none", "fixed", 0, [E.Effect("field_mod", 1)]),
        E.Card("ambush", "기습", "hack", "all_enemy", "magic", "fixed", 0,
               [E.Effect("scatter_damage", 4, count=2), E.Effect("daemon", 1, count=1)]),
        E.Card("seize", "장악", "hack", "all_enemy", "magic", "fixed", 0,
               [E.Effect("ice", 1), E.Effect("daemon", 1, count=2)]),
        E.Card("breach", "침입", "hack", "enemy", "magic", "fixed", 0,
               [E.Effect("firewall_drain", 2), E.Effect("firewall_lock", 1)]),
        E.Card("overload", "과부하", "attack", "all_enemy", "magic", "fixed", 0,
               [E.Effect("damage", 5), E.Effect("expose", 1)]),
    ]
    for c in extra:
        cards[c.id] = c
    return cards


def mk2(n_players=2, n_enemies=1, host="99", cfg=None, enemy_hp=30, gimmicks=(), chooser=wait_chooser,
        enemy_traits=None, enemy_agility=5, rng=None, **kw):
    cfg = cfg or flat_cfg()
    players = [E.player_combatant(str(i + 1), {"max_hp": 40, "agility": 10 + i}, f"P{i + 1}", cfg)
               for i in range(n_players)]
    enemies = [E.Combatant(f"e{i + 1}", f"적{i + 1}", E.ENEMY, enemy_hp, enemy_hp, agility=enemy_agility,
                           traits=dict(enemy_traits or {})) for i in range(n_enemies)]
    st = E.new_battle(players, enemies, mk_cards2(), cfg, host_id=host, battle_id="t", battle_name="테스트",
                      gimmicks=gimmicks, rng=rng or random.Random(1), **kw)
    if chooser is not None and st.status == E.ROUND_OPEN:
        E.open_round(st, random.Random(1), chooser)
    return st


# ---------------------------------------------------------------------------
# 규칙 단순화
# ---------------------------------------------------------------------------
def test_split_list_and_count_effects():
    from cardbattle import data as D
    assert E.split_list("a;b, c ;;d") == ["a", "b", "c", "d"]
    effs = D.parse_effects("scatter_damage:4:3;daemon:1:2;shield:0.5:1")
    assert (effs[0].kind, effs[0].value, effs[0].count, effs[0].turns) == ("scatter_damage", 4.0, 3, 1)
    assert (effs[1].kind, effs[1].count) == ("daemon", 2) and effs[2].turns == 1 and effs[2].count == 1


def test_overkill_risk_helper():
    st = mk2(n_players=1, enemy_hp=3, gimmicks=("nonlethal",), cfg=flat_cfg(overkill_margin=5))
    me, e = st.players["1"], st.enemies["e1"]
    assert E.overkill_risk(st, me, st.cards["atk"], e)                  # 10 - 3 = 7 ≥ 5
    assert not E.overkill_risk(st, me, st.cards["knee"], e)             # 비살상 태그
    e.hp = 20
    assert not E.overkill_risk(st, me, st.cards["atk"], e)
    assert not E.overkill_risk(st, me, st.cards["over"], e)             # 살상이지만 14 < 20
    e.hp = 14
    assert E.overkill_risk(st, me, st.cards["over"], e)                 # 살상: 쓰러뜨릴 수 있으면 위험
    e.hp = 10
    assert not E.overkill_risk(st, me, st.cards["atk"], e)              # 10 - 10 = 0
    e.modifiers.append(E.Modifier("stun", 1, 1, 1))
    assert E.overkill_risk(st, me, st.cards["atk"], e)                  # 콤보 ×1.5 → 15 - 10 = 5
    st.gimmicks = []
    assert not E.overkill_risk(st, me, st.cards["atk"], e)              # 비살상 전투가 아니면 없음
    assert "⚠ 과잉 위험" in R.card_detail(mk2(n_players=1, enemy_hp=1, gimmicks=("nonlethal",), cfg=flat_cfg(overkill_margin=5)),
                                       st.cards["atk"], me)


def test_firewall_options_gate_and_lock():
    st = mk2(n_players=1, cfg=flat_cfg(firewall_options="cover;reboot"))
    st.meters["firewall"] = 9
    assert E.enabled_firewall_options(st) == ["cover", "reboot"]
    with pytest.raises(E.RuleError) as ei:
        E.use_firewall(st, "1", "aim")
    assert ei.value.code == "FIREWALL_OFF"
    st.meters["firewall_lock_round"] = st.round
    with pytest.raises(E.RuleError) as ei:
        E.use_firewall(st, "1", "cover")
    assert ei.value.code == "FIREWALL_LOCKED" and E.firewall_locked(st)
    assert "잠김" in R.meters_text(st)[0]
    st.meters["firewall_lock_round"] = 0
    assert E.use_firewall(st, "1", "cover")["option"] == "cover"
    st2 = mk2(n_players=1, cfg=flat_cfg(firewall_options=""))
    assert E.enabled_firewall_options(st2) == list(E.FIREWALL_OPTIONS)


def test_stun_from_previous_round_still_combos_this_round():
    st = mk2(n_players=1, enemy_hp=50, chooser=always("atk"))
    st.enemies["e1"].modifiers.append(E.Modifier("stun", 1, 1, 0))
    E.open_round(st, random.Random(1), always("atk"))
    assert st.intents["e1"]["kind"] == "stunned"
    rep = play(st, {"1": ("atk", "e1")})
    atk = next(l for l in rep["lines"] if l["card_id"] == "atk")
    assert atk["combo"] is True and atk["targets"][0]["amount"] == 15 and hp(st, "e1") == 35
    assert [l["result"] for l in rep["lines"]][0] == "stunned"
    assert st.enemies["e1"].mod("stun") is None                      # 라운드 끝에 풀림


# ---------------------------------------------------------------------------
# 무기 / 준비 단계
# ---------------------------------------------------------------------------
def test_roll_upgrade_table():
    cfg = flat_cfg()
    assert E.roll_upgrade(1, SeqRng([3]), cfg)["delta"] == 1                  # mk.1→2 는 굴림 없이
    assert E.roll_upgrade(2, SeqRng([3]), cfg)["delta"] == -1
    assert E.roll_upgrade(3, SeqRng([1]), cfg)["delta"] == -2
    assert E.roll_upgrade(2, SeqRng([1]), cfg, allow_down=False)["delta"] == 0
    r = E.roll_upgrade(3, SeqRng([10]), cfg)
    assert r["delta"] == 1 and r["refund"]
    assert E.roll_upgrade(2, SeqRng([4]), cfg)["delta"] == 1
    with pytest.raises(E.RuleError) as ei:
        E.roll_upgrade(4, SeqRng([4]), cfg)
    assert ei.value.code == "WEAPON_MAX"
    assert E.clamp_grade(-3, cfg) == 1 and E.clamp_grade(9, cfg) == 4


def test_prep_phase_split_upgrade_ready_finish():
    cfg = flat_cfg(prep_enabled=True, pot_start_per_player=100, price_normal=100, price_black=50, prep_actions=2,
                   weapon_start_max=1, weapon_bonus="0;3;6;10")
    rng = SeqRng([1, 1, 3, 7])                    # 무기 배정 둘 (1,1) → 뒷골목 굴림 3 → (안 씀) 7
    st = mk2(n_players=2, cfg=cfg, rng=rng, chooser=None, weapon_names={"": ["권총"]})
    assert st.status == E.PREP and st.intents == {}
    assert all(p.weapon_grade == 1 and p.weapon_name == "권총" for p in st.players.values())
    assert st.prep["share"] == 100 and st.prep["allowance"] == {"1": 100, "2": 100} and st.meters["pot"] == 0
    with pytest.raises(E.RuleError):
        E.submit_choice(st, "1", "atk", "e1")                        # 준비 단계에는 카드 제출 불가
    e = gig.prep_upgrade(st, "1", "normal", rng)
    assert (e["before"], e["after"], e["allowance"], e["actions_left"]) == (1, 2, 0, 1)
    assert E.damage_range(st.cards["atk"], st.players["1"], cfg) == (13, 13)   # mk.2 +3
    with pytest.raises(E.RuleError) as ei:
        gig.prep_upgrade(st, "1", "normal", rng)
    assert ei.value.code == "PREP_POOR"
    e = gig.prep_upgrade(st, "2", "black", rng)                      # mk.1→2 자동 성공, 굴림 없음
    assert e["roll"] in (None, 0) and e["after"] == 2 and e["allowance"] == 50
    e = gig.prep_upgrade(st, "2", "black", rng)                      # d10 = 3 → 실패, 한 단계 하락
    assert e["roll"] == 3 and e["after"] == 1 and e["allowance"] == 0 and not e["success"]
    with pytest.raises(E.RuleError) as ei:
        gig.prep_upgrade(st, "2", "black", rng)
    assert ei.value.code == "PREP_NO_ACTIONS"
    assert "d10 3" in R.prep_roll_line(e) and "실패" in R.prep_result_text(e)
    assert gig.prep_ready(st, "1") and not gig.prep_ready(st, "1") and not gig.prep_all_ready(st)
    with pytest.raises(E.RuleError) as ei:
        gig.prep_upgrade(st, "1", "black", rng)
    assert ei.value.code == "PREP_READY"
    assert gig.prep_ready(st, "2") and gig.prep_all_ready(st)
    st.prep["allowance"]["1"] = 30                                    # 안 쓴 돈은 금고로
    assert gig.finish_prep(st, rng) is None
    assert st.status == E.ROUND_OPEN and st.round == 1 and st.meters["pot"] == 30 and st.prep["returned"] == 30
    assert st.intents["e1"]["kind"] in E.KINDS or st.intents["e1"]["kind"] == "wait"
    d = st.to_dict()
    st2 = E.BattleState.from_dict(d)
    assert st2.players["1"].weapon_grade == 2 and st2.prep["done"] and st2.to_dict() == d


def test_prep_normal_max_and_weapon_max():
    cfg = flat_cfg(prep_enabled=True, weapon_normal_max=3, weapon_max=4, pot_start_per_player=1000, prep_actions=9)
    st = mk2(n_players=1, cfg=cfg, rng=SeqRng([9, 9]), chooser=None)
    p = st.players["1"]
    p.weapon_grade = 3
    with pytest.raises(E.RuleError) as ei:
        gig.prep_upgrade(st, "1", "normal", SeqRng())
    assert ei.value.code == "WEAPON_NORMAL_MAX"
    assert gig.prep_upgrade(st, "1", "black", SeqRng([9]))["after"] == 4
    with pytest.raises(E.RuleError) as ei:
        gig.prep_upgrade(st, "1", "black", SeqRng([9]))
    assert ei.value.code == "WEAPON_MAX"


def test_field_mod_card_costs_pot_and_fails_safely():
    cfg = flat_cfg(field_mod_cost=50, weapon_max=4)
    st = mk2(n_players=1, chooser=always("atk"), cfg=cfg)
    p = st.players["1"]
    assert E.FIELD_MOD_CARD not in E.hand_for(st, "1")                  # 무기 없음
    p.weapon_grade, p.weapon_name = 2, "권총"
    st.meters["pot"] = 120
    assert E.FIELD_MOD_CARD in E.fixed_cards(st, p)
    rep = play(st, {"1": (E.FIELD_MOD_CARD, None)}, rng=SeqRng([2]), chooser=always("atk"))
    line = rep["lines"][0]
    assert line["result"] == "field_mod" and not line["field_mod"]["success"] and p.weapon_grade == 2
    assert st.meters["pot"] == 70 and p.mod("misfire") is not None and any(ev["kind"] == "field_mod" for ev in rep["events"])
    assert "실패" in R.log_lines(line)[0] and "field_mod" in rep["tips"]
    rep = play(st, {"1": ("atk", "e1")}, rng=SeqRng([], rnd=0.5), chooser=always("atk"))
    assert rep["lines"][0]["result"] == "misfire"                       # 실패의 대가: 다음 공격 불발
    rep = play(st, {"1": (E.FIELD_MOD_CARD, None)}, rng=SeqRng([10]), chooser=always("atk"))
    assert rep["lines"][0]["field_mod"]["success"] and p.weapon_grade == 3 and st.meters["pot"] == 70   # 10 → 환불
    st.meters["pot"] = 10
    assert E.FIELD_MOD_CARD not in E.fixed_cards(st, p)
    st.meters["pot"] = 100
    p.weapon_grade = 4
    assert E.FIELD_MOD_CARD not in E.fixed_cards(st, p)


def test_payout_and_pot_file(tmp_path):
    path = str(tmp_path / "gig.json")
    cfg = flat_cfg(reward_per_player=150, penalty_overkill=50, penalty_loud=20, reward_collapse_ratio=0.5)
    st = mk2(n_players=2, enemy_hp=1, gimmicks=("nonlethal",), cfg=cfg)
    play(st, {"1": ("sweep", None)})
    assert st.status == E.ENDED and st.end_reason == "ko"
    p = gig.payout(st)
    assert (p["base"], p["loud"], p["overkills"], p["penalty"], p["reward"]) == (300, 1, 0, 20, 280)
    assert gig.apply_payout(st, path)["pot_after"] is None and not (tmp_path / "gig.json").exists()   # prep 꺼짐
    st.meters.pop("payout")
    st.config["prep_enabled"] = True
    st.meters["pot"] = 30
    p = gig.apply_payout(st, path)
    assert p["pot_after"] == 310 and gig.load_pot(path) == 310
    assert gig.apply_payout(st, path) is p and gig.load_pot(path) == 310              # 멱등
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh)["history"][0]["battle"] == "t"
    st.end_reason = "collapse"
    assert gig.payout(st)["reward"] == 130
    st.winner = E.ENEMY
    assert gig.payout(st)["reward"] == 0
    st.config["prep_sandbox"] = True
    st.winner = E.ALLY
    st.meters.pop("payout")
    assert gig.apply_payout(st, path)["pot_after"] is None and gig.load_pot(path) == 310
    assert gig.set_pot(42, path) == 42 and gig.load_pot(path) == 42
    with pytest.raises(E.RuleError):
        gig.apply_payout(mk2(n_players=1), path)


def test_new_battle_with_pot_argument_and_sandbox_default():
    cfg = flat_cfg(prep_enabled=True, pot_start_per_player=100)
    st = mk2(n_players=3, cfg=cfg, pot=250, chooser=None)
    assert st.prep["share"] == 83 and st.meters["pot"] == 1 and st.prep["pot_start"] == 250
    st = mk2(n_players=3, cfg=cfg, pot=None, chooser=None)
    assert st.prep["pot_start"] == 300
