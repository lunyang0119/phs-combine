"""적 AI — 손에 든 카드마다 점수를 매겨 최고점을 고른다 (LLM 없음).

점수 = 상황 점수(score_card) × 성향 가중치(PERSONALITIES[kind]) + 난수(ai_noise).
입력은 전부 BattleState 에서 읽을 수 있는 공개 정보다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import engine as E

PERSONALITIES: Dict[str, Dict[str, float]] = {
    "aggressive": {"attack": 1.4, "defend": 0.6, "hack": 0.8, "special": 1.3},
    "defensive":  {"attack": 0.8, "defend": 1.5, "hack": 1.0, "special": 0.7},
    "hacker":     {"attack": 0.8, "defend": 0.8, "hack": 1.6, "special": 0.9},
}


@dataclass
class AiContext:
    me: E.Combatant
    own_hp_ratio: float
    allies: List[E.Combatant]            # 생존한 같은 편 (자신 포함)
    allies_dying: int
    lowest_ally: Optional[E.Combatant]
    players: List[E.Combatant]           # 생존한 상대편
    last_player_kinds: List[str]
    player_debuffs: Dict[str, List[str]] = field(default_factory=dict)
    own_shielded: bool = False


def build_context(st: E.BattleState, me: E.Combatant) -> AiContext:
    allies = st.alive_units(me.side)
    players = st.alive_units(E.opposite(me.side))
    hurt = [a for a in allies if a.hp < a.max_hp]
    lowest = min(hurt, key=lambda a: a.hp_ratio()) if hurt else None
    return AiContext(
        me=me, own_hp_ratio=me.hp_ratio(), allies=allies,
        allies_dying=sum(1 for a in allies if a.hp_ratio() < 0.3),
        lowest_ally=lowest, players=players,
        last_player_kinds=list(st.last_player_kinds),
        player_debuffs={p.id: [m.kind for m in p.modifiers] for p in players},
        own_shielded=me.mod("shield") is not None,
    )


def _best_attack_target(card: E.Card, ctx: AiContext, cfg) -> Optional[E.Combatant]:
    if not ctx.players:
        return None
    # 처치 가능한 대상 우선, 그 다음 HP 비율이 낮은 대상
    def key(p: E.Combatant):
        dmg = E.expected_damage(card, ctx.me, p, cfg)
        return (0 if dmg >= p.hp else 1, p.hp_ratio(), p.hp)
    return min(ctx.players, key=key)


def _best_hack_target(card: E.Card, ctx: AiContext) -> Optional[E.Combatant]:
    if not ctx.players:
        return None
    kinds = [e.kind for e in card.effects]
    fresh = [p for p in ctx.players if not any(k in ctx.player_debuffs.get(p.id, []) for k in kinds)]
    pool = fresh or ctx.players
    if "stun" in kinds or "atk_down" in kinds:
        return max(pool, key=lambda p: (p.physics + p.magic, p.hp))
    return max(pool, key=lambda p: p.hp)


def score_card(card: E.Card, ctx: AiContext, cfg) -> float:
    attacks_last = ctx.last_player_kinds.count("attack") + ctx.last_player_kinds.count("special")
    if card.kind in ("attack", "special"):
        if card.has("heal") and not card.has("damage"):
            if ctx.lowest_ally is None:
                return 0.0
            return (1.0 - ctx.lowest_ally.hp_ratio()) * 1.5
        if not ctx.players:
            return 0.0
        if card.target in ("all_enemy",):
            s = sum(E.expected_damage(card, ctx.me, p, cfg) / p.max_hp for p in ctx.players)
        else:
            t = _best_attack_target(card, ctx, cfg)
            dmg = E.expected_damage(card, ctx.me, t, cfg)
            s = dmg / max(1, t.max_hp) + (1.0 if dmg >= t.hp else 0.0)
        mis = ctx.me.mod("misfire")
        if mis:
            s *= max(0.0, 1.0 - mis.value)
        if card.kind == "special":
            s *= 1.2
            if card.has("self_damage") and ctx.own_hp_ratio < 0.35:
                s -= 0.5
        return s
    if card.kind == "defend":
        if card.target == "self" and ctx.own_shielded:
            return 0.0
        if card.target == "ally":
            tgt = ctx.lowest_ally or ctx.me
            s = (1.0 - tgt.hp_ratio()) * 1.2
        else:
            s = (1.0 - ctx.own_hp_ratio) * 1.2
        if attacks_last >= 2:
            s += 0.5
        return s
    if card.kind == "hack":
        if not ctx.players:
            return 0.0
        kinds = [e.kind for e in card.effects]
        s = 0.5
        if any(not any(k in ctx.player_debuffs.get(p.id, []) for k in kinds) for p in ctx.players):
            s += 0.5
        if "atk_down" in kinds and attacks_last >= 1:
            s += 0.3
        if "stun" in kinds:
            s += 0.4
        if "redirect_random" in kinds and len(ctx.players) >= 3:
            s += 0.2
        return s
    return 0.0


def pick_target(card: E.Card, ctx: AiContext, cfg, rng: E.Rng) -> Optional[str]:
    if card.target == "enemy":
        if card.kind == "hack":
            t = _best_hack_target(card, ctx)
        else:
            t = _best_attack_target(card, ctx, cfg)
        return t.id if t else None
    if card.target == "ally":
        t = ctx.lowest_ally or ctx.me
        return t.id
    if card.target == "self":
        return ctx.me.id
    return None


def choose(st: E.BattleState, me: E.Combatant, hand: List[str], rng: E.Rng) -> Optional[E.Choice]:
    if not hand:
        return None
    cfg = st.config
    ctx = build_context(st, me)
    weights = PERSONALITIES.get(me.personality, PERSONALITIES["aggressive"])
    noise = float(cfg.get("ai_noise", 0.15))
    best: Optional[E.Card] = None
    best_score = float("-inf")
    for cid in hand:
        card = st.cards.get(cid)
        if card is None:
            continue
        if E.needs_target(card) and not E.valid_targets(st, me, card):
            continue
        s = score_card(card, ctx, cfg) * weights.get(card.kind, 1.0) + rng.random() * noise
        if s > best_score:
            best, best_score = card, s
    if best is None:
        return None
    return E.Choice(actor_id=me.id, card_id=best.id, target_id=pick_target(best, ctx, cfg, rng))
