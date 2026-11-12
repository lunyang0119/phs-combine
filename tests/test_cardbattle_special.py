"""전투별 특수 규칙: 리미트 · 플래시백 · 팀 연계 · 국면 전환 · 말 걸기 · 환경, 그리고 1라운드 브리핑."""
import asyncio
import random

from cardbattle import data as D
from cardbattle import embeds
from cardbattle import engine as E
from cardbattle import render as R
from cardbattle import strings as S

from tests.test_cardbattle_cog import FakeChannel, make_cog, setup_battle, text
from tests.test_cardbattle_engine import always, enemy_plays, flat_cfg, hp, mk_cards, play, wait_chooser
from tests.test_cardbattle_gig import SeqRng


def cards():
    cs = mk_cards()
    extra = [
        E.Card("limit_edge", "과부하", "special", "enemy", "physics", "fixed", 0,
               [E.Effect("damage", 18), E.Effect("self_damage", 8)], tags=["limit", "pierce"]),
        E.Card("limit_net", "균열", "hack", "all_enemy", "magic", "fixed", 0, [E.Effect("stun", 1)], tags=["limit"]),
        E.Card("limit_soldier", "팔도일섬", "special", "enemy", "physics", "fixed", 0,
               [E.Effect("flurry", 4, count=8)], tags=["limit"]),
        E.Card("limit_any", "이 악물기", "special", "self", "none", "fixed", 0,
               [E.Effect("heal", 20), E.Effect("shield", 0.5, 1)], tags=["limit"]),
        E.Card(E.TALK_CARD, "말 걸기", "special", "enemy", "charm", "fixed", 0, [E.Effect("talk", 1)], tags=["nonlethal"]),
        E.Card("rage", "약 빨기", "special", "self", "none", "fixed", 0, [E.Effect("heal", 5), E.Effect("rage", 0.5)]),
    ]
    for c in extra:
        cs[c.id] = c
    return cs


def mk(n_players=2, jobs=("스트라이커", "마테리아 위버", "솔져"), charm=10, gimmicks=(), cfg=None, enemy_hp=60,
       enemy_id="e1", rng=None, chooser=wait_chooser, enemy_traits=None):
    cfg = cfg or flat_cfg()
    cfg.setdefault("limit_cards", "스트라이커:limit_edge;마테리아 위버:limit_net;솔져:limit_soldier;*:limit_any")
    players = [E.player_combatant(str(i + 1), {"max_hp": 40, "agility": 10 + i, "charm": charm,
                                                "job": jobs[i % len(jobs)]}, f"P{i + 1}", cfg) for i in range(n_players)]
    enemies = [E.Combatant(enemy_id, "적1", E.ENEMY, enemy_hp, enemy_hp, agility=5, traits=dict(enemy_traits or {}))]
    st = E.new_battle(players, enemies, cards(), cfg, host_id="99", battle_id="t", battle_name="테스트",
                      gimmicks=gimmicks, rng=rng or random.Random(1))
    if chooser is not None:
        E.open_round(st, SeqRng(), chooser)       # SeqRng: 주사위가 전부 1 → 플래시백이 터지지 않는다
    return st


# ---------------------------------------------------------------------------
# 리미트
# ---------------------------------------------------------------------------
def test_limit_card_is_per_job_once_per_battle_and_pierces():
    st = mk(n_players=3, gimmicks=("limit", "ice"), cfg=flat_cfg(ice_layers=2, ice_reduction=0.3))
    assert E.fixed_cards(st, st.players["1"]) == ["limit_edge"]
    assert E.fixed_cards(st, st.players["2"]) == ["limit_net"] and E.fixed_cards(st, st.players["3"]) == ["limit_soldier"]
    assert "limit_edge" in E.hand_for(st, "1")
    st.enemies["e1"].modifiers.append(E.Modifier("shield", 0.5, 1, 1))
    E.submit_choice(st, "1", "limit_edge", "e1")
    rep = E.resolve_round(st, random.Random(1), wait_chooser)
    line = next(l for l in rep["lines"] if l["actor_id"] == "1")
    dmg = next(t for t in line["targets"] if t["kind"] == "damage")
    assert line["limit"] and dmg["amount"] == 18 and "shielded" not in dmg and "iced" not in dmg    # 엄폐·ICE 무시
    assert hp(st, "1") == 40 - 8 and R.log_lines(line)[0].startswith(S.LOG_LIMIT_PREFIX)
    assert st.meters["limit_used"] == ["1"] and "limit_edge" not in E.hand_for(st, "1")             # 한 번 쓰면 끝
    assert "limit_net" in E.hand_for(st, "2")


