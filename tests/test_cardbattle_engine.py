"""카드 전투 엔진·로더·렌더 테스트 (시드 고정 random.Random).

라운드 혼합 정산(해킹 → 방어 → 공격, 민첩 순), 수정자 만료, 기믹(비살상·NCPD·도주·끌고 가기·돌·단말기),
방화벽, 콤보를 결정적인 숫자로 검사한다.
"""
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
    cfg.update({"damage_variance": 0, "stat_divisor": 1000, "ai_noise": 0.0, "hand_size": 3,
                "prep_enabled": False, "firewall_options": "cover;reboot;aim;scan;maxdoc",
                "admin_sees_all": True})
    cfg.update(over)
    return cfg


def mk_cards():
    return {c.id: c for c in [
        E.Card("atk", "근접 타격", "attack", "enemy", "physics", "both", 10, [E.Effect("damage", 10)],
               combo=E.Combo("target_has", "stun", "dmg_x", 1.5)),
        E.Card("knee", "관절 치기", "attack", "enemy", "physics", "ally", 6, [E.Effect("damage", 5)], tags=["nonlethal"]),
        E.Card("sweep", "제압 사격", "attack", "all_enemy", "physics", "both", 4, [E.Effect("damage", 5)], tags=["loud"]),
        E.Card("guard", "엄폐", "defend", "self", "none", "both", 8, [E.Effect("shield", 0.5, 1)]),
        E.Card("wall", "ICE", "defend", "ally", "none", "both", 4, [E.Effect("shield", 0.7, 1)]),
        E.Card("jam", "무기 글리치", "hack", "enemy", "magic", "both", 5, [E.Effect("atk_down", 0.5, 2)]),
        E.Card("mis", "고장", "hack", "enemy", "magic", "both", 5, [E.Effect("misfire", 1.0, 2)]),
        E.Card("redir", "적대 인식", "hack", "enemy", "magic", "both", 3, [E.Effect("redirect_random", 1, 2)]),
        E.Card("stun", "시스템 리셋", "hack", "enemy", "magic", "both", 2, [E.Effect("stun", 1, 1)]),
        E.Card("heal", "맥스닥", "special", "ally", "none", "both", 4, [E.Effect("heal", 8)]),
        E.Card("over", "돌격", "special", "enemy", "physics", "ally", 2,
               [E.Effect("damage", 14), E.Effect("self_damage", 3)], tags=["lethal"]),
        E.Card(E.PASS_CARD, "단말기 넘기기", "special", "ally", "none", "fixed", 0, [E.Effect("pass_terminal", 1)]),
        E.Card(E.BREAK_CARD, "떨쳐내기", "special", "self", "none", "fixed", 0, [E.Effect("break_free", 1)]),
        E.Card("doll_cling", "매달리기", "special", "enemy", "none", "fixed", 0,
               [E.Effect("damage", 3), E.Effect("grapple", 1)]),
        E.Card("doll_claws", "손톱 연타", "attack", "enemy", "physics", "fixed", 0,
               [E.Effect("damage", 6), E.Effect("damage", 6)]),
        E.Card("doll_reboot", "리부트", "special", "self", "none", "fixed", 0, [E.Effect("noop", 0)]),
    ]}


def always(card_id, target_first=True):
    """적 chooser: 항상 같은 카드로 첫 생존 플레이어를 노린다."""
    def chooser(st, me, hand, rng):
        card = st.cards[card_id]
        t = E.valid_targets(st, me, card)
        return E.Choice(me.id, card_id, t[0].id if t else None)
    return chooser


def wait_chooser(st, me, hand, rng):
    return None


def mk_state(n_players=2, n_enemies=1, host="1", cfg=None, enemy_hp=30, gimmicks=(), chooser=wait_chooser,
             enemy_traits=None, enemy_agility=5):
    cfg = cfg or flat_cfg()
    players = [E.player_combatant(str(i + 1), {"max_hp": 40, "agility": 10 + i}, f"P{i + 1}", cfg)
               for i in range(n_players)]
    enemies = [E.Combatant(f"e{i + 1}", f"적{i + 1}", E.ENEMY, enemy_hp, enemy_hp, agility=enemy_agility,
                           traits=dict(enemy_traits or {})) for i in range(n_enemies)]
    return E.new_battle(players, enemies, mk_cards(), cfg, host_id=host, battle_id="t", battle_name="테스트",
                        gimmicks=gimmicks, rng=random.Random(1)) if chooser is None else _new_with(
        players, enemies, cfg, host, gimmicks, chooser)


def _new_with(players, enemies, cfg, host, gimmicks, chooser):
    """new_battle 은 open_round 에서 ai.choose 를 쓰므로, 테스트용 chooser 로 라운드를 다시 연다."""
    st = E.new_battle(players, enemies, mk_cards(), cfg, host_id=host, battle_id="t", battle_name="테스트",
                      gimmicks=gimmicks, rng=random.Random(1))
    E.open_round(st, random.Random(1), chooser)
    return st


