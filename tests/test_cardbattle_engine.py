"""카드 전투 엔진·로더·렌더 테스트 (시드 고정 random.Random)."""
import random
from pathlib import Path

import pytest

from cardbattle import config as C
from cardbattle import data as D
from cardbattle import engine as E
from cardbattle import render as R


# ---------------------------------------------------------------------------
# 도우미: 변동 없는 설정 + 손으로 만든 카드
# ---------------------------------------------------------------------------
def flat_cfg(**over):
    cfg = C.build_config()
    cfg.update({"damage_variance": 0, "stat_divisor": 1000, "ai_noise": 0.0, "hand_size": 3})
    cfg.update(over)
    return cfg


def mk_cards():
    return {c.id: c for c in [
        E.Card("atk", "베기", "attack", "enemy", "physics", "both", 10, [E.Effect("damage", 10)]),
        E.Card("sweep", "휩쓸기", "attack", "all_enemy", "physics", "both", 4, [E.Effect("damage", 5)]),
        E.Card("guard", "가드", "defend", "self", "none", "both", 8, [E.Effect("shield", 0.5, 1)]),
        E.Card("wall", "방벽", "defend", "ally", "none", "both", 4, [E.Effect("shield", 0.7, 1)]),
        E.Card("jam", "재밍", "hack", "enemy", "magic", "both", 5, [E.Effect("atk_down", 0.5, 2)]),
        E.Card("mis", "오작동", "hack", "enemy", "magic", "both", 5, [E.Effect("misfire", 1.0, 2)]),
        E.Card("redir", "표적 교란", "hack", "enemy", "magic", "both", 3, [E.Effect("redirect_random", 1, 2)]),
        E.Card("stun", "쇼크", "hack", "enemy", "magic", "both", 2, [E.Effect("stun", 1, 1)]),
        E.Card("heal", "응급 키트", "special", "ally", "none", "both", 4, [E.Effect("heal", 8)]),
        E.Card("over", "오버드라이브", "special", "enemy", "physics", "ally", 2,
               [E.Effect("damage", 14), E.Effect("self_damage", 3)]),
    ]}


def mk_state(n_players=2, n_enemies=1, host="1", cfg=None, enemy_hp=30):
    cfg = cfg or flat_cfg()
    players = [E.player_combatant(str(i + 1), {"max_hp": 40, "agility": 10 + i}, f"P{i + 1}", cfg)
               for i in range(n_players)]
    enemies = [E.Combatant(f"e{i + 1}", f"적{i + 1}", E.ENEMY, enemy_hp, enemy_hp, agility=5) for i in range(n_enemies)]
    return E.new_battle(players, enemies, mk_cards(), cfg, host_id=host, battle_id="t", battle_name="테스트")


def always(card_id):
    """적 chooser: 항상 같은 카드로 첫 생존 플레이어를 노린다."""
    def chooser(st, me, hand, rng):
        card = st.cards[card_id]
        t = E.valid_targets(st, me, card)
        return E.Choice(me.id, card_id, t[0].id if t else None)
    return chooser


def hp(st, uid):
    return st.unit(uid).hp


# ---------------------------------------------------------------------------
# 로더
# ---------------------------------------------------------------------------
def test_parse_effects_grammar():
    effs = D.parse_effects("damage:8; shield:0.5:1 ;stun:1:1:0.3")
    assert [(e.kind, e.value, e.turns, e.chance) for e in effs] == [
        ("damage", 8.0, 1, 1.0), ("shield", 0.5, 1, 1.0), ("stun", 1.0, 1, 0.3)]
    with pytest.raises(D.DataError):
        D.parse_effects("explode:3")
    with pytest.raises(D.DataError):
        D.parse_effects("damage:x")


