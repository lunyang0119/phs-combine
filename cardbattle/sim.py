"""몬테카를로 시뮬레이션 — 승률·결과 분포·라운드 수 확인용.

    python -m cardbattle.sim --battle scav4 --players 4 --games 500 --seed 1
    python -m cardbattle.sim --all --games 200          # Battles.csv 의 players 열 인원으로 전부
    python -m cardbattle.sim --battle doll3 --set chip_stability=3 --policy random

플레이어 봇은 실제 직업 템플릿(엣지러너 HP110 근력16 / 넷러너 HP80 RAM16 / 솔져 HP110 16·14)을 돌려 쓴다.
정책:
  greedy       공개된 적 의도를 읽는다. 공격 준비 중인 적이 많으면 엄폐/ICE, 강한 적에게 해킹, 돌에게는 해킹 우선,
               비살상이면 과잉 제압을 피해 약한 카드로 마무리, 방화벽은 상황에 따라 사용
  attack_only  무조건 가장 센 공격. 이 봇이 greedy 와 승률이 같거나 높으면 공격 외 카드가 함정이라는 뜻 (기준선)
  random       무작위
사람은 봇보다 잘 하므로 결과는 하한선으로 본다.
"""
from __future__ import annotations

import argparse
import random
import statistics
from collections import Counter
from typing import Callable, Dict, List, Optional

from . import ai
from . import config as cfgmod
from . import data as D
from . import engine as E

JOBS = [
    {"job": "스트라이커", "max_hp": 110, "physics": 16, "magic": 8, "agility": 10, "charm": 8},
    {"job": "마테리아 위버", "max_hp": 80, "physics": 8, "magic": 16, "agility": 8, "charm": 10},
    {"job": "솔져", "max_hp": 110, "physics": 16, "magic": 14, "agility": 12, "charm": 8},
]

Policy = Callable[[E.BattleState, E.Combatant, List[str], random.Random], Optional[E.Choice]]


def _attack(st: E.BattleState, me: E.Combatant, cards: List[E.Card], enemies: List[E.Combatant],
            avoid_overkill: bool) -> Optional[E.Choice]:
    best = _attack_scored(st, me, cards, enemies, avoid_overkill)
    return best[1] if best else None


def _attack_scored(st: E.BattleState, me: E.Combatant, cards: List[E.Card], enemies: List[E.Combatant],
                   avoid_overkill: bool):
    attacks = [c for c in cards if c.has("damage") and not c.has("grapple")]
    if not attacks or not enemies:
        return None
    best: Optional[tuple] = None
    for c in attacks:
        for t in (enemies if c.target == "enemy" else [None]):
            exp = E.expected_damage(c, me, t, st.config) if t else sum(E.expected_damage(c, me, e, st.config) for e in enemies)
            score = exp
            if t is not None:
                lo, hi = E.damage_range(c, me, st.config)
                margin = int(st.config.get("overkill_margin", 10))
                risky = c.tagged("lethal") or (hi - t.hp >= margin and not c.tagged("nonlethal"))
                if avoid_overkill and risky and hi >= t.hp:
                    score -= 40 if lo - t.hp >= margin else 15     # 과잉 제압 위험
                if exp >= t.hp:
                    score += 6              # 처치 보너스
                score -= t.hp / 50.0        # 약한 적 우선
            if best is None or score > best[0]:
                best = (score, c, t)
    if best is None:
        return None
    score, c, t = best
    return score, E.Choice(me.id, c.id, t.id if t is not None else None)