def test_limit_fallback_job_and_off_without_gimmick():
    st = mk(n_players=1, jobs=("바텐더",), gimmicks=("limit",))
    assert E.fixed_cards(st, st.players["1"]) == ["limit_any"]
    st = mk(n_players=1, gimmicks=())
    assert E.fixed_cards(st, st.players["1"]) == []


def test_flurry_rolls_hits_and_range_covers_it():
    st = mk(n_players=3, gimmicks=("limit",))
    card = st.cards["limit_soldier"]
    assert E.damage_range(card, st.players["3"], st.config) == (8, 32)        # 2타 ~ 8타
    E.submit_choice(st, "3", "limit_soldier", "e1")
    rep = E.resolve_round(st, SeqRng(ints=[5]), wait_chooser)
    line = next(l for l in rep["lines"] if l["actor_id"] == "3")
    assert line["flurry"] == 5 and line["targets"][0]["amount"] == 20 and S.LOG_FLURRY.format(hits=5) in R.log_lines(line)[0]


# ---------------------------------------------------------------------------
# 플래시백
# ---------------------------------------------------------------------------
def test_flash_needs_threshold_and_charm_helps():
    cfg = flat_cfg(flash_threshold=29, flash_effects="boost")
    st = mk(n_players=2, charm=10, gimmicks=("flash",), cfg=cfg)
    assert "flash_now" not in st.meters                                        # 주사위 1,1,1,1
    E.open_round(st, SeqRng(ints=[7, 7, 7, 7, 1, 1, 1, 1]), wait_chooser)       # 28 < 29
    assert "flash_now" not in st.meters
    st.players["1"].charm = 12                                                 # 보정 +1 → 29
    E.open_round(st, SeqRng(ints=[7, 7, 7, 7, 1, 1, 1, 1]), wait_chooser)
    rec = st.meters["flash_now"][0]
    assert rec["pid"] == "1" and rec["roll"] == 29 and rec["effect"] == "boost" and rec["value"] == 6
    assert st.players["1"].mod("boost").value == 6
    assert E.damage_range(st.cards["atk"], st.players["1"], st.config) == (16, 16)   # 패 표시에 반영
    rep = play(st, {"1": ("atk", "e1")})
    line = next(l for l in rep["lines"] if l["actor_id"] == "1")
    assert line["targets"][0]["amount"] == 16 and st.players["1"].mod("boost") is None   # 한 번 쓰고 사라진다
    assert "flash_now" not in st.meters                                         # 다음 라운드(주사위 낮음)에는 지워진다


def test_flash_one_per_round_highest_roll_and_extra_card():
    cfg = flat_cfg(flash_threshold=20, flash_effects="extra_card", flash_per_round=1)
    st = mk(n_players=2, gimmicks=("flash",), cfg=cfg)
    st.config["admin_sees_all"] = False
    E.open_round(st, SeqRng(ints=[5, 5, 5, 5, 9, 9, 9, 9]), wait_chooser)
    now = st.meters["flash_now"]
    assert len(now) == 1 and now[0]["pid"] == "2" and now[0]["roll"] == 36
    assert len(E.draw_hand(st, "2", random.Random(1))) == 4 and len(E.draw_hand(st, "1", random.Random(1))) == 3
    entry = R.flash_entries(st)[0]
    fig = S.FLASH_FIGURES["extra_card"]
    assert entry["player"] == "P2" and fig["name"] in entry["title"] and entry["lines"] in fig["memories"]
    emb = embeds.flash(st)[0]
    assert "9 + 9 + 9 + 9" in emb.fields[0].value and "P2" in emb.fields[1].value