def test_load_shipped_csvs():
    gd = D.load_game_data(D.CsvSource())
    assert "b01" in gd.battles and gd.battles["b01"].enemies == ["grunt", "grunt"]
    assert gd.battles["b03"].enemies.count("grunt") == 3
    assert all(c.effects for c in gd.cards.values())
    enemies = E.enemy_combatants(gd.battles["b01"], gd.enemies)
    assert [e.id for e in enemies] == ["grunt#1", "grunt#2"]
    assert enemies[0].name.endswith(" A") and enemies[1].name.endswith(" B")


def _write_csvs(d: Path, battles: str, enemies: str = None, cards: str = None):
    (d / "Cards.csv").write_text(cards or "card_id,name,kind,target,stat,pool,weight,effects,desc\n"
                                 "a,베기,attack,enemy,physics,both,1,damage:5,\n", encoding="utf-8")
    (d / "Enemies.csv").write_text(enemies or "enemy_id,name,max_hp,physics,magic,agility,charm,personality,pool\n"
                                   "g,보병,10,10,10,10,10,aggressive,enemy\n", encoding="utf-8")
    (d / "Battles.csv").write_text(battles, encoding="utf-8")


def test_load_rejects_unknown_enemy_and_empty_battle(tmp_path):
    _write_csvs(tmp_path, "battle_id,name,enemies,desc\nb,x,ghost,\n")
    with pytest.raises(D.DataError):
        D.load_game_data(D.CsvSource(tmp_path))
    _write_csvs(tmp_path, "battle_id,name,enemies,desc\nb,x,g*0,\n")
    with pytest.raises(D.DataError):
        D.load_game_data(D.CsvSource(tmp_path))
    _write_csvs(tmp_path, "battle_id,name,enemies,desc\nb,x,g*2;g,\n")
    gd = D.load_game_data(D.CsvSource(tmp_path))
    assert gd.battles["b"].enemies == ["g", "g", "g"]


def test_make_source_env_and_fallback(tmp_path, caplog):
    _write_csvs(tmp_path, "battle_id,name,enemies,desc\nb,x,g,\n")
    env = {"CARDBATTLE_SOURCE": "csv", "CARDBATTLE_DATA_DIR": str(tmp_path)}
    assert isinstance(D.make_source(None, env), D.CsvSource)
    env["CARDBATTLE_SOURCE"] = "sheet"
    assert isinstance(D.make_source(None, env), D.CsvSource)       # 스프레드시트 없음 → CSV

    class NoTabs:
        def worksheet(self, name):
            raise KeyError(name)

    src = D.make_source(NoTabs(), env)
    assert isinstance(src, D.FallbackSource)
    gd = D.load_game_data(src)                                      # 탭 없음 → CSV 폴백
    assert "b" in gd.battles


# ---------------------------------------------------------------------------
# 드로우 / 제출
# ---------------------------------------------------------------------------
def test_draw_hand_deterministic_and_sticky():
    st = mk_state(host="99")
    rng = random.Random(1)
    h1 = E.draw_hand(st, "1", rng)
    assert len(h1) == 3 and len(set(h1)) == 3
    assert E.draw_hand(st, "1", random.Random(999)) == h1          # 다시 뽑아도 같은 패
    st2 = mk_state(host="99")
    assert E.draw_hand(st2, "1", random.Random(1)) == h1            # 같은 시드 → 같은 패
    assert "over" in {c.id for c in E.pool_for(st, st.players["1"])}
    assert "over" not in {c.id for c in E.pool_for(st, st.enemies["e1"])}   # pool=ally 는 적이 못 뽑음


def test_host_sees_all_cards_and_nothing_stored():
    st = mk_state(host="1")
    hand = E.draw_hand(st, "1", random.Random(1))
    assert set(hand) == {c.id for c in E.pool_for(st, st.players["1"])}
    assert "1" not in st.hands
    E.submit_choice(st, "1", "stun", "e1")                          # 패에 없어도 주최자는 가능
    st.config["admin_sees_all"] = False
    assert len(E.draw_hand(st, "1", random.Random(1))) == 3


