"""카드 전투 Cog — 준비 단계 버튼, 금고·규칙 명령, 몸의 보느와 시작(기습 보고), 튜토리얼 코치, ⚠ 대상 라벨."""
import asyncio
import random

from cardbattle import engine as E
from cardbattle import gig
from cardbattle import render
from cardbattle import strings as S
from cardbattle.views import PrepView, TargetView, TurnView

from tests.test_cardbattle_cog import (FakeChannel, FakeMessage, flat, interaction, last, make_cog, pick,
                                       setup_battle, text, turn_message)


def prep_message(cog, ch):
    return ch.messages[cog.state.prep_message_id]


def test_prep_buttons_then_battle_and_payout(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch, prep_enabled=True)
        st = cog.state
        st.config["prep_sandbox"] = False                 # b01 은 임시 금고라 보수를 안 주므로, 이 테스트에서는 실제 금고로
        assert st.status == E.PREP and st.prep_message_id and st.prep["share"] == 100
        pm = prep_message(cog, ch)
        assert isinstance(pm.view, PrepView) and "준비 단계" in text(pm) and "🔫" not in text(pm)
        assert any("규칙" in text(m) and "준비 단계" in text(m) for m in ch.sent)      # 소개문에 준비 단계 규칙
        i = interaction(1, ch, message=pm)
        await cog.handle_draw(i)
        assert last(i)[1] == S.ERR_NOT_ALLY_TURN                               # 아직 카드 단계가 아님
        i = interaction(2, ch, message=pm)
        await cog.handle_prep(i, "normal")
        assert last(i)[2] is True and "정식 개조" in last(i)[1] and st.players["2"].weapon_grade >= 2
        assert pm.edits and "정식 개조" in text(pm)
        i = interaction(2, ch, message=FakeMessage(ch, "old"))
        await cog.handle_prep(i, "ready")
        assert last(i)[1] == S.ERR_STALE_MESSAGE
        i = interaction(7, ch, message=pm)
        await cog.handle_prep(i, "ready")
        assert last(i)[1] == S.ERR_NOT_IN_GAME
        n = len(ch.sent)
        i = interaction(1, ch, message=pm)
        await cog.handle_prep(i, "black")
        assert "뒷골목 개조" in last(i)[1]
        if "d10" in last(i)[1]:
            assert any("🎲" in (m.content or "") for m in ch.sent[n:])             # 굴림은 공개
        i = interaction(1, ch, message=pm)
        await cog.handle_prep(i, "ready")
        assert last(i)[1] == S.OK_PREP_READY and cog.state.status == E.PREP
        i = interaction(1, ch, message=pm)
        await cog.handle_prep(i, "ready")
        assert last(i)[1] == S.OK_PREP_ALREADY
        i = interaction(2, ch, message=pm)
        await cog.handle_prep(i, "ready")
        st = cog.state
        assert st.status == E.ROUND_OPEN and st.round == 1
        assert pm.edits[-1].get("view", "keep") is None                          # 준비 버튼 제거
        tm = turn_message(cog, ch)
        assert isinstance(tm.view, TurnView) and "금고" in text(tm) and "🔫mk." in text(tm)
        # 끝까지: 적 HP 1 → 주최자가 제압 사격 → 종료 + 보수가 금고 파일에
        for e in st.enemies.values():
            e.hp = 1
        await pick(cog, ch, 2)
        await pick(cog, ch, 1, "atk_sweep")
        st = cog.state
        assert st.status == E.ENDED and st.winner == E.ALLY
        win = text(ch.sent[-2])
        assert "보수" in win and "무기" in win and isinstance(st.meters.get("payout"), dict)
        assert S.END_TITLE in text(ch.sent[-1])                                 # 종료 배너가 맨 뒤에 따로
        pot = gig.load_pot(cog.gig_path)
        assert pot is not None and pot == st.meters["payout"]["pot_after"] and pot > 0
    asyncio.run(body())


def test_prep_force_by_host_and_resume_in_prep(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch, prep_enabled=True)
        i = interaction(2, ch)
        await cog._cmd_force(i, None)
        assert last(i)[1] == S.ERR_HOST_ONLY and cog.state.status == E.PREP
        # 재시작: 준비 메시지가 없으면 다시 올린다
        cog.state.prep_message_id = 0
        cog._save()
        cog2 = make_cog(ch, tmp_path)
        await cog2.cog_load()
        await asyncio.sleep(0.05)
        assert any(isinstance(v, PrepView) for v in cog2.registered_views)
        assert cog2.state.status == E.PREP and cog2.state.prep_message_id
        i = interaction(1, ch)
        await cog2._cmd_force(i, None)
        assert last(i)[1] == S.OK_FORCED_PREP and cog2.state.status == E.ROUND_OPEN and cog2.state.turn_message_id
        i = interaction(2, ch)
        await cog2._cmd_status(i, None)
        assert "현황" in flat(last(i)[4])
    asyncio.run(body())