def greedy_choice(st: E.BattleState, me: E.Combatant, hand: List[str], rng: random.Random) -> Optional[E.Choice]:
    cards = [st.cards[c] for c in hand if c in st.cards]
    enemies = st.alive_units(E.ENEMY)
    if not cards or not enemies:
        return None
    if E.is_grappled(me):
        b = [c for c in cards if c.has(E.BREAK_CARD)]
        return E.Choice(me.id, b[0].id, me.id) if b else None
    intents = {it["id"]: it for it in E.public_intents(st)}
    attackers = [e for e in enemies if intents.get(e.id, {}).get("kind") in ("attack", "special", "mode")]
    nonlethal = st.has("nonlethal")
    # 돌: 해킹이 답
    if st.has("doll"):
        hacks = [c for c in cards if c.kind == "hack"]
        doll = next((e for e in enemies if e.has_trait("doll")), None)
        if hacks and doll is not None:
            pref = [c for c in hacks if c.has("stun")] or hacks
            return E.Choice(me.id, pref[0].id, doll.id)
    # 위험하면 방어 (공격 준비 중인 적이 2 이상이고 내 HP 가 낮을 때)
    if me.hp_ratio() < 0.4 and len(attackers) >= 1 and me.mod("shield") is None:
        d = [c for c in cards if c.kind == "defend"]
        if d:
            c = d[0]
            return E.Choice(me.id, c.id, me.id)
    hurt = [p for p in st.players.values() if (not p.alive() and not p.looted) or (p.alive() and p.hp_ratio() < 0.35)]
    heals = [c for c in cards if c.has("heal") and not c.has("damage")]
    if hurt and heals:
        t = min(hurt, key=lambda p: p.hp_ratio())
        return E.Choice(me.id, heals[0].id, t.id)
    # 공격 준비 중인 강한 적에게 기절/글리치
    hacks = [c for c in cards if c.kind == "hack"]
    if hacks and attackers:
        kinds = {e.kind for c in hacks for e in c.effects}
        fresh = [e for e in attackers if not any(m.kind in kinds for m in e.modifiers)]
        if fresh:
            t = max(fresh, key=lambda e: e.physics + e.magic + e.hp / 10)
            pref = [c for c in hacks if c.has("stun")] or hacks
            if me.agility >= t.agility or not any(c.has("damage") for c in cards):
                return E.Choice(me.id, pref[0].id, t.id if pref[0].target == "enemy" else None)
    best = _attack_scored(st, me, cards, enemies, nonlethal)
    if best and best[0] < 0:
        # 모든 공격이 과잉 제압 위험 → 해킹/방어로 돌린다
        alt = [c for c in cards if c.kind in ("hack", "defend")]
        if alt:
            c = alt[0]
            tgt = max(enemies, key=lambda e: e.hp).id if c.target == "enemy" else (me.id if c.target == "self" else None)
            return E.Choice(me.id, c.id, tgt)
    if best:
        return best[1]
    return ai.choose(st, me, hand, rng)


def attack_only(st: E.BattleState, me: E.Combatant, hand: List[str], rng: random.Random) -> Optional[E.Choice]:
    cards = [st.cards[c] for c in hand if c in st.cards]
    enemies = st.alive_units(E.ENEMY)
    if E.is_grappled(me):
        b = [c for c in cards if c.has(E.BREAK_CARD)]
        return E.Choice(me.id, b[0].id, me.id) if b else None
    ch = _attack(st, me, cards, enemies, avoid_overkill=False)
    return ch or random_choice(st, me, hand, rng)


def random_choice(st: E.BattleState, me: E.Combatant, hand: List[str], rng: random.Random) -> Optional[E.Choice]:
    cards = [st.cards[c] for c in hand if c in st.cards]
    rng.shuffle(cards)
    for c in cards:
        t = E.valid_targets(st, me, c)
        if E.needs_target(c) and not t:
            continue
        return E.Choice(me.id, c.id, rng.choice(t).id if t else None)
    return None


def greedy_firewall(st: E.BattleState, rng: random.Random) -> Optional[str]:
    fw = int(st.meters.get("firewall", 0))
    down = [p for p in st.players.values() if not p.alive() and not p.looted]
    if down and fw >= E.firewall_cost(st, "maxdoc"):
        return "maxdoc"
    stuck = [p for p in st.alive_units(E.ALLY) if E.is_stunned(p) or E.is_grappled(p)]
    if stuck and fw >= E.firewall_cost(st, "reboot"):
        return "reboot"
    attackers = [it for it in E.public_intents(st) if it["kind"] in ("attack", "special", "mode")]
    if len(attackers) >= 2 and fw >= E.firewall_cost(st, "cover"):
        return "cover"
    if st.has("nonlethal") and fw >= E.firewall_cost(st, "aim") and any(e.hp <= 20 for e in st.alive_units(E.ENEMY)):
        return "aim"
    return None


POLICIES: Dict[str, Policy] = {"greedy": greedy_choice, "attack_only": attack_only, "random": random_choice}