def test_submit_validation():
    st = mk_state(n_enemies=2, host="99")
    st.hands["1"] = ["atk", "guard", "heal"]
    with pytest.raises(E.RuleError) as ei:
        E.submit_choice(st, "1", "stun", "e1")
    assert ei.value.code == "NOT_IN_HAND"
    with pytest.raises(E.RuleError) as ei:
        E.submit_choice(st, "1", "atk")                             # 적이 둘 → 대상 필요
    assert ei.value.code == "NO_TARGET"
    with pytest.raises(E.RuleError) as ei:
        E.submit_choice(st, "1", "atk", "2")                        # 아군을 공격 대상으로
    assert ei.value.code == "BAD_TARGET"
    ch = E.submit_choice(st, "1", "guard")
    assert ch.target_id == "1"
    ch = E.submit_choice(st, "1", "atk", "e2")                      # 덮어쓰기
    assert st.choices["1"].card_id == "atk" and ch.target_id == "e2"
    st.enemies["e2"].hp = 0
    st.hands["2"] = ["atk"]
    assert E.submit_choice(st, "2", "atk").target_id == "e1"        # 생존 대상 하나 → 자동
    with pytest.raises(E.RuleError) as ei:
        E.submit_choice(st, "zzz", "atk")
    assert ei.value.code == "NOT_PLAYER"


def test_cancel_and_all_chosen():
    st = mk_state(host="99")
    st.hands = {"1": ["atk"], "2": ["guard"]}
    assert not E.all_chosen(st)
    E.submit_choice(st, "1", "atk")
    assert not E.all_chosen(st)
    E.submit_choice(st, "2", "guard")
    assert E.all_chosen(st)
    assert E.cancel_choice(st, "2") is True
    assert not E.all_chosen(st) and E.cancel_choice(st, "2") is False
    st.players["2"].hp = 0                                          # 사망자는 제외
    assert E.all_chosen(st)


# ---------------------------------------------------------------------------
# 정산
# ---------------------------------------------------------------------------
def test_phase_order_hack_then_defend_then_attack_and_stun_skips_enemy():
    st = mk_state(n_players=3, host="99")
    st.hands = {"1": ["atk"], "2": ["guard"], "3": ["stun"]}       # 민첩: 3 > 2 > 1 이지만 단계가 우선
    E.submit_choice(st, "1", "atk")
    E.submit_choice(st, "2", "guard")
    E.submit_choice(st, "3", "stun")
    rep = E.resolve_ally_turn(st, random.Random(1))
    assert [l["card_id"] for l in rep["lines"]] == ["stun", "guard", "atk"]
    assert hp(st, "e1") == 20
    assert st.status == E.ENEMY_TURN and st.turn_no == 2
    rep2 = E.run_enemy_turn(st, random.Random(1), chooser=always("atk"))
    assert rep2["lines"][0]["result"] == "stunned"
    assert all(hp(st, p) == 40 for p in ("1", "2", "3"))
    assert st.enemies["e1"].mod("stun") is None                     # 소모됨
    assert st.status == E.ALLY_TURN and st.turn_no == 3


def test_defend_protects_next_enemy_turn_then_expires():
    st = mk_state(n_players=1, host="99")
    st.hands = {"1": ["guard"]}
    E.submit_choice(st, "1", "guard")
    E.resolve_ally_turn(st, random.Random(1))
    assert st.players["1"].mod("shield") is not None               # 생성 턴엔 안 깎임
    E.run_enemy_turn(st, random.Random(1), chooser=always("atk"))
    assert hp(st, "1") == 35                                        # 10 × 0.5
    assert st.players["1"].mod("shield") is None                   # 적 턴 끝에 만료
    st.hands = {"1": ["heal"]}
    E.submit_choice(st, "1", "heal", "1")
    E.resolve_ally_turn(st, random.Random(1))
    E.run_enemy_turn(st, random.Random(1), chooser=always("atk"))
    assert hp(st, "1") == 30                                        # 40 → 35 → 40(힐 8, 최대치) → 30