def test_flash_recharge_rearms_the_limit():
    cfg = flat_cfg(flash_threshold=20, flash_effects="recharge;heal")
    st = mk(n_players=1, gimmicks=("flash", "limit"), cfg=cfg)
    assert E._flash_options(st, st.players["1"]) == []                           # 리미트를 안 썼고 HP 도 가득 → 줄 게 없다
    E.submit_choice(st, "1", "limit_edge", "e1")
    E.resolve_round(st, SeqRng(ints=[9, 9, 9, 9]), wait_chooser)                  # 다음 라운드를 열면서 터진다
    rec = st.meters["flash_now"][0]
    assert rec["effect"] in ("recharge", "heal") and E._flash_options(st, st.players["1"]) != []
    st.meters["limit_used"] = ["1"]
    st.players["1"].hp = 40
    E.open_round(st, SeqRng(ints=[9, 9, 9, 9]), wait_chooser)
    assert st.meters["flash_now"][0]["effect"] == "recharge" and st.meters["limit_used"] == []
    assert "limit_edge" in E.hand_for(st, "1")


# ---------------------------------------------------------------------------
# 팀 연계
# ---------------------------------------------------------------------------
def test_chain_bonus_grows_fills_firewall_and_never_causes_overkill():
    cfg = flat_cfg(chain_bonus=0.5, chain_max=4, chain_firewall_at=3, overkill_margin=5)
    st = mk(n_players=3, gimmicks=("chain", "nonlethal"), cfg=cfg, enemy_hp=40)
    rep = play(st, {"1": ("atk", "e1"), "2": ("atk", "e1"), "3": ("atk", "e1")})
    amounts = [l["targets"][0]["amount"] for l in rep["lines"] if l["side"] == E.ALLY]
    assert amounts == [10, 15, 20]                                             # +0% / +50% / +100%
    last = [l for l in rep["lines"] if l["side"] == E.ALLY][-1]["targets"][0]
    assert last["chain"] == 3 and last["killed"] and not last.get("overkill")   # 15 남았는데 20: 기본 피해 10 으로 판정
    assert any(ev["kind"] == "chain" and ev["count"] == 3 for ev in rep["events"]) and st.meters["firewall"] == 1
    assert S.LOG_CHAIN.format(n=3) in R.log_lines([l for l in rep["lines"] if l["side"] == E.ALLY][-1])[0]


def test_chain_resets_each_round_and_is_off_without_gimmick():
    st = mk(n_players=2, gimmicks=("chain",), cfg=flat_cfg(chain_bonus=0.5))
    play(st, {"1": ("atk", "e1")})
    rep = play(st, {"1": ("atk", "e1")})
    assert rep["lines"][0]["targets"][0]["amount"] == 10
    st = mk(n_players=2, gimmicks=())
    rep = play(st, {"1": ("atk", "e1"), "2": ("atk", "e1")})
    assert [l["targets"][0]["amount"] for l in rep["lines"] if l["side"] == E.ALLY] == [10, 10]


# ---------------------------------------------------------------------------
# 국면 전환
# ---------------------------------------------------------------------------
def test_phase_is_warned_then_played_once_and_rage_boosts_damage():
    cfg = flat_cfg(phases="boss:50:rage")
    st = mk(n_players=1, gimmicks=("phases",), cfg=cfg, enemy_hp=30, enemy_id="boss#1", chooser=always("atk"))
    rep = E.resolve_round(st, random.Random(1), always("atk"))
    assert hp(st, "1") == 30 and not any(e["kind"] == "phase_warn" for e in rep["events"])
    st.hands["1"] = ["atk"]
    E.submit_choice(st, "1", "atk", "boss#1")
    st.enemies["boss#1"].hp = 22
    rep = E.resolve_round(st, random.Random(1), always("atk"))                    # 22 → 12 (40%) → 예고
    warn = next(e for e in rep["events"] if e["kind"] == "phase_warn")
    assert warn["card"] == "약 빨기" and "약 빨기" in R.event_line(warn)
    it = E.public_intents(st)[0]
    assert it["kind"] == "phase" and "국면 전환" in R.intent_text(it) and "약 빨기" in R.intent_text(it)
    rep = E.resolve_round(st, random.Random(1), always("atk"))                    # 예고된 카드를 낸다
    assert st.enemies["boss#1"].hp == 17 and st.enemies["boss#1"].mod("rage").value == 0.5
    assert any(e["kind"] == "rage" for e in rep["events"]) and E.public_intents(st)[0]["kind"] == "attack"
    before = hp(st, "1")
    rep = E.resolve_round(st, random.Random(1), always("atk"))
    assert before - hp(st, "1") == 15 and not any(e["kind"] == "phase_warn" for e in rep["events"])   # 10 × 1.5, 한 번뿐