def make_players(n: int, cfg: Dict) -> List[E.Combatant]:
    out = []
    for i in range(n):
        row = dict(JOBS[i % len(JOBS)])
        row["agility"] = int(row["agility"]) + (i // len(JOBS))
        out.append(E.player_combatant(f"p{i + 1}", row, f"플레이어{i + 1}", cfg))
    return out


def battle_config(bdef: E.BattleDef, overrides: Dict[str, str]) -> Dict:
    merged = dict(bdef.overrides)
    merged.update(overrides)
    return cfgmod.build_config(merged)


def play_one(gd: D.GameData, battle_id: str, n_players: int, cfg: Dict, rng: random.Random,
             policy: Policy = greedy_choice, max_rounds: int = 60, use_firewall: bool = True):
    bdef = gd.battles[battle_id]
    enemies = E.enemy_combatants(bdef, gd.enemies, hp_mult=float(cfg.get("enemy_hp_mult", 1.0)))
    st = E.new_battle(make_players(n_players, cfg), enemies, gd.cards, cfg, host_id="", battle_id=bdef.id,
                      battle_name=bdef.name, gimmicks=bdef.gimmicks, rng=rng)
    used: Counter = Counter()
    overkills = 0
    while st.status != E.ENDED and st.round_no() <= max_rounds:
        if st.status == E.ROUND_OPEN:
            if use_firewall and policy is greedy_choice:
                opt = greedy_firewall(st, rng)
                if opt:
                    try:
                        E.use_firewall(st, next(iter(E.acting_players(st))).id if E.acting_players(st) else "", opt, rng)
                    except (E.RuleError, StopIteration):
                        pass
            for p in E.acting_players(st):
                hand = E.draw_hand(st, p.id, rng)
                ch = policy(st, p, hand, rng)
                if ch:
                    try:
                        E.submit_choice(st, p.id, ch.card_id, ch.target_id)
                        used[ch.card_id] += 1
                    except E.RuleError:
                        pass
            rep = E.resolve_round(st, rng)
            for line in rep["lines"]:
                if line.get("side") == E.ENEMY and line.get("card_id"):
                    used["E:" + line["card_id"]] += 1
                overkills += sum(1 for t in line.get("targets") or [] if t.get("overkill"))
        elif st.status == E.ESCAPE:
            safe = E.safe_escape_action(st.escape.get("hint", ""))
            for p in E.escape_players(st):
                act = safe if (safe and policy is not random_choice) else rng.choice(E.ESCAPE_ACTIONS)
                E.submit_escape(st, p.id, act)
            E.resolve_escape(st, rng)
    deaths = sum(1 for p in st.players.values() if not p.alive())
    looted = sum(1 for p in st.players.values() if p.looted)
    outcome = st.end_reason or ("timeout" if st.status != E.ENDED else "")
    return st.winner, outcome, st.round_no(), deaths, looted, overkills, used


def run(gd: D.GameData, battle_id: str, n_players: int, games: int, seed: int, cfg: Dict, policy: Policy) -> str:
    rng = random.Random(seed)
    wins = 0
    rounds: List[int] = []
    deaths_total = looted_total = ok_total = 0
    outcomes: Counter = Counter()
    used: Counter = Counter()
    for _ in range(games):
        w, outcome, r, d, lo, ok, u = play_one(gd, battle_id, n_players, cfg, rng, policy)
        wins += 1 if w in (E.ALLY, E.ESCAPED) else 0
        outcomes[outcome] += 1
        rounds.append(r)
        deaths_total += d
        looted_total += lo
        ok_total += ok
        used.update(u)
    q = statistics.quantiles(rounds, n=4) if len(rounds) >= 2 else [rounds[0]] * 3
    top = ", ".join(f"{k}×{v}" for k, v in used.most_common(6))
    oc = " ".join(f"{k}:{v / games:.0%}" for k, v in outcomes.most_common())
    name = next(k for k, v in POLICIES.items() if v is policy)
    return (f"[{battle_id}] {n_players}인 {name:11s} ×{games}: 승률 {wins / games:.0%} ({oc}) "
            f"라운드 p25/p50/p75 {q[0]:.0f}/{q[1]:.0f}/{q[2]:.0f}, 사망 {deaths_total / games:.2f} 약탈 {looted_total / games:.2f} "
            f"과잉 {ok_total / games:.2f}\n    카드: {top}")


def _default_players(bdef: E.BattleDef) -> List[int]:
    s = (bdef.players or "").strip()
    if "-" in s:
        a, b = s.split("-", 1)
        try:
            return list(range(int(a), int(b) + 1))
        except ValueError:
            pass
    try:
        return [int(s)]
    except ValueError:
        return [2, 3, 4]


def main(argv=None):
    ap = argparse.ArgumentParser(description="카드 전투 시뮬레이션")
    ap.add_argument("--players", type=int, default=None, help="기본: Battles.csv players 열")
    ap.add_argument("--battle", default="scav4")
    ap.add_argument("--games", type=int, default=300)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--data", default=None, help="CSV 디렉터리 (기본 data/cardbattle)")
    ap.add_argument("--set", action="append", default=[], metavar="key=value")
    ap.add_argument("--policy", choices=sorted(POLICIES) + ["all"], default="all")
    ap.add_argument("--all", action="store_true", help="모든 전투")
    a = ap.parse_args(argv)

    gd = D.load_game_data(D.CsvSource(a.data))
    overrides = dict(kv.split("=", 1) for kv in a.set)
    policies = list(POLICIES.values()) if a.policy == "all" else [POLICIES[a.policy]]
    battles = list(gd.battles) if a.all else [a.battle]
    for bid in battles:
        if bid not in gd.battles:
            raise SystemExit(f"전투 id 없음: {bid} (가능: {', '.join(gd.battles)})")
        bdef = gd.battles[bid]
        cfg = battle_config(bdef, overrides)
        for n in ([a.players] if a.players else _default_players(bdef)):
            for pol in policies:
                print(run(gd, bid, n, a.games, a.seed, cfg, pol))


if __name__ == "__main__":
    main()
