import random

import pytest

from fugitive import config as cfg
from fugitive import engine as E


def make(hunters=(("1", "철수"), ("2", "영희")), map_id="car2077_12", start="A2", **over):
    c, _ = cfg.build_config(len(hunters))
    c["contact_capture_chance"] = 1.0
    c["capture_mode"] = "contact"
    c.update(over)
    st = E.new_game(E.GameMap.builtin(map_id), c, list(hunters), fugitive_start=start, rng=random.Random(1))
    return st


def place(st, uid, room):
    st.hunters[uid].room = room


def test_maps_validate():
    for mid in cfg.BUILTIN_MAPS:
        m = E.GameMap.builtin(mid)
        assert len(m.rooms) == int(mid.split("_")[1])


def test_same_device_adjacent_rejected():
    rows = cfg.map_rows("car2077_12")
    for r in rows:
        if r["room"] == "A1":
            r["device"] = "coffeepot"   # A2 도 coffeepot
    with pytest.raises(cfg.ConfigError):
        E.GameMap.from_rows("car2077_12", rows)


def test_hunter_move_must_be_adjacent():
    st = make()
    place(st, "1", "C1")
    with pytest.raises(E.RuleError):
        E.submit_hunter_order(st, "1", "move", "A1")
    E.submit_hunter_order(st, "1", "move", "B1")
    assert st.hunters["1"].order.target == "B1"


def test_colocation_capture():
    st = make(start="A2")
    place(st, "1", "A1"); place(st, "2", "C4")
    E.submit_hunter_order(st, "1", "move", "A2")
    E.submit_fugitive_order(st, None)
    rep = E.resolve_round(st, random.Random(0))
    assert st.status == E.CAPTURED and st.captured_by == "1" and rep["capture_how"] == "colocate"


def test_edge_swap_capture():
    st = make(start="A2")
    place(st, "1", "A3"); place(st, "2", "C4")
    E.submit_hunter_order(st, "1", "move", "A2")
    E.submit_fugitive_order(st, "A3")
    E.resolve_round(st, random.Random(0))
    assert st.captured_by == "1" and st.capture_how == "swap"


def test_doorlock_blocks_hunter_and_is_public():
    st = make(start="A2")
    place(st, "1", "A3"); place(st, "2", "C4")
    E.submit_hunter_order(st, "1", "move", "A2")
    E.submit_fugitive_order(st, None, "doorlock", "A2-A3")
    rep = E.resolve_round(st, random.Random(0))
    assert st.status == E.ROUND_OPEN
    assert st.hunters["1"].room == "A3"
    assert rep["locks"] == ["A2-A3"]
    assert any(e["type"] == "door_locked" for e in rep["events"])
    assert st.fugitive.ram == st.config["ram_max"] - 2 + 1


def test_doorlock_range_enforced():
    st = make(start="A2", doorlock_range=0)
    with pytest.raises(E.RuleError):
        E.submit_fugitive_order(st, None, "doorlock", "C3-C4")
    st.config["doorlock_range"] = -1
    E.submit_fugitive_order(st, None, "doorlock", "C3-C4")


def test_hide_requires_curtain_and_needs_search():
    st = make(start="A2")
    with pytest.raises(E.RuleError):
        E.submit_fugitive_order(st, None, "hide")
    st.fugitive.room = "B2"; st.position_history = ["B2"]
    place(st, "1", "A2"); place(st, "2", "C4")
    E.submit_hunter_order(st, "1", "move", "B2")
    E.submit_fugitive_order(st, "B3", "hide")     # hide 는 이동을 취소한다
    rep = E.resolve_round(st, random.Random(0))
    assert st.status == E.ROUND_OPEN and st.fugitive.room == "B2"
    assert rep["signature"] == "curtain"
    # 다음 라운드: 수색이면 잡힌다 (hide_duration=2)
    E.submit_hunter_order(st, "1", "search")
    E.resolve_round(st, random.Random(0))
    assert st.captured_by == "1" and st.capture_how == "search"