# ---------------------------------------------------------------------------
# 말 걸기
# ---------------------------------------------------------------------------
def test_talk_roll_uses_charm_resolves_first_and_dc_rises():
    cfg = flat_cfg(stat_divisor=2, talk_dc=16, talk_dc_step=2, talk_crit_margin=8)
    st = mk(n_players=1, charm=12, gimmicks=("talk",), cfg=cfg, chooser=always("atk"))
    assert E.TALK_CARD in E.hand_for(st, "1")
    E.submit_choice(st, "1", E.TALK_CARD, "e1")
    rep = E.resolve_round(st, SeqRng(ints=[5, 5]), always("atk"))                   # 5+5+6 = 16 ≥ 16
    assert rep["lines"][0]["actor_id"] == "1"                                      # 말이 공격보다 먼저
    t = rep["lines"][0]["targets"][0]
    assert t["success"] and not t["crit"] and t["total"] == 16 and t["dc"] == 16
    assert 40 - hp(st, "1") == 7                                                   # 적 공격 10 + 근력 5 = 15 → 반감
    out = R.log_lines(rep["lines"][0])
    assert "5+5+6" in out[0] and "성공" in out[0] and len(out) == 2 and out[1].startswith("　💬")
    E.submit_choice(st, "1", E.TALK_CARD, "e1")
    rep = E.resolve_round(st, SeqRng(ints=[5, 5]), always("atk"))                   # 목표 18 → 실패
    t = rep["lines"][0]["targets"][0]
    assert t["dc"] == 18 and not t["success"] and "실패" in R.log_lines(rep["lines"][0])[0]


def test_talk_crit_stuns_and_touches_battle_meters():
    cfg = flat_cfg(talk_dc=4, talk_crit_margin=8, ncpd_max=3.0)
    st = mk(n_players=1, gimmicks=("talk", "ncpd", "life_support"), cfg=cfg, chooser=always("atk"))
    st.meters["ncpd"] = 1.0
    st.meters["life"] = 5
    E.submit_choice(st, "1", E.TALK_CARD, "e1")
    rep = E.resolve_round(st, SeqRng(ints=[10, 10]), always("atk"))
    t = rep["lines"][0]["targets"][0]
    assert t["crit"] and hp(st, "1") == 40                                        # 기절해서 공격이 안 나왔다
    assert next(l for l in rep["lines"] if l["actor_id"] == "e1")["result"] == "stunned"
    assert st.meters["ncpd"] == 0.5 and st.meters["life"] == 5                     # +1 뒤 라운드 끝 −1
    assert R.talk_quote("scav_boss", "ok", 0) in S.TALK_LINES["scav_boss"]["ok"]
    assert R.talk_quote("nobody", "fail", 3) in S.TALK_LINES["default"]["fail"]


# ---------------------------------------------------------------------------
# 환경
# ---------------------------------------------------------------------------
def test_env_blackout_hides_intents_until_scan():
    st = mk(n_players=1, gimmicks=("env",), cfg=flat_cfg(env_pool="blackout"), chooser=always("atk"))
    assert st.meters["env"] == "blackout"
    it = E.public_intents(st)[0]
    assert it["kind"] == "hidden" and "정전" in R.intent_text(it)
    st.round_flags["scan"] = True
    assert E.public_intents(st)[0]["kind"] == "attack"
    assert S.METER_ENV.format(env="정전") in R.meters_text(st)