def test_atk_down_lasts_two_enemy_actions():
    st = mk_state(n_players=1, host="99")
    rng = random.Random(1)
    st.hands = {"1": ["jam"]}
    E.submit_choice(st, "1", "jam")
    E.resolve_ally_turn(st, rng)
    E.run_enemy_turn(st, rng, chooser=always("atk"))
    assert hp(st, "1") == 35
    st.hands = {"1": ["heal"]}; E.submit_choice(st, "1", "heal", "1"); E.resolve_ally_turn(st, rng)
    E.run_enemy_turn(st, rng, chooser=always("atk"))
    assert hp(st, "1") == 35                                        # 40 → 35 (두 번째 감소)
    assert st.enemies["e1"].mod("atk_down") is None
    st.hands = {"1": ["heal"]}; E.submit_choice(st, "1", "heal", "1"); E.resolve_ally_turn(st, rng)
    E.run_enemy_turn(st, rng, chooser=always("atk"))
    assert hp(st, "1") == 30                                        # 40 → 30 (원래 피해)


def test_misfire_consumed_on_use():
    st = mk_state(n_players=1, host="99")
    rng = random.Random(1)
    st.hands = {"1": ["mis"]}
    E.submit_choice(st, "1", "mis")
    E.resolve_ally_turn(st, rng)
    rep = E.run_enemy_turn(st, rng, chooser=always("atk"))
    assert rep["lines"][0]["result"] == "misfire" and hp(st, "1") == 40
    assert st.enemies["e1"].mod("misfire") is None
    st.hands = {"1": ["guard"]}; E.submit_choice(st, "1", "guard"); E.resolve_ally_turn(st, rng)
    rep = E.run_enemy_turn(st, rng, chooser=always("atk"))
    assert rep["lines"][0]["result"] == "hit"


def test_redirect_random_retargets_and_dead_target_falls_back():
    st = mk_state(n_players=2, n_enemies=2, host="99")
    st.hands = {"1": ["redir"], "2": ["guard"]}
    E.submit_choice(st, "1", "redir", "e1")
    E.submit_choice(st, "2", "guard")
    E.resolve_ally_turn(st, random.Random(1))
    rep = E.run_enemy_turn(st, random.Random(1), chooser=always("atk"))
    line = next(l for l in rep["lines"] if l["actor_id"] == "e1")
    assert line["result"] == "redirect"
    assert line["targets"][0]["id"] in {"1", "2", "e2"}             # 자신 제외 양편 생존자
    # 고른 대상이 정산 전에 죽으면 생존 적으로 전환
    st.hands = {"1": ["atk"], "2": ["atk"]}
    E.submit_choice(st, "1", "atk", "e2")
    E.submit_choice(st, "2", "atk", "e2")
    st.enemies["e2"].hp = 1
    rep = E.resolve_ally_turn(st, random.Random(1))
    second = [l for l in rep["lines"] if l["kind"] == "attack"][1]
    assert second["result"] == "redirect" and second["targets"][0]["id"] == "e1"


def test_sweep_hits_all_and_overdrive_recoil():
    st = mk_state(n_players=2, n_enemies=3, host="99")
    st.hands = {"1": ["sweep"], "2": ["over"]}
    E.submit_choice(st, "1", "sweep")
    E.submit_choice(st, "2", "over", "e1")
    rep = E.resolve_ally_turn(st, random.Random(1))
    sweep = next(l for l in rep["lines"] if l["card_id"] == "sweep")
    assert len(sweep["targets"]) == 3 and all(t["amount"] == 5 for t in sweep["targets"])
    assert hp(st, "e1") == 30 - 5 - 14 and hp(st, "2") == 37