def test_hide_expires():
    st = make(start="B2", hide_duration=1)
    place(st, "1", "A2"); place(st, "2", "C4")
    E.submit_fugitive_order(st, None, "hide")
    E.resolve_round(st, random.Random(0))
    E.submit_hunter_order(st, "1", "move", "B2")
    E.resolve_round(st, random.Random(0))
    assert st.captured_by == "1" and st.capture_how == "colocate"


def test_scan_budget_and_random_order():
    st = make(hunters=(("1", "a"), ("2", "b"), ("3", "c")), start="A2", scan_budget=1)
    for u in ("1", "2", "3"):
        place(st, u, "C4")
        E.submit_hunter_order(st, u, "scan")
    rep = E.resolve_round(st, random.Random(5))
    kinds = [e["type"] for e in rep["events"]]
    assert kinds.count("scan") == 1 and kinds.count("scan_busy") == 2
    scan = next(e for e in rep["events"] if e["type"] == "scan")
    assert scan["device"] == "coffeepot" and scan["uid"] == rep["order"][0]


def test_blackout_suppresses_report():
    st = make(start="A3")
    place(st, "1", "A2"); place(st, "2", "C4")
    E.submit_hunter_order(st, "2", "scan")
    E.submit_fugitive_order(st, "A4", "blackout")
    rep = E.resolve_round(st, random.Random(0))
    assert rep["blackout"] and rep["signal"] == "noise" and rep["signature"] is None and rep["markers"] == []
    assert any(e["type"] == "scan_noise" for e in rep["events"])


def test_distract_decoy_marker():
    st = make(start="B3")
    place(st, "1", "A1"); place(st, "2", "C1")
    E.submit_fugitive_order(st, None, "distract", "C4")
    rep = E.resolve_round(st, random.Random(0))
    assert rep["markers"] == ["C4"] and rep["signature"] == "speaker"


def test_overload_dazes_and_blocks_search():
    st = make(start="A2")
    place(st, "1", "A1"); place(st, "2", "A2")
    E.submit_hunter_order(st, "1", "move", "A2")
    E.submit_hunter_order(st, "2", "search")
    E.submit_fugitive_order(st, "A3", "overload")
    rep = E.resolve_round(st, random.Random(0))
    assert st.status == E.ROUND_OPEN
    assert st.hunters["1"].dazed_until == 2 and st.hunters["2"].dazed_until == 2
    assert any(e["type"] == "search_steam" for e in rep["events"])
    with pytest.raises(E.RuleError):
        E.submit_hunter_order(st, "1", "move", "A1")


def test_ping_returns_orders_once_per_round():
    st = make(start="A2")
    E.submit_hunter_order(st, "1", "scan")
    got = E.cast_ping(st)
    assert dict(got)["철수"].type == "scan" and st.fugitive.ram == st.config["ram_max"] - 1 and st.fugitive.trace == 5
    with pytest.raises(E.RuleError):
        E.cast_ping(st)


def test_trace_tiers_markers_and_traced():
    st = make(start="A2", trace_passive=0)
    place(st, "1", "C4"); place(st, "2", "C1")
    st.fugitive.trace = 40
    E.submit_fugitive_order(st, "A3")
    rep = E.resolve_round(st, random.Random(0))          # R1 → history [A2, A3], lag 2 → idx -1 → 없음
    assert rep["markers"] == []
    E.submit_fugitive_order(st, "A4")
    rep = E.resolve_round(st, random.Random(0))          # history [A2,A3,A4], lag2 → A2
    assert rep["markers"] == ["A2"]
    st.fugitive.trace = 70
    E.submit_fugitive_order(st, "B4")
    rep = E.resolve_round(st, random.Random(0))          # lag1 → A4
    assert rep["markers"] == ["A4"]
    st.fugitive.trace = 100
    E.submit_fugitive_order(st, "C4" if st.hunters["1"].room != "C4" else None)
    rep = E.resolve_round(st, random.Random(0))
    assert rep["tier"] == 3 and st.fugitive.hacks_disabled and rep["traced"]