def enemy_plays(st, card_id, target=None):
    """적 e1 의 이번 라운드 선택을 강제한다."""
    e = st.enemies["e1"]
    card = st.cards[card_id]
    if target is None:
        t = E.valid_targets(st, e, card)
        target = t[0].id if t else None
    st.enemy_choices[e.id] = E.Choice(e.id, card_id, target)
    st.intents[e.id] = {"kind": card.kind, "label": card.kind, "target": target, "card_id": card_id}


def play(st, choices, rng=None, chooser=wait_chooser):
    """플레이어 선택 → 정산. 다음 라운드는 chooser 로 연다."""
    for uid, (cid, tid) in choices.items():
        st.hands[uid] = [cid]
        E.submit_choice(st, uid, cid, tid)
    return E.resolve_round(st, rng or random.Random(1), chooser)


def hp(st, uid):
    return st.unit(uid).hp


# ---------------------------------------------------------------------------
# 로더
# ---------------------------------------------------------------------------
def test_parse_effects_tags_combo_traits_grammar():
    effs = D.parse_effects("damage:8; shield:0.5:1 ;stun:1:1:0.3")
    assert [(e.kind, e.value, e.turns, e.chance) for e in effs] == [
        ("damage", 8.0, 1, 1.0), ("shield", 0.5, 1, 1.0), ("stun", 1.0, 1, 0.3)]
    with pytest.raises(D.DataError):
        D.parse_effects("explode:3")
    with pytest.raises(D.DataError):
        D.parse_effects("damage:x")
    assert D.parse_tags("loud; lethal") == ["loud", "lethal"]
    with pytest.raises(D.DataError):
        D.parse_tags("quiet")
    cb = D.parse_combo("target_has:stun:dmg_x:1.5")
    assert (cb.cond, cb.mod, cb.bonus, cb.value) == ("target_has", "stun", "dmg_x", 1.5)
    assert D.parse_combo("") is None
    with pytest.raises(D.DataError):
        D.parse_combo("target_has:stun:dmg_x")
    with pytest.raises(D.DataError):
        D.parse_combo("target_has:fire:dmg_x:2")
    assert D.parse_traits("cheap_chrome;evade:0.5") == {"cheap_chrome": 1.0, "evade": 0.5}
    with pytest.raises(D.DataError):
        D.parse_traits("wings")
    assert D.parse_gimmicks("nonlethal; ncpd") == ["nonlethal", "ncpd"]
    with pytest.raises(D.DataError):
        D.parse_gimmicks("lava")
    assert C.parse_overrides("chip_stability=4; enemy_hp_mult=1.5") == {"chip_stability": "4", "enemy_hp_mult": "1.5"}
    with pytest.raises(C.ConfigError):
        C.parse_overrides("chip_stability")


def test_load_shipped_csvs():
    gd = D.load_game_data(D.CsvSource(), include_commented=True)   # # 으로 숨긴 보스전도 검증
    assert "scav3" not in D.load_game_data(D.CsvSource()).battles       # 봇에는 숨겨져 있음
    assert gd.battles["scav3"].enemies == ["scav_pipe", "scav_gun", "scav_boss"]
    assert gd.battles["scav3"].gimmicks == ["nonlethal", "ncpd", "drag"]
    assert gd.battles["doll4"].gimmicks == ["nonlethal", "doll", "terminal"]
    assert gd.battles["doll4"].overrides["chip_stability"] == "9"
    assert all(c.effects for c in gd.cards.values())
    assert gd.cards["atk_knee"].tagged("nonlethal") and gd.cards["sp_overdrive"].tagged("lethal")
    assert gd.cards["atk_slash"].combo.mod == "stun"
    assert "doll" in gd.enemies["doll"].traits and gd.enemies["scav_pipe"].traits == {"cheap_chrome": 1.0}
    enemies = E.enemy_combatants(gd.battles["scav4"], gd.enemies)
    assert [e.id for e in enemies] == ["scav_pipe#1", "scav_pipe#2", "scav_gun#1", "scav_boss#1"]
    assert enemies[0].name.endswith(" A") and enemies[1].name.endswith(" B") and enemies[2].name == "스캐빈저 (권총)"
    scaled = E.enemy_combatants(gd.battles["scav6"], gd.enemies, hp_mult=1.5)
    assert scaled[0].max_hp == 60
    for bid in ("scav3", "doll3"):
        b = gd.battles[bid]
        cfg = C.build_config(b.overrides)
        cfg["prep_enabled"] = False
        st = E.new_battle([E.player_combatant("1", {"max_hp": 100}, "A", cfg)],
                          E.enemy_combatants(b, gd.enemies, cfg["enemy_hp_mult"]), gd.cards, cfg, host_id="9",
                          gimmicks=b.gimmicks, rng=random.Random(1))
        assert st.status == E.ROUND_OPEN and st.intents