def test_unchosen_players_wait_and_winner():
    st = mk_state(n_players=2, host="99", enemy_hp=10)
    st.hands = {"1": ["atk"], "2": ["atk"]}
    E.submit_choice(st, "1", "atk")
    rep = E.resolve_ally_turn(st, random.Random(1))
    assert [l["result"] for l in rep["lines"]] == ["hit", "wait"]
    assert st.status == E.ENDED and st.winner == E.ALLY and rep["winner"] == E.ALLY
    with pytest.raises(E.RuleError):
        E.submit_choice(st, "1", "atk")


def test_enemy_wins_when_players_dead():
    st = mk_state(n_players=1, host="99")
    st.players["1"].hp = 5
    st.hands = {"1": ["guard"]}
    E.submit_choice(st, "1", "guard")
    E.resolve_ally_turn(st, random.Random(1))
    E.run_enemy_turn(st, random.Random(1), chooser=always("atk"))
    assert st.status == E.ENDED and st.winner == E.ENEMY


def test_state_roundtrip():
    st = mk_state(n_players=2, n_enemies=2, host="1")
    st.hands = {"2": ["atk", "jam", "guard"]}
    E.submit_choice(st, "2", "jam", "e2")
    st.players["1"].modifiers.append(E.Modifier("shield", 0.5, 1, 1, "1"))
    st.log.append({"turn_no": 1, "side": "ally", "lines": [], "hp": {}, "winner": None})
    d = st.to_dict()
    st2 = E.BattleState.from_dict(d)
    assert st2.to_dict() == d
    assert isinstance(st2.players["1"].modifiers[0], E.Modifier)
    assert isinstance(st2.cards["atk"].effects[0], E.Effect)
    assert st2.choices["2"].target_id == "e2"


def test_player_combatant_sheet_mapping():
    cfg = flat_cfg()
    c = E.player_combatant("7", {"max_hp": "39", "current_hp": "12", "physics": "nan", "magic": "", "agility": 15.0},
                           "닉", cfg)
    assert (c.name, c.max_hp, c.hp, c.physics, c.magic, c.agility) == ("닉", 39, 39, 10, 10, 15)
    c = E.player_combatant("7", {"max_hp": 39, "current_hp": 12}, "닉", flat_cfg(use_current_hp=True))
    assert c.hp == 12
    c = E.player_combatant("8", None, "무명", flat_cfg(default_hp=25))
    assert c.max_hp == 25 and c.side == E.ALLY


# ---------------------------------------------------------------------------
# 렌더
# ---------------------------------------------------------------------------
def test_log_lines_format_and_particles():
    st = mk_state(n_players=2, host="99")
    st.hands = {"1": ["atk"], "2": ["guard"]}
    E.submit_choice(st, "1", "atk")
    E.submit_choice(st, "2", "guard")
    rep = E.resolve_ally_turn(st, random.Random(1))
    text = R.report_text(rep)
    assert "**P2**님이 **가드**를 사용했습니다. HP 40/40" in text          # 자기 대상 → 이름 생략
    assert "**P1**님이 **베기**를 사용했습니다. 적1 HP 20/30 (-10)" in text
    rep2 = E.run_enemy_turn(st, random.Random(1), chooser=always("atk"))
    text2 = R.report_text(rep2)
    assert "**적1**이(가) **베기**를 사용했습니다. P1 HP 30/40 (-10)" in text2   # 숫자로 끝남 → 묶음 조사
    line = {"actor_id": "x", "actor_name": "영희", "side": "ally", "card_id": "w", "card_name": "방벽",
            "kind": "defend", "result": "shield", "targets": [{"id": "y", "name": "철수", "kind": "shield",
                                                                "amount": 0.7, "hp": 3, "max_hp": 9, "killed": False}]}
    assert R.log_lines(line) == ["**영희**님이 **방벽**을 사용했습니다. 철수 HP 3/9 (다음 상대 턴 피해 70% 감소)"]
    board = R.hp_board(st)
    assert "🟢 아군" in board and "🔴 적" in board
    assert "선택 현황: 0/2 완료" in R.turn_open_text(st)
    assert len(R.chunks("a\n" * 3000, 100)) > 1