def test_failsafes_freeze():
    st = make(start="A2", max_rounds=1)
    place(st, "1", "C4"); place(st, "2", "C1")
    rep = E.resolve_round(st, random.Random(0))
    assert st.fugitive.frozen and rep["frozen"]
    with pytest.raises(E.RuleError):
        E.submit_fugitive_order(st, "A1")

    st = make(start="A2", overheat_rounds=2, trace_passive=100)
    place(st, "1", "C4"); place(st, "2", "C1")
    E.resolve_round(st, random.Random(0))      # traced_at = 1
    E.resolve_round(st, random.Random(0))
    assert not st.fugitive.frozen
    E.resolve_round(st, random.Random(0))      # R3 - 1 >= 2
    assert st.fugitive.frozen


def test_timeout_defaults_and_signal_band():
    st = make(start="A2")
    place(st, "1", "C4"); place(st, "2", "C1")
    rep = E.resolve_round(st, random.Random(0), timed_out=True)
    assert rep["timed_out"] and rep["signal"] == "medium"
    assert st.log[-1]["hunter_orders"]["1"]["type"] == "stay"


def test_serialization_roundtrip():
    st = make(start="A2")
    E.submit_hunter_order(st, "1", "scan")
    E.submit_fugitive_order(st, "A3", "doorlock", "A2-B2")
    d = st.to_dict()
    st2 = E.GameState.from_dict(d)
    assert st2.to_dict() == d
    E.resolve_round(st2, random.Random(0))
    assert st2.round_no == 2


def test_public_view_has_no_fugitive_fields():
    st = make(start="A2")
    place(st, "1", "C4"); place(st, "2", "C1")
    E.submit_fugitive_order(st, "A3")
    rep = E.resolve_round(st, random.Random(0))
    assert rep["playback"]["fugitive_path"] == ["A2", "A3"]      # 보고 dict 에는 있지만 (관제 전용)
    pv = E.public_view(st)
    dumped = str(pv.__dict__)
    assert "fugitive" not in dumped and "ram" not in dumped
    assert "fugitive_path" not in dumped                        # PublicView 는 도주자 경로를 절대 내보내지 않는다
    assert pv.playback == rep["playback"]["hunters"]            # 공개 플레이백(헌터 이동/결과)만 노출
    assert pv.round_no == 1 and len(pv.hunters) == 2


def test_playback_block_and_seed_replay():
    st = make(hunters=(("1", "a"), ("2", "b"), ("3", "c")), start="A2", scan_budget=1, capture_mode="search")
    place(st, "1", "A3"); place(st, "2", "C4"); place(st, "3", "C1")
    E.submit_hunter_order(st, "1", "move", "A2")                 # 잠긴 문에 막힌다
    E.submit_hunter_order(st, "2", "scan")
    E.submit_fugitive_order(st, "B2", "doorlock", "A2-A3")       # 3 은 미제출 → stay
    snapshot = st.to_dict()
    rep = E.resolve_round(st, random.Random(7))
    pb = rep["playback"]
    assert [p["id"] for p in pb["hunters"]] == rep["order"]      # 정산 순서
    by = {p["id"]: p for p in pb["hunters"]}
    assert by["1"] == {"id": "1", "from": "A3", "to": "A3", "blocked_edge": "A2-A3", "action": "move", "result": None}
    assert by["2"] == {"id": "2", "from": "C4", "to": "C4", "blocked_edge": None, "action": "scan", "result": "scan_ok"}
    assert by["3"] == {"id": "3", "from": "C1", "to": "C1", "blocked_edge": None, "action": "stay", "result": "stay"}
    assert pb["fugitive_path"] == ["A2", "B2"]
    entry = st.log[-1]
    assert entry["playback"] == pb["hunters"] and entry["fugitive_path"] == ["A2", "B2"]
    assert isinstance(entry["seed"], int)
    # 기록된 시드로 같은 상태를 다시 정산하면 같은 보고가 나온다 (리플레이)
    st2 = E.GameState.from_dict(snapshot)
    rep2 = E.resolve_round(st2, random.Random(999), seed=entry["seed"])
    assert rep2 == rep and st2.log[-1]["seed"] == entry["seed"]


