"""몬테카를로 시뮬레이션 — 승률·라운드 수 분포 확인용.

    python -m cardbattle.sim --players 3 --battle b01 --games 500 --seed 1
    python -m cardbattle.sim --all --games 200
    python -m cardbattle.sim --battle b02 --set damage_variance=2 --policy random

플레이어 봇 greedy: HP<35% 면 방어, 디버프 없는 고공격 적이 있으면 해킹, 아니면 가장 약한 적 공격.
사람은 봇보다 잘 하므로 결과는 하한선으로 본다.
"""
from __future__ import annotations

import argparse
import random
import statistics
from collections import Counter
from typing import Dict, List, Optional

from . import ai
from . import config as cfgmod
from . import data as D
from . import engine as E


def greedy_choice(st: E.BattleState, me: E.Combatant, hand: List[str], rng: random.Random) -> Optional[E.Choice]:
    cards = [st.cards[c] for c in hand if c in st.cards]
    enemies = st.alive_units(E.ENEMY)
    if not cards or not enemies:
        return None
    if me.hp_ratio() < 0.35 and me.mod("shield") is None:
        d = [c for c in cards if c.kind == "defend"]
        if d:
            c = d[0]
            return E.Choice(me.id, c.id, me.id if c.target in ("self", "ally") else None)
    hurt = [p for p in st.alive_units(E.ALLY) if p.hp_ratio() < 0.4]
    heals = [c for c in cards if c.has("heal") and not c.has("damage")]
    if hurt and heals:
        t = min(hurt, key=lambda p: p.hp_ratio())
        return E.Choice(me.id, heals[0].id, t.id)
    hacks = [c for c in cards if c.kind == "hack"]
    if hacks:
        kinds = {e.kind for c in hacks for e in c.effects}
        fresh = [e for e in enemies if not any(m.kind in kinds for m in e.modifiers)]
        strong = [e for e in fresh if e.physics + e.magic >= 22]
        if strong:
            t = max(strong, key=lambda e: e.hp)
            return E.Choice(me.id, hacks[0].id, t.id)
    attacks = [c for c in cards if c.has("damage")]
    if attacks:
        c = max(attacks, key=lambda c: E.expected_damage(c, me, None, st.config))
        t = min(enemies, key=lambda e: e.hp)
        return E.Choice(me.id, c.id, t.id if c.target == "enemy" else None)
    return ai.choose(st, me, hand, rng)


def random_choice(st: E.BattleState, me: E.Combatant, hand: List[str], rng: random.Random) -> Optional[E.Choice]:
    cards = [st.cards[c] for c in hand if c in st.cards]
    rng.shuffle(cards)
    for c in cards:
        t = E.valid_targets(st, me, c)
        if E.needs_target(c) and not t:
            continue
        return E.Choice(me.id, c.id, rng.choice(t).id if t else None)
    return None


POLICIES = {"greedy": greedy_choice, "random": random_choice}


def make_players(n: int, cfg: Dict) -> List[E.Combatant]:
    out = []
    for i in range(n):
        row = {"max_hp": 40, "physics": 12, "magic": 12, "agility": 10 + i, "charm": 10}
        out.append(E.player_combatant(f"p{i + 1}", row, f"플레이어{i + 1}", cfg))
    return out


def play_one(gd: D.GameData, battle_id: str, n_players: int, cfg: Dict, rng: random.Random,
             policy=greedy_choice, max_rounds: int = 60):
    st = E.new_battle(make_players(n_players, cfg), E.enemy_combatants(gd.battles[battle_id], gd.enemies),
                      gd.cards, cfg, host_id="")
    used: Counter = Counter()
    while st.status != E.ENDED and st.round_no() <= max_rounds:
        for p in E.acting_players(st):
            hand = E.draw_hand(st, p.id, rng)
            ch = policy(st, p, hand, rng)
            if ch:
                try:
                    E.submit_choice(st, p.id, ch.card_id, ch.target_id)
                    used[ch.card_id] += 1
                except E.RuleError:
                    pass
        E.resolve_ally_turn(st, rng)
        if st.status == E.ENEMY_TURN:
            rep = E.run_enemy_turn(st, rng)
            for line in rep["lines"]:
                if line.get("card_id"):
                    used["E:" + line["card_id"]] += 1
    deaths = sum(1 for p in st.players.values() if not p.alive())
    return st.winner, st.round_no(), deaths, used


def run(gd: D.GameData, battle_id: str, n_players: int, games: int, seed: int, cfg: Dict, policy) -> str:
    rng = random.Random(seed)
    wins = 0
    rounds: List[int] = []
    deaths_total = 0
    used: Counter = Counter()
    for _ in range(games):
        w, r, d, u = play_one(gd, battle_id, n_players, cfg, rng, policy)
        wins += 1 if w == E.ALLY else 0
        rounds.append(r)
        deaths_total += d
        used.update(u)
    q = statistics.quantiles(rounds, n=4) if len(rounds) >= 2 else [rounds[0]] * 3
    top = ", ".join(f"{k}×{v}" for k, v in used.most_common(6))
    return (f"[{battle_id}] {n_players}인 ×{games}판: 승률 {wins / games:.0%}, 라운드 p25/p50/p75 "
            f"{q[0]:.0f}/{q[1]:.0f}/{q[2]:.0f} (평균 {statistics.mean(rounds):.1f}), "
            f"판당 아군 사망 {deaths_total / games:.2f}\n    카드: {top}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="카드 전투 시뮬레이션")
    ap.add_argument("--players", type=int, default=3)
    ap.add_argument("--battle", default="b01")
    ap.add_argument("--games", type=int, default=300)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--data", default=None, help="CSV 디렉터리 (기본 data/cardbattle)")
    ap.add_argument("--set", action="append", default=[], metavar="key=value")
    ap.add_argument("--policy", choices=sorted(POLICIES), default="greedy")
    ap.add_argument("--all", action="store_true", help="모든 전투 × 2~4인")
    a = ap.parse_args(argv)

    gd = D.load_game_data(D.CsvSource(a.data))
    overrides = dict(kv.split("=", 1) for kv in a.set)
    cfg = cfgmod.build_config(overrides)
    policy = POLICIES[a.policy]
    if a.all:
        for bid in gd.battles:
            for n in (2, 3, 4):
                print(run(gd, bid, n, a.games, a.seed, cfg, policy))
    else:
        if a.battle not in gd.battles:
            raise SystemExit(f"전투 id 없음: {a.battle} (가능: {', '.join(gd.battles)})")
        print(run(gd, a.battle, a.players, a.games, a.seed, cfg, policy))


if __name__ == "__main__":
    main()