def test_vault_and_rules_commands(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        i = interaction(1, ch)
        await cog._cmd_vault(i, None, None)
        assert "금고" in last(i)[1] and "**0**" in last(i)[1]
        i = interaction(2, ch)
        await cog._cmd_vault(i, None, 500)
        assert last(i)[1] == S.ERR_HOST_ONLY                                 # 세션 없음 + 관리자 아님
        await cog._cmd_host(interaction(1, ch), None)
        i = interaction(1, ch)
        await cog._cmd_vault(i, None, -1)
        assert last(i)[1] == S.ERR_VAULT_AMOUNT
        i = interaction(1, ch)
        await cog._cmd_vault(i, None, 500)
        assert "500" in last(i)[1] and gig.load_pot(cog.gig_path) == 500
        i = interaction(1, ch)
        await cog._cmd_rules(i, None, None)
        kind, content, ephemeral, view, embed = last(i)
        assert kind == "send" and ephemeral and isinstance(embed, list) and len(embed) >= 7
        assert any("30초" in (e.title or "") for e in embed)
    asyncio.run(body())


def test_bonewa_start_posts_ambush_before_first_turn(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch, battle="bonewa3")
        st = cog.state
        assert st.status == E.ROUND_OPEN and st.round == 1 and st.has("first_strike") and st.has("ice")
        texts = [text(m) for m in ch.sent]
        intro = next(t for t in texts if "규칙" in t and "몸의 보느와" in t)
        rules = render.intro_rules(st)
        assert len(rules) == len(st.gimmicks) + 1 and all(r and r in intro for r in rules)   # 기믹 6줄 + 방화벽
        idx_amb = next(i for i, t in enumerate(texts) if "기습" in t)
        idx_turn = next(i for i, m in enumerate(ch.sent) if m.id == st.turn_message_id)
        assert idx_amb < idx_turn and not st.pending_reports
        tm = text(turn_message(cog, ch))
        assert "블랙 ICE 4/4" in tm and "생명 유지 8/8" in tm and "모드" in tm
        assert any(p.hp < p.max_hp or p.mod("daemon") for p in st.players.values())
        assert "daemon" in st.meters["tips_seen"]
    asyncio.run(body())


def test_tutorial_battle_posts_coach_and_feedback(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch, battle="tut")
        st = cog.state
        assert st.has("tutorial") and st.script and not st.config["admin_sees_all"]
        texts = [text(m) for m in ch.sent]
        assert any("코치" in t and "첫 라운드" in t for t in texts)
        assert texts.index(next(t for t in texts if "코치" in t)) < [m.id for m in ch.sent].index(st.turn_message_id)
        hand = await pick(cog, ch, 1, "atk_slash")
        assert "atk_slash" in hand and len(hand) == 3
        n = len(ch.sent)
        await pick(cog, ch, 2, "atk_slash")
        st = cog.state
        texts = [text(m) for m in ch.sent[n:]]
        assert any("과제 성공" in t for t in texts) and any("코치 — 2라운드" in t for t in texts)
        assert st.round == 2 and all(e.alive() for e in st.enemies.values())            # 수업이 끝나기 전엔 쓰러지지 않음
    asyncio.run(body())


def test_target_select_marks_overkill_risk(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch, battle="scav3")
        st = cog.state
        for e in st.enemies.values():
            e.hp = 1
        i = interaction(1, ch, message=turn_message(cog, ch))
        await cog.handle_draw(i)
        assert "⚠ 과잉 위험" in flat(last(i)[4])
        i2 = interaction(1, ch)
        await cog.handle_card_pick(i2, "1", "atk_slash")
        view = last(i2)[3]
        assert isinstance(view, TargetView) and all("⚠" in o.label for o in view.children[0].options)
        i3 = interaction(1, ch)
        await cog.handle_card_pick(i3, "1", "atk_knee")
        view = last(i3)[3]
        assert isinstance(view, TargetView) and not any("⚠" in o.label for o in view.children[0].options)
    asyncio.run(body())


def test_host_gets_random_hand_unless_admin_option(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch, admin_sees_all=False)
        st = cog.state
        tm = turn_message(cog, ch)
        await cog.handle_draw(interaction(1, ch, message=tm))
        assert len(E.hand_for(st, "1")) == 3                                     # 주최자도 보통 패
        i = interaction(2, ch)
        await cog._cmd_admin(i, None)
        assert last(i)[1] == S.ERR_HOST_ONLY
        i = interaction(1, ch)
        await cog._cmd_admin(i, None)
        assert last(i)[1] == S.OK_ADMIN_ON and st.all_cards == ["1"] and "1" not in st.hands
        pool = [c for c in st.cards.values() if c.pool in ("both", "ally")]
        assert len(E.hand_for(st, "1")) == len(pool)
        await cog.handle_draw(interaction(1, ch, message=tm))
        assert len(E.hand_for(st, "1")) == len(pool) and "1" not in st.hands     # 전체 목록은 저장하지 않음
        i = interaction(1, ch)
        await cog._cmd_admin(i, None)
        assert last(i)[1] == S.OK_ADMIN_OFF and st.all_cards == []
        await cog.handle_draw(interaction(1, ch, message=tm))
        assert len(E.hand_for(st, "1")) == 3
        # 로비에서 켜 두면 시작한 전투로 이어진다
        ch2 = FakeChannel()
        cog2 = make_cog(ch2, tmp_path)
        await cog2._cmd_host(interaction(1, ch2), None)
        cog2.state.config.update({"report_delay_sec": 0.0, "prep_enabled": False, "admin_sees_all": False})
        await cog2._cmd_join(interaction(1, ch2), None)
        await cog2._cmd_join(interaction(2, ch2), None)
        await cog2._cmd_admin(interaction(1, ch2), None)
        await cog2._cmd_start(interaction(1, ch2), "b01")
        assert cog2.state.all_cards == ["1"] and len(E.hand_for(cog2.state, "1")) > 3
    asyncio.run(body())