def test_playback_results_search_capture_and_steam():
    st = make(hunters=(("1", "a"), ("2", "b"), ("3", "c")), start="A2", capture_mode="search")
    place(st, "1", "A2"); place(st, "2", "A1"); place(st, "3", "C4")
    E.submit_hunter_order(st, "1", "search")                     # 체포
    E.submit_hunter_order(st, "2", "search")                     # 빈 방
    E.submit_fugitive_order(st, None)
    rep = E.resolve_round(st, random.Random(0))
    by = {p["id"]: p for p in rep["playback"]["hunters"]}
    assert st.status == E.CAPTURED and by["1"]["result"] == "captured" and by["2"]["result"] == "search_empty"
    # 증기: 수색은 search_steam, 들어온 헌터는 dazed
    st = make(hunters=(("1", "a"), ("2", "b")), start="A2", capture_mode="search")
    place(st, "1", "A1"); place(st, "2", "A2")
    E.submit_hunter_order(st, "1", "move", "A2")
    E.submit_hunter_order(st, "2", "search")
    E.submit_fugitive_order(st, "A3", "overload")
    rep = E.resolve_round(st, random.Random(0))
    by = {p["id"]: p for p in rep["playback"]["hunters"]}
    assert by["1"]["result"] == "dazed" and by["2"]["result"] == "search_steam"


def test_glimpse_fires_in_search_mode_and_is_public():
    from fugitive import render as RD
    st = make(start="A2", capture_mode="search")
    place(st, "1", "A1"); place(st, "2", "C4")
    E.submit_hunter_order(st, "1", "move", "A2")                 # 같은 방에서 라운드를 마친다 (수색 아님)
    E.submit_fugitive_order(st, None)
    rep = E.resolve_round(st, random.Random(0))
    assert st.status == E.ROUND_OPEN                             # 체포 로직은 그대로: search 모드에서 같은 방은 체포가 아니다
    assert [e for e in rep["events"] if e["type"] == "glimpse"] == [{"type": "glimpse", "uid": "1", "room": "A2"}]
    assert E.public_view(st).glimpses == ["A2"]
    assert "A2 에서 침입자 신호의 주인을 목격" in RD.report_text(rep, {"1": "철수", "2": "영희"})
    # 도주자가 헌터가 서 있는 방으로 들어와도 목격된다
    st = make(start="A2", capture_mode="search")
    place(st, "1", "A3"); place(st, "2", "C4")
    E.submit_hunter_order(st, "1", "stay")
    E.submit_fugitive_order(st, "A3")
    rep = E.resolve_round(st, random.Random(0))
    assert st.status == E.ROUND_OPEN and any(e["type"] == "glimpse" and e["room"] == "A3" for e in rep["events"])


def test_glimpse_not_in_contact_mode_and_not_when_captured():
    st = make(start="A2")                                        # contact 모드: contact_miss/colocate 가 담당
    place(st, "1", "A1"); place(st, "2", "C4")
    E.submit_hunter_order(st, "1", "move", "A2")
    E.submit_fugitive_order(st, None)
    rep = E.resolve_round(st, random.Random(0))
    assert not any(e["type"] == "glimpse" for e in rep["events"])
    st = make(start="A2", capture_mode="search")
    place(st, "1", "A2"); place(st, "2", "A1")
    E.submit_hunter_order(st, "1", "search")
    E.submit_hunter_order(st, "2", "move", "A2")
    E.submit_fugitive_order(st, None)
    rep = E.resolve_round(st, random.Random(0))
    assert st.status == E.CAPTURED and not any(e["type"] == "glimpse" for e in rep["events"])


def test_hide_suppresses_glimpse():
    st = make(start="B2", capture_mode="search")                 # B2 = curtain
    place(st, "1", "A2"); place(st, "2", "C4")
    E.submit_hunter_order(st, "1", "move", "B2")
    E.submit_fugitive_order(st, None, "hide")
    rep = E.resolve_round(st, random.Random(0))
    assert rep["signature"] == "curtain" and not any(e["type"] == "glimpse" for e in rep["events"])
    # 은신 2라운드째에도 목격 없음, 끝나면 다시 목격된다
    E.submit_hunter_order(st, "1", "stay")
    rep = E.resolve_round(st, random.Random(0))
    assert not any(e["type"] == "glimpse" for e in rep["events"])
    E.submit_hunter_order(st, "1", "stay")
    rep = E.resolve_round(st, random.Random(0))
    assert any(e["type"] == "glimpse" and e["room"] == "B2" for e in rep["events"])