def _write_csvs(d: Path, battles: str, enemies: str = None, cards: str = None):
    (d / "Cards.csv").write_text(cards or "card_id,name,kind,target,stat,pool,weight,effects,desc\n"
                                 "a,베기,attack,enemy,physics,both,1,damage:5,\n", encoding="utf-8")
    (d / "Enemies.csv").write_text(enemies or "enemy_id,name,max_hp,physics,magic,agility,charm,personality,pool\n"
                                   "g,보병,10,10,10,10,10,aggressive,enemy\n", encoding="utf-8")
    (d / "Battles.csv").write_text(battles, encoding="utf-8")


def test_load_rejects_unknown_enemy_empty_battle_and_missing_fixed_cards(tmp_path):
    _write_csvs(tmp_path, "battle_id,name,enemies,desc\nb,x,ghost,\n")
    with pytest.raises(D.DataError):
        D.load_game_data(D.CsvSource(tmp_path))
    _write_csvs(tmp_path, "battle_id,name,enemies,desc\nb,x,g*0,\n")
    with pytest.raises(D.DataError):
        D.load_game_data(D.CsvSource(tmp_path))
    _write_csvs(tmp_path, "battle_id,name,enemies,desc\nb,x,g*2;g,\n")
    gd = D.load_game_data(D.CsvSource(tmp_path))
    assert gd.battles["b"].enemies == ["g", "g", "g"] and gd.battles["b"].gimmicks == []
    _write_csvs(tmp_path, "battle_id,name,enemies,desc\nb,x,g,\n#h,hidden,g,\n")
    assert list(D.load_game_data(D.CsvSource(tmp_path)).battles) == ["b"]                        # 주석 행 숨김
    assert list(D.load_game_data(D.CsvSource(tmp_path), include_commented=True).battles) == ["b", "h"]
    _write_csvs(tmp_path, "battle_id,name,enemies,gimmicks,desc\nb,x,g,terminal,\n")
    with pytest.raises(D.DataError):               # pass_terminal 카드 없음
        D.load_game_data(D.CsvSource(tmp_path))
    _write_csvs(tmp_path, "battle_id,name,enemies,overrides,desc\nb,x,g,nope=1,\n")
    with pytest.raises(D.DataError):               # 알 수 없는 설정 키
        D.load_game_data(D.CsvSource(tmp_path))


def test_make_source_env_and_fallback(tmp_path):
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
# 드로우 / 제출 / 의도
# ---------------------------------------------------------------------------
def test_draw_hand_deterministic_sticky_and_fixed_pool_excluded():
    st = mk_state(host="99")
    rng = random.Random(1)
    h1 = E.draw_hand(st, "1", rng)
    assert len(h1) == 3 and len(set(h1)) == 3
    assert E.draw_hand(st, "1", random.Random(999)) == h1          # 다시 뽑아도 같은 패
    st2 = mk_state(host="99")
    assert E.draw_hand(st2, "1", random.Random(1)) == h1            # 같은 시드 → 같은 패
    pool = {c.id for c in E.pool_for(st, st.players["1"])}
    assert "over" in pool and E.PASS_CARD not in pool and "doll_claws" not in pool
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


def test_open_round_publishes_enemy_kind_but_not_target():
    st = mk_state(n_players=2, host="99", chooser=always("atk"))
    assert st.status == E.ROUND_OPEN and st.round == 1
    assert st.enemy_choices["e1"].card_id == "atk" and st.enemy_choices["e1"].target_id == "1"
    pub = E.public_intents(st)
    assert pub == [{"id": "e1", "name": "적1", "kind": "attack", "label": "attack", "target": None}]
    st.round_flags["scan"] = True
    assert E.public_intents(st)[0]["target"] == "P1"               # 스캔하면 대상 공개
    st.round_flags.clear()
    st.config["show_intent_target"] = True
    assert E.public_intents(st)[0]["target"] == "P1"
    text = R.intents_text(st)
    assert "적1: 공격 준비 → P1" in text


# ---------------------------------------------------------------------------
# 혼합 정산
# ---------------------------------------------------------------------------
def test_phase_order_then_agility_and_stun_cancels_slower_enemy_same_round():
    st = mk_state(n_players=3, host="99", chooser=always("atk"), enemy_agility=5)
    rep = play(st, {"1": ("atk", "e1"), "2": ("guard", None), "3": ("stun", "e1")})
    # 해킹(3) → 방어(2) → 공격: 민첩 10(P1) vs 5(적) → P1 먼저, 적은 기절로 행동 못 함
    assert [(l["actor_id"], l["result"]) for l in rep["lines"]] == [
        ("3", "hack"), ("2", "shield"), ("1", "hit"), ("e1", "stunned")]
    assert hp(st, "e1") == 15 and all(hp(st, p) == 40 for p in ("1", "2", "3"))   # 기절 콤보 10 × 1.5
    assert st.enemies["e1"].mod("stun") is None                     # 소모됨
    assert st.round == 2 and st.status == E.ROUND_OPEN


