"""적 AI 점수 함수 테스트."""
import random

from cardbattle import ai
from cardbattle import engine as E

from tests.test_cardbattle_engine import flat_cfg, mk_cards


def mk(personality="aggressive", me_hp=30, players=2):
    cfg = flat_cfg()
    ps = [E.player_combatant(str(i + 1), {"max_hp": 40, "physics": 12 + i}, f"P{i + 1}", cfg) for i in range(players)]
    me = E.Combatant("e1", "적", E.ENEMY, 45, me_hp, physics=10, magic=10, personality=personality)
    st = E.new_battle(ps, [me], mk_cards(), cfg, host_id="99")
    st.status = E.ENEMY_TURN
    return st, st.enemies["e1"]


def test_defensive_low_hp_prefers_defend():
    st, me = mk("defensive", me_hp=5)
    st.last_player_kinds = ["attack", "attack"]
    ch = ai.choose(st, me, ["atk", "guard", "jam"], random.Random(1))
    assert ch.card_id == "guard" and ch.target_id == "e1"


def test_aggressive_prefers_killing_blow_target():
    st, me = mk("aggressive")
    st.players["2"].hp = 3
    ch = ai.choose(st, me, ["atk", "guard", "jam"], random.Random(1))
    assert ch.card_id == "atk" and ch.target_id == "2"


def test_hacker_prefers_hack_on_undebuffed_target():
    st, me = mk("hacker")
    st.players["2"].modifiers.append(E.Modifier("atk_down", 0.5, 2, 1))
    ch = ai.choose(st, me, ["atk", "jam", "guard"], random.Random(1))
    assert ch.card_id == "jam" and ch.target_id == "1"             # 디버프 없는 쪽


def test_already_shielded_skips_guard_and_heal_needs_wounded():
    st, me = mk("defensive", me_hp=10)
    me.modifiers.append(E.Modifier("shield", 0.5, 1, 1))
    ch = ai.choose(st, me, ["guard", "atk"], random.Random(1))
    assert ch.card_id == "atk"
    st2, me2 = mk("defensive", me_hp=45)
    ctx = ai.build_context(st2, me2)
    assert ai.score_card(st2.cards["heal"], ctx, st2.config) == 0.0


def test_choose_targets_are_valid_and_seeded_reproducible():
    st, me = mk("aggressive", players=3)
    st.config["ai_noise"] = 0.5
    st.players["3"].hp = 0
    hand = ["atk", "jam", "guard", "heal"]
    a = ai.choose(st, me, hand, random.Random(7))
    b = ai.choose(st, me, hand, random.Random(7))
    assert (a.card_id, a.target_id) == (b.card_id, b.target_id)
    card = st.cards[a.card_id]
    if E.needs_target(card):
        assert a.target_id in {t.id for t in E.valid_targets(st, me, card)}
        assert a.target_id != "3"
    assert ai.choose(st, me, [], random.Random(1)) is None