def test_steam_suppresses_glimpse():
    st = make(start="A2", capture_mode="search")                 # A2 = coffeepot
    place(st, "1", "A1"); place(st, "2", "C4")
    E.submit_hunter_order(st, "1", "move", "A2")                 # 증기 방으로 들어가 도주자와 같은 방에서 라운드를 마친다
    E.submit_fugitive_order(st, None, "overload")                # 도주자는 제자리에서 과부하
    rep = E.resolve_round(st, random.Random(0))
    assert st.status == E.ROUND_OPEN and rep["steam"] == ["A2"]
    assert any(e["type"] == "dazed" and e["uid"] == "1" for e in rep["events"])
    assert not any(e["type"] == "glimpse" for e in rep["events"])
    assert E.public_view(st).glimpses == []
    # 증기가 걷힌 다음 라운드, 같은 방이면 다시 목격된다 (헌터 1 은 기절 → 자동 대기)
    E.submit_fugitive_order(st, None)
    rep = E.resolve_round(st, random.Random(0))
    assert any(e["type"] == "glimpse" and e["uid"] == "1" and e["room"] == "A2" for e in rep["events"])


def test_blackout_does_not_suppress_glimpse():
    st = make(start="A3", capture_mode="search")                 # A3 = light
    place(st, "1", "A2"); place(st, "2", "C4")
    E.submit_hunter_order(st, "1", "move", "A3")
    E.submit_fugitive_order(st, None, "blackout")
    rep = E.resolve_round(st, random.Random(0))
    assert rep["blackout"] and rep["markers"] == [] and rep["signature"] is None
    assert any(e["type"] == "glimpse" and e["room"] == "A3" for e in rep["events"])
    assert E.public_view(st).glimpses == ["A3"]


def test_config_coerce():
    assert cfg.coerce("early_resolve", "off") is False
    assert cfg.coerce("ram_max", "12") == 12
    with pytest.raises(cfg.ConfigError):
        cfg.coerce("signal_mode", "loud")
    with pytest.raises(cfg.ConfigError):
        cfg.coerce("nope", 1)
    c, mid = cfg.build_config(6)
    assert mid == "car2077_16" and c["scan_budget"] == 2


def test_doorlock_range_message_lists_adjacent_doors():
    st = make(map_id="car2077_9", start="A2", doorlock_range=0)
    st.fugitive.ram = 10
    with pytest.raises(E.RuleError) as ei:
        E.submit_fugitive_order(st, None, "doorlock", "C1-C2")
    msg = str(ei.value)
    assert "A2" in msg and "A1-A2" in msg and "A2-B2" in msg and "C1-C2" not in msg.split("가능:")[1]
    assert sorted(E.lockable_edges(st)) == ["A1-A2", "A2-A3", "A2-B2"]
    E.submit_fugitive_order(st, None, "doorlock", "A2-B2")


def test_device_hack_message_names_room_and_candidates():
    st = make(map_id="car2077_9", start="A2")
    st.fugitive.ram = 10
    with pytest.raises(E.RuleError) as ei:
        E.submit_fugitive_order(st, None, "blackout", "C2")
    msg = str(ei.value)
    assert "광학 재부팅" in msg and "조명" in msg and "A2" in msg and "커피포트" in msg
    assert "A3" in msg and "C2" in msg


def test_normalize_map_id_absorbs_typing_noise():
    n = cfg.normalize_map_id
    for raw in ("car2077_16", " CAR2077_16 ", "car2077-16", "ｃａｒ２０７７＿１６", "car2077_16​"):
        assert n(raw) == "car2077_16", raw
    assert {"car2077_9", "car2077_12", "car2077_16"} <= {n(k) for k in cfg.BUILTIN_MAPS}