def test_faster_enemy_attacks_before_slower_player_kills_it():
    st = mk_state(n_players=1, host="99", chooser=always("atk"), enemy_agility=20, enemy_hp=10)
    rep = play(st, {"1": ("atk", "e1")})
    assert [l["actor_id"] for l in rep["lines"]] == ["e1", "1"]
    assert hp(st, "1") == 30 and hp(st, "e1") == 0 and st.winner == E.ALLY and st.status == E.ENDED


def test_defend_covers_this_round_only():
    st = mk_state(n_players=1, host="99", chooser=always("atk"))
    play(st, {"1": ("guard", None)}, chooser=always("atk"))
    assert hp(st, "1") == 35                                        # 10 × 0.5
    assert st.players["1"].mod("shield") is None                   # 라운드 끝에 만료
    play(st, {"1": ("heal", "1")}, chooser=always("atk"))
    assert hp(st, "1") == 30                                        # 35 → 40(힐) → 30


def test_atk_down_consumed_by_next_attack_only():
    st = mk_state(n_players=1, host="99", chooser=always("atk"))
    play(st, {"1": ("jam", "e1")}, chooser=always("atk"))
    assert hp(st, "1") == 35                                        # 해킹이 공격보다 먼저 → 같은 라운드의 공격에 적용
    assert st.enemies["e1"].mod("atk_down") is None                 # 그 공격에 소모됨
    play(st, {"1": ("heal", "1")}, chooser=always("atk"))
    assert hp(st, "1") == 30                                        # 40 → 30: 다음 라운드는 원래 피해
    # 글리치를 걸어 두고 적이 공격하지 않으면 다음 공격 때까지 남는다
    st = mk_state(n_players=1, host="99", chooser=always("guard"))
    play(st, {"1": ("jam", "e1")}, chooser=always("guard"))
    play(st, {"1": ("guard", None)}, chooser=always("atk"))
    assert st.enemies["e1"].mod("atk_down") is not None
    play(st, {"1": ("heal", "1")}, chooser=always("atk"))
    assert hp(st, "1") == 35 and st.enemies["e1"].mod("atk_down") is None


def test_misfire_consumed_on_use_and_redirect_random():
    st = mk_state(n_players=1, host="99", chooser=always("atk"))
    rep = play(st, {"1": ("mis", "e1")}, chooser=always("atk"))
    assert rep["lines"][-1]["result"] == "misfire" and hp(st, "1") == 40
    assert st.enemies["e1"].mod("misfire") is None
    rep = play(st, {"1": ("guard", None)}, chooser=always("atk"))
    assert rep["lines"][-1]["result"] == "hit"
    st = mk_state(n_players=2, n_enemies=2, host="99", chooser=always("atk"))
    rep = play(st, {"1": ("redir", "e1"), "2": ("guard", None)})
    line = next(l for l in rep["lines"] if l["actor_id"] == "e1")
    assert line["result"] == "redirect" and line["targets"][0]["id"] in {"1", "2", "e2"}


def test_dead_target_falls_back_to_survivor():
    st = mk_state(n_players=2, n_enemies=2, host="99")
    st.enemies["e2"].hp = 1
    rep = play(st, {"1": ("atk", "e2"), "2": ("atk", "e2")})
    attacks = [l for l in rep["lines"] if l["kind"] == "attack"]
    assert attacks[0]["actor_id"] == "2"                            # 민첩 11 > 10
    assert attacks[1]["result"] == "redirect" and attacks[1]["targets"][0]["id"] == "e1"


def test_sweep_hits_all_and_overdrive_recoil():
    st = mk_state(n_players=2, n_enemies=3, host="99")
    rep = play(st, {"1": ("sweep", None), "2": ("over", "e1")})
    sweep = next(l for l in rep["lines"] if l["card_id"] == "sweep")
    assert len(sweep["targets"]) == 3 and all(t["amount"] == 5 for t in sweep["targets"])
    assert hp(st, "e1") == 30 - 5 - 14 and hp(st, "2") == 37


def test_combo_multiplies_when_target_has_modifier():
    st = mk_state(n_players=2, host="99", enemy_hp=50)
    rep = play(st, {"1": ("atk", "e1"), "2": ("stun", "e1")})
    atk = next(l for l in rep["lines"] if l["card_id"] == "atk")
    assert atk["combo"] is True and atk["targets"][0]["amount"] == 15
    assert "⚡콤보!" in R.log_lines(atk)[0]
    assert R.combo_text(st.cards["atk"]) == "▶ 콤보: 기절한 적에게 피해 ×1.5"


def test_unchosen_players_wait_and_winner():
    st = mk_state(n_players=2, host="99", enemy_hp=10)
    rep = play(st, {"1": ("atk", "e1")})
    assert [l["result"] for l in rep["lines"]] == ["hit", "wait"]          # 쓰러진 적의 대기는 생략
    assert st.status == E.ENDED and st.winner == E.ALLY and rep["winner"] == E.ALLY and st.end_reason == "ko"
    with pytest.raises(E.RuleError):
        E.submit_choice(st, "1", "atk")