def test_env_emp_acid_and_glitch():
    st = mk(n_players=1, gimmicks=("env",), cfg=flat_cfg(env_pool="emp"))
    assert st.meters["firewall"] == 2                                             # new_battle + 테스트의 open_round
    st = mk(n_players=1, gimmicks=("env",), cfg=flat_cfg(env_pool="acid", env_acid_ratio=0.1), enemy_hp=5)
    rep = E.resolve_round(st, random.Random(1), wait_chooser)
    assert hp(st, "1") == 36 and hp(st, "e1") == 4 and any(e["kind"] == "env_acid" for e in rep["events"])
    for _ in range(6):
        E.resolve_round(st, random.Random(1), wait_chooser)
    assert hp(st, "e1") == 1 and st.status == E.ROUND_OPEN                         # 산성비로는 쓰러지지 않는다
    st = mk(n_players=2, gimmicks=("env",), cfg=flat_cfg(env_pool="glitch"))
    firsts = set()
    for seed in range(20):
        st.choices, st.hands = {}, {}
        rep = play(st, {"1": ("guard", None), "2": ("guard", None)}, rng=random.Random(seed))
        firsts.add(rep["lines"][0]["actor_id"])
    assert firsts == {"1", "2"}                                                    # 민첩(P2 가 빠름)이 순서를 정하지 않는다


def test_env_is_absent_without_gimmick_or_with_zero_chance():
    assert "env" not in mk(n_players=1, gimmicks=()).meters
    assert "env" not in mk(n_players=1, gimmicks=("env",), cfg=flat_cfg(env_chance=0.0)).meters


# ---------------------------------------------------------------------------
# 브리핑 · 데이터
# ---------------------------------------------------------------------------
def test_briefing_sections_only_for_special_gimmicks_and_fit_embed_fields():
    gd = D.load_game_data(D.CsvSource(), include_commented=True)
    from cardbattle import config as C
    for bid, b in gd.battles.items():
        cfg = C.build_config(b.overrides)
        cfg["prep_enabled"] = False
        players = [E.player_combatant(str(i), {"max_hp": 100, "job": "솔져"}, f"P{i}", cfg) for i in range(1, 4)]
        script = gd.tutorials.get(str(cfg.get("tutorial_script", ""))) if "tutorial" in b.gimmicks else None
        st = E.new_battle(players, E.enemy_combatants(b, gd.enemies), gd.cards, cfg, host_id="1", gimmicks=b.gimmicks,
                          rng=random.Random(2), script=script)
        sections = R.briefing_sections(st)
        special = [g for g in b.gimmicks if g in S.BRIEFING]
        assert len(sections) == len(special), bid
        assert all("{" not in body and len(body) <= 1024 for _, body in sections), bid
        emb = embeds.briefing(st)
        assert (emb is None) == (not sections)
        if bid in ("tut", "b01"):
            assert emb is None                                                    # 튜토리얼·기본 연습은 그대로
        if "phases" in b.gimmicks:
            assert any("HP 50% 이하" in body for _, body in sections), bid
        for p in st.players.values():
            assert E.limit_card_for(st, p) == ("limit_soldier" if "limit" in b.gimmicks else None)


def test_every_flash_effect_and_env_has_text():
    assert set(S.FLASH_FIGURES) == set(E.FLASH_EFFECTS)
    assert all(f["memories"] and all(m for m in f["memories"]) for f in S.FLASH_FIGURES.values())
    assert set(S.ENV_KO) == set(E.ENVS) == set(S.ENV_BRIEF)


def test_cog_posts_briefing_once_before_first_turn_and_flash_above_turn(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch, battle="scav3", players=(1, 2), flash_threshold=4)
        st = cog.state
        texts = [text(m) for m in ch.sent]
        idx_brief = next(i for i, t in enumerate(texts) if S.BRIEFING_TITLE in t)
        idx_turn = next(i for i, m in enumerate(ch.sent) if m.id == st.turn_message_id)
        assert idx_brief < idx_turn and sum(S.BRIEFING_TITLE in t for t in texts) == 1
        assert "리미트" in texts[idx_brief] and "말 걸기" in texts[idx_brief] and "국면 전환" in texts[idx_brief]
        intro = next(t for t in texts if S.INTRO_SPECIAL_FIELD in t)
        assert "브리핑" in intro
        assert st.meters.get("flash_now") and "플래시백" in texts[idx_turn - 1]      # 문턱 4 → 반드시 터진다
        n = len(ch.sent)
        await cog._resolve_round(force=True)
        assert sum(S.BRIEFING_TITLE in text(m) for m in ch.sent[n:]) == 0           # 브리핑은 한 번만

        ch2 = FakeChannel()
        cog2 = make_cog(ch2, tmp_path)
        await setup_battle(cog2, ch2, battle="b01")
        assert not any(S.BRIEFING_TITLE in text(m) for m in ch2.sent)
    asyncio.run(body())