def test_image_renderer_finds_repo_font_even_with_foreign_env_path(monkeypatch):
    from fugitive import image_render as R
    monkeypatch.setenv("FUGITIVE_FONT_PATH", "D:/somewhere/else/fontYouandiModernTR.ttf")
    p = R.font_path()
    assert p and p.endswith("fontYouandiModernTR.ttf")
    monkeypatch.setenv("FUGITIVE_FONT_PATH", "")
    assert R.font_path() is not None          # 루트/패키지에 놓인 글꼴을 자동 인식
    assert R.unavailable_reason() is None


def test_hunter_colors_from_config_reach_public_view_and_parse():
    from fugitive import image_render as R
    st = make(hunters=(("1", "a"), ("2", "b"), ("3", "c")), hunter_colors="#FF0000, 0F0 ,zzz")
    cols = [h["color"] for h in E.public_view(st).hunters]
    assert cols == ["#FF0000", "0F0", "zzz"]
    assert R.parse_color("#FF0000", (0, 0, 0)) == (255, 0, 0)
    assert R.parse_color("0F0", (0, 0, 0)) == (0, 255, 0)
    assert R.parse_color("zzz", (1, 2, 3)) == (1, 2, 3)
    st2 = make(hunter_colors="")
    assert all(h["color"] == "" for h in E.public_view(st2).hunters)
    assert R.render(E.public_view(st)) is not None   # 실제로 그려진다


def test_hunter_fixed_color_overrides_palette():
    st = make(hunters=(("1", "a"), ("2", "b")), hunter_colors="#111111,#222222")
    st.hunters["2"].color = "#ABCDEF"
    cols = [h["color"] for h in E.public_view(st).hunters]
    assert cols == ["#111111", "#ABCDEF"]
    back = E.GameState.from_dict(st.to_dict())
    assert back.hunters["2"].color == "#ABCDEF"


def test_capture_room_only_public_after_capture():
    from fugitive import render as RD
    st = make(map_id="car2077_9", start="A2")
    assert E.public_view(st).capture_room is None
    assert "★" not in RD.table_text(E.public_view(st))
    place(st, "1", "A2")
    E.submit_hunter_order(st, "1", "search")
    E.submit_fugitive_order(st, None)
    E.resolve_round(st, random.Random(1))
    assert st.status == E.CAPTURED
    pv = E.public_view(st)
    assert pv.capture_room == "A2"
    t = RD.table_text(pv)
    assert "★" in t and "체포 지점" in t
    from fugitive import image_render as R
    assert R.render(pv) is not None


def test_builtin_maps_are_distinct_and_large_ones_have_16_plus_rooms():
    sigs = {}
    for mid in cfg.BUILTIN_MAPS:
        m = E.GameMap.builtin(mid)
        sig = tuple(sorted((r.id, r.device, tuple(sorted(r.neighbors))) for r in m.rooms.values()))
        assert sig not in sigs, f"{mid} 은 {sigs[sig]} 와 같은 맵"
        sigs[sig] = mid
        # 문 구조만 봐도 서로 달라야 한다 (장치 배치만 다른 복사본 금지)
    door_sigs = {}
    for mid in cfg.BUILTIN_MAPS:
        ds = tuple(E.GameMap.builtin(mid).edges())
        assert ds not in door_sigs, f"{mid} 의 문 구조가 {door_sigs[ds]} 와 같음"
        door_sigs[ds] = mid
    big = [mid for mid in cfg.BUILTIN_MAPS if len(E.GameMap.builtin(mid).rooms) >= 16]
    assert len(big) >= 4
    for mid in big:
        m = E.GameMap.builtin(mid)
        # 모든 장치가 최소 3방씩은 있어야 시그니처/장치 퀵핵이 의미 있다
        for dev in cfg.DEVICES:
            assert len(m.rooms_with_device(dev)) >= 3, (mid, dev)
        # 문이 하나뿐인 막다른 방이 너무 많으면 도주자가 갇힌다
        dead_ends = [r for r in m.rooms.values() if len(r.neighbors) == 1]
        assert len(dead_ends) <= len(m.rooms) // 4, (mid, dead_ends)