def test_enemy_wins_when_players_down():
    st = mk_state(n_players=1, host="99", chooser=always("atk"))
    st.players["1"].hp = 5
    play(st, {"1": ("guard", None)})
    assert st.status == E.ENDED and st.winner == E.ENEMY and st.end_reason == "wipe"


def test_heal_can_revive_downed_ally():
    st = mk_state(n_players=2, host="99")
    st.players["2"].hp = 0
    targets = {t.id for t in E.valid_targets(st, st.players["1"], st.cards["heal"])}
    assert targets == {"1", "2"}
    rep = play(st, {"1": ("heal", "2")})
    t = rep["lines"][0]["targets"][0]
    assert t["revived"] and hp(st, "2") == 8


# ---------------------------------------------------------------------------
# 기믹: 비살상 / NCPD / 도주
# ---------------------------------------------------------------------------
def test_overkill_raises_ncpd_and_nonlethal_card_is_exempt():
    st = mk_state(n_players=2, host="99", enemy_hp=30, gimmicks=("nonlethal", "ncpd"))
    st.enemies["e1"].hp = 1
    rep = play(st, {"1": ("knee", "e1")})                           # 5 - 1 = 4 < 10, 비살상 태그
    assert not rep["lines"][0]["targets"][0].get("overkill") and st.meters["ncpd"] == 0.0
    st = mk_state(n_players=2, host="99", enemy_hp=30, gimmicks=("nonlethal", "ncpd"))
    st.enemies["e1"].hp = 1
    rep = play(st, {"2": ("over", "e1")})                           # lethal 태그 → 과잉
    assert rep["lines"][0]["targets"][0]["overkill"] and st.meters["ncpd"] == 1.0
    assert any(ev["kind"] == "ncpd" and ev["why"] == "overkill" for ev in rep["events"])
    st = mk_state(n_players=1, host="99", enemy_hp=30, gimmicks=("nonlethal", "ncpd"))
    st.enemies["e1"].hp = 20
    rep = play(st, {"1": ("atk", "e1")})                            # 10 피해 → 남은 HP 10, 과잉 아님
    assert st.meters["ncpd"] == 0.0
    rep = play(st, {"1": ("sweep", None)})                          # loud +0.5
    assert st.meters["ncpd"] == 0.5


def test_ncpd_full_starts_escape_even_if_enemies_down(monkeypatch):
    cfg = flat_cfg(ncpd_max=1.0, escape_turns=5, escape_wins_needed=2)
    st = mk_state(n_players=3, host="99", cfg=cfg, enemy_hp=1, gimmicks=("nonlethal", "ncpd"))
    rep = play(st, {"1": ("atk", "e1")})                            # 10 - 1 = 9 → 과잉? margin 10 → 아님
    assert st.status == E.ENDED                                     # 적 전멸
    st = mk_state(n_players=3, host="99", cfg=cfg, enemy_hp=1, gimmicks=("nonlethal", "ncpd"))
    rep = play(st, {"1": ("over", "e1")})
    assert rep["reason"] == "ncpd" and st.status == E.ESCAPE and st.winner is None
    es = st.escape
    assert es["turn"] == 1 and es["alert"] == 2 and es["police"] in E.POLICE_ACTIONS and es["hint"] != es["police"]
    safe = E.safe_escape_action(es["hint"])
    assert safe and E.escape_outcome(safe, es["police"]) != "lose"
    # 과반수가 이기면 경계도 -1, 아니면 그대로
    for p in E.escape_players(st):
        E.submit_escape(st, p.id, "hide")
    r1 = E.resolve_escape(st, random.Random(1), police="light")
    assert r1["turn_won"] and r1["wins"] == 3 and st.escape["alert"] == 1 and st.status == E.ESCAPE
    E.submit_escape(st, "1", "hide"); E.submit_escape(st, "2", "move"); E.submit_escape(st, "3", "dead")
    r2 = E.resolve_escape(st, random.Random(1), police="thermal")
    assert not r2["turn_won"] and r2["wins"] == 1 and st.escape["alert"] == 1
    assert [r["outcome"] for r in r2["results"]] == ["lose", "win", "tie"]
    for p in E.escape_players(st):
        E.submit_escape(st, p.id, "dead")
    r3 = E.resolve_escape(st, random.Random(1), police="callout")
    assert r3["winner"] == E.ESCAPED and st.status == E.ENDED and st.winner == E.ESCAPED
    with pytest.raises(E.RuleError):
        E.submit_escape(st, "1", "hide")


def test_escape_runs_out_of_turns_is_caught():
    cfg = flat_cfg(ncpd_max=1.0, escape_turns=2, escape_wins_needed=3, escape_hint=False)
    st = mk_state(n_players=1, host="99", cfg=cfg, enemy_hp=1, gimmicks=("nonlethal", "ncpd"))
    play(st, {"1": ("over", "e1")})
    assert st.status == E.ESCAPE and st.escape["hint"] == ""
    E.submit_escape(st, "1", "hide")
    E.resolve_escape(st, random.Random(1), police="thermal")
    assert st.status == E.ESCAPE
    E.resolve_escape(st, random.Random(1), police="thermal")         # 선택 없음 → none
    assert st.status == E.ENDED and st.winner == E.CAUGHT and st.log[-1]["escape"]["results"][0]["outcome"] == "none"


