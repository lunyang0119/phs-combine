"""게임 상태 스냅샷 — 로컬 JSON, 원자적 쓰기. 구글 시트에는 절대 쓰지 않는다."""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from .engine import GameState

logger = logging.getLogger(__name__)
DEFAULT_PATH = os.path.join("data", "fugitive_state.json")


def save(state: Optional[GameState], path: str = DEFAULT_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if state is None:
        if os.path.exists(path):
            os.remove(path)
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state.to_dict(), fh, ensure_ascii=False)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load(path: str = DEFAULT_PATH) -> Optional[GameState]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return GameState.from_dict(json.load(fh))
    except Exception as e:  # noqa: BLE001
        logger.error("추적기 상태 복원 실패: %s", e)
        return None