# ---------------------------------------------------------------------------
# 기믹: 끌고 가기 / 돌 / 단말기
# ---------------------------------------------------------------------------
def test_drag_loots_after_n_rounds_unless_dragger_downed_or_ally_revived():
    cfg = flat_cfg(drag_turns=2)
    st = mk_state(n_players=2, host="99", cfg=cfg, gimmicks=("drag",), chooser=always("atk"))
    st.players["1"].hp = 5
    rep = play(st, {"2": ("guard", None)}, chooser=wait_chooser)     # 적이 P1 을 쓰러뜨림
    assert hp(st, "1") == 0 and st.meters["drag"]["1"]["by"] == "e1"
    assert any(ev["kind"] == "drag_start" for ev in rep["events"])
    rep = play(st, {"2": ("guard", None)})
    assert st.meters["drag"]["1"]["turns"] == 1 and not st.players["1"].looted
    rep = play(st, {"2": ("heal", "1")})                            # 일으키면 끌고 가기 중단
    assert hp(st, "1") == 8 and "1" not in st.meters["drag"]
    st = mk_state(n_players=2, host="99", cfg=cfg, gimmicks=("drag",))
    st.players["1"].hp = 0
    play(st, {"2": ("guard", None)})
    play(st, {"2": ("guard", None)})
    rep = play(st, {"2": ("guard", None)})
    assert st.players["1"].looted and any(ev["kind"] == "looted" for ev in rep["events"])
    assert "1" not in st.meters["drag"]
    assert "끌려갔습니다" in "\n".join(R.event_lines(rep["events"]))


def test_doll_chip_track_stun_immunity_round_cap_and_reboot():
    cfg = flat_cfg(chip_stability=5, chip_round_cap=2, chip_reboot_mult=2, chip_stun_bonus=1,
                   doll_modes="cling:doll_cling:0;rampage:doll_claws:1;reboot:doll_reboot:0")
    st = mk_state(n_players=3, host="1", cfg=cfg, enemy_hp=100, gimmicks=("nonlethal", "doll", "terminal"),
                  enemy_traits={"doll": 1.0, "evade": 0.0}, chooser=None)
    assert st.meters["chip"] == 5 and st.meters["doll_mode"] == "rampage"
    assert st.intents["e1"]["kind"] == "mode" and st.enemy_choices["e1"].target_id == "1"   # 소지자 = 주최자
    rep = play(st, {"1": ("jam", "e1"), "2": ("stun", "e1"), "3": ("mis", "e1")}, chooser=None)
    hacks = [l for l in rep["lines"] if l["kind"] == "hack"]
    assert sum(l["chip"]["amount"] for l in hacks) == 2             # 라운드 상한 2
    assert any(l["chip"].get("capped") for l in hacks)
    assert st.enemies["e1"].mod("stun") is None                     # 돌은 기절하지 않는다
    stun_line = next(l for l in hacks if l["card_id"] == "stun")
    assert stun_line["targets"][0]["kind"] == "stun_immune"
    assert st.meters["chip"] == 3
    # 리부트 모드: 행동 안 함, 상한 2배
    st.config["doll_modes"] = "reboot:doll_reboot:1"
    E.open_round(st, random.Random(1))
    assert st.meters["doll_mode"] == "reboot"
    rep = play(st, {"1": ("jam", "e1"), "2": ("jam", "e1"), "3": ("jam", "e1")}, chooser=None)
    assert any(l["result"] == "reboot" for l in rep["lines"])
    assert st.meters["chip"] == 0 and st.status == E.ENDED and st.winner == E.ALLY and st.end_reason == "chip"


def test_doll_cling_grapples_holder_pass_terminal_and_break_free():
    cfg = flat_cfg(screen_durability=2, doll_modes="cling:doll_cling:1")
    st = mk_state(n_players=2, host="1", cfg=cfg, enemy_hp=100, gimmicks=("nonlethal", "doll", "terminal"),
                  enemy_traits={"doll": 1.0}, chooser=None)
    assert E.PASS_CARD in E.draw_hand(st, "1", random.Random(1)) and E.PASS_CARD not in E.draw_hand(st, "2", random.Random(1))
    rep = play(st, {"1": ("guard", None), "2": ("guard", None)}, chooser=None)
    cling = next(l for l in rep["lines"] if l["actor_id"] == "e1")
    assert cling["targets"][0]["screen"] == 1 and hp(st, "1") == 39            # 긁힘 3 × 0.5 → 1, 액정 -1
    assert E.is_grappled(st.players["1"]) and E.hand_for(st, "1") == [E.BREAK_CARD]
    with pytest.raises(E.RuleError) as ei:
        st.hands["1"] = ["atk"]; E.submit_choice(st, "1", "atk", "e1")
    assert ei.value.code == "GRAPPLED"
    # 아군이 돌을 맞히면 풀린다
    rep = play(st, {"2": ("atk", "e1")}, chooser=None)
    hit = next(l for l in rep["lines"] if l["card_id"] == "atk")
    assert hit.get("released") == ["P1"]                            # P2 가 먼저 때려서 풀렸다가
    assert E.is_grappled(st.players["1"])                           # 같은 라운드에 돌이 또 매달림
    # 떨쳐내기, 단말기 넘기기
    rep = play(st, {"1": (E.BREAK_CARD, None), "2": ("guard", None)}, chooser=None)
    assert any(l["result"] == "break_free" for l in rep["lines"])
    st.players["1"].remove_mod("grapple")
    rep = play(st, {"1": (E.PASS_CARD, "2"), "2": ("guard", None)}, chooser=None)
    assert st.meters["terminal_holder"] == "2" and st.enemy_choices["e1"].target_id == "2"
    assert E.PASS_CARD in E.draw_hand(st, "2", random.Random(1))


def test_evade_and_steady_aim_and_cheap_chrome():
    cfg = flat_cfg(hack_weak_mult=1.5)
    st = mk_state(n_players=1, host="99", cfg=cfg, enemy_traits={"evade": 1.0, "cheap_chrome": 1.0})
    rep = play(st, {"1": ("atk", "e1")})
    assert rep["lines"][0]["result"] == "evade" and hp(st, "e1") == 30
    rep = play(st, {"1": ("jam", "e1")})
    assert rep["lines"][0]["targets"][0]["weak"] and st.enemies["e1"].mod("atk_down").value == 0.75
    st.meters["firewall"] = 9
    ev = E.use_firewall(st, "1", "aim")
    assert st.round_flags["steady_aim"] and ev["cost"] == st.config["firewall_cost_aim"]
    rep = play(st, {"1": ("atk", "e1")})
    assert rep["lines"][0]["result"] == "hit" and hp(st, "e1") == 20     # 회피 무시


def test_regen_trait_heals_at_round_end():
    st = mk_state(n_players=1, host="99", enemy_hp=50, enemy_traits={"regen": 8.0})
    rep = play(st, {"1": ("atk", "e1")})
    assert hp(st, "e1") == 48 and any(ev["kind"] == "regen" and ev["amount"] == 8 for ev in rep["events"])


# ---------------------------------------------------------------------------
# 방화벽
# ---------------------------------------------------------------------------
def test_firewall_gains_from_damage_defend_and_down():
    cfg = flat_cfg(firewall_per_damage=10, firewall_per_defend=1, firewall_per_down=2, firewall_max=9)
    st = mk_state(n_players=2, host="99", cfg=cfg, chooser=always("atk"))
    play(st, {"1": ("guard", None), "2": ("guard", None)}, chooser=always("atk"))
    assert st.meters["firewall"] == 2 and st.meters["firewall_acc"] == 0.5        # 방어 2 + 피해 5
    st.players["1"].hp = 5
    play(st, {"2": ("guard", None)}, chooser=always("atk"))
    assert st.meters["firewall"] == 2 + 1 + 1 + 2                   # 방어 1, 피해 10(누적 1.5 → +1), 쓰러짐 2
    st.meters["firewall"] = 9
    with pytest.raises(E.RuleError) as ei:
        E.use_firewall(st, "1", "cover")                            # 쓰러진 사람은 못 씀
    assert ei.value.code == "DEAD"


def test_firewall_options_once_per_round():
    cfg = flat_cfg(firewall_cost_cover=3, firewall_cost_reboot=2, firewall_cost_maxdoc=4, firewall_maxdoc_ratio=0.5)
    st = mk_state(n_players=3, host="99", cfg=cfg, chooser=always("atk"))
    st.meters["firewall"] = 1
    with pytest.raises(E.RuleError) as ei:
        E.use_firewall(st, "1", "cover")
    assert ei.value.code == "FIREWALL_POOR"
    st.meters["firewall"] = 9
    ev = E.use_firewall(st, "1", "cover")
    assert st.meters["firewall"] == 6 and all(p.mod("shield") for p in st.alive_units(E.ALLY))
    with pytest.raises(E.RuleError) as ei:
        E.use_firewall(st, "2", "scan")
    assert ei.value.code == "FIREWALL_USED"
    rep = play(st, {"1": ("guard", None), "2": ("guard", None), "3": ("guard", None)}, chooser=always("atk"))
    assert any(e["kind"] == "firewall_use" and e["option"] == "cover" for e in rep["events"])
    assert hp(st, "1") == 35                                        # 엄폐 50%
    # 재부팅: 기절·붙잡힘 해제. 맥스닥: 쓰러진 아군 일으킴
    st.players["2"].modifiers.append(E.Modifier("stun", 1, 1, 1))
    st.players["3"].modifiers.append(E.Modifier("grapple", 1, 99, 1, "e1"))
    assert "2" not in {p.id for p in E.acting_players(st)}
    E.use_firewall(st, "1", "reboot")
    assert not st.players["2"].modifiers and not st.players["3"].modifiers
    assert "2" in {p.id for p in E.acting_players(st)}
    st.round_flags["firewall_used"] = False
    st.players["3"].hp = 0
    ev = E.use_firewall(st, "1", "maxdoc")
    assert ev["revived"] and hp(st, "3") == 20 and ev["targets"] == ["P3"]
    assert "맥스닥" in R.event_line(ev)


# ---------------------------------------------------------------------------
# 직렬화 / 시트 매핑 / 렌더
# ---------------------------------------------------------------------------
def test_state_roundtrip():
    st = mk_state(n_players=2, n_enemies=2, host="1", gimmicks=("nonlethal", "ncpd", "drag"), chooser=always("atk"))
    st.hands = {"2": ["atk", "jam", "guard"]}
    E.submit_choice(st, "2", "jam", "e2")
    st.players["1"].modifiers.append(E.Modifier("shield", 0.5, 1, 1, "1"))
    st.log.append({"round_no": 1, "lines": [], "hp": {}, "winner": None})
    d = st.to_dict()
    st2 = E.BattleState.from_dict(d)
    assert st2.to_dict() == d
    assert isinstance(st2.players["1"].modifiers[0], E.Modifier)
    assert isinstance(st2.cards["atk"].effects[0], E.Effect) and isinstance(st2.cards["atk"].combo, E.Combo)
    assert st2.choices["2"].target_id == "e2" and st2.enemy_choices["e1"].card_id == "atk"
    assert st2.gimmicks == ["nonlethal", "ncpd", "drag"] and st2.intents["e1"]["kind"] == "attack"


def test_player_combatant_sheet_mapping():
    cfg = flat_cfg()
    c = E.player_combatant("7", {"max_hp": "39", "current_hp": "12", "physics": "nan", "magic": "", "agility": 15.0,
                                 "job": "스트라이커", "color": "#FF0000"}, "닉", cfg)
    assert (c.name, c.max_hp, c.hp, c.physics, c.magic, c.agility) == ("닉", 39, 39, 10, 10, 15)
    assert c.job == "스트라이커" and c.color == "#FF0000" and R.job_label(c) == "엣지러너"
    c = E.player_combatant("7", {"max_hp": 39, "current_hp": 12}, "닉", flat_cfg(use_current_hp=True))
    assert c.hp == 12
    c = E.player_combatant("8", None, "무명", flat_cfg(default_hp=25))
    assert c.max_hp == 25 and c.side == E.ALLY and c.job == ""


def test_stat_bonus_and_damage_range():
    cfg = C.build_config()
    edge = E.player_combatant("1", {"max_hp": 110, "physics": 16, "magic": 8}, "엣지", cfg)
    net = E.player_combatant("2", {"max_hp": 80, "physics": 8, "magic": 16}, "넷", cfg)
    gd = D.load_game_data(D.CsvSource())
    assert E.damage_range(gd.cards["atk_slash"], edge, cfg) == (16, 20)
    assert E.damage_range(gd.cards["atk_slash"], net, cfg) == (12, 16)
    assert E.damage_range(gd.cards["atk_bolt"], net, cfg) == (16, 20)
    assert E.damage_range(gd.cards["def_guard"], net, cfg) == (0, 0)


def test_log_lines_format_and_particles():
    st = mk_state(n_players=2, host="99", chooser=always("atk"))
    rep = play(st, {"1": ("atk", "e1"), "2": ("guard", None)})
    text = R.report_text(rep)
    assert "🟢 **P2**님이 **엄폐**를 사용했습니다. HP 40/40" in text          # 자기 대상 → 이름 생략
    assert "🟢 **P1**님이 **근접 타격**을 사용했습니다. 적1 HP 20/30 (-10)" in text
    assert "🔴 **적1**이(가) **근접 타격**을 사용했습니다. P1 HP 30/40 (-10)" in text   # 숫자로 끝남 → 묶음 조사
    line = {"actor_id": "x", "actor_name": "영희", "side": "ally", "card_id": "w", "card_name": "ICE",
            "kind": "defend", "result": "shield", "targets": [{"id": "y", "name": "철수", "kind": "shield",
                                                                "amount": 0.7, "hp": 3, "max_hp": 9, "killed": False}]}
    assert R.log_lines(line) == ["**영희**님이 **ICE**을(를) 사용했습니다. 철수 HP 3/9 (이번 라운드 피해 70% 감소)"]
    board = R.hp_board(st)
    assert "🟢 아군" in board and "🔴 적" in board
    assert "선택 현황: 0/2 완료" in R.turn_open_text(st) and "적 의도" in R.turn_open_text(st)
    assert len(R.chunks("a\n" * 3000, 100)) > 1
    assert R.meters_text(st)[0].startswith("🧱 방화벽")
