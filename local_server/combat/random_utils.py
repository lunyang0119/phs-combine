"""
랜덤 유틸리티 모듈

모든 랜덤 함수를 중앙에서 관리합니다.
시드 설정, 디버깅, 테스트 시 일관된 결과를 위해 사용합니다.
"""

import os
import random
import secrets
import time
import hashlib
import logging
from typing import List, TypeVar, Optional, Sequence

logger = logging.getLogger(__name__)

T = TypeVar('T')


def generate_secure_seed() -> int:
    """여러 엔트로피 소스를 조합한 고품질 시드 생성
    
    조합 요소:
    - os.urandom: 운영체제의 암호학적 난수
    - time.time_ns(): 나노초 타임스탬프
    - os.getpid(): 현재 프로세스 ID
    - id(object()): 임시 객체의 메모리 주소
    - secrets.token_bytes: 추가 암호학적 난수
    """
    components = [
        os.urandom(16),                          # 시스템 암호학적 엔트로피
        str(time.time_ns()).encode(),            # 나노초 (Linux 지원)
        str(os.getpid()).encode(),               # 프로세스 ID
        str(id(object())).encode(),              # 메모리 주소 (매번 다름)
        secrets.token_bytes(16),                 # 추가 암호학적 난수
    ]
    
    # SHA-256으로 혼합하여 균일한 분포 보장
    combined = b''.join(components)
    hash_digest = hashlib.sha256(combined).digest()
    
    # 64비트 정수로 변환
    return int.from_bytes(hash_digest[:8], byteorder='big')


class RandomManager:
    """중앙 집중식 랜덤 관리자"""
    
    _instance: Optional['RandomManager'] = None
    _rng: random.Random
    _seed: Optional[int] = None
    _debug_mode: bool = False
    _auto_reseed: bool = True  # 매 호출마다 자동 리시드 여부
    
    def __new__(cls) -> 'RandomManager':
        """싱글톤 패턴"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._rng = random.Random()
            cls._instance._seed = None
            cls._instance._debug_mode = False
            cls._instance._auto_reseed = False
            cls._instance._initialize_with_secure_seed()
        return cls._instance
    
    def _initialize_with_secure_seed(self) -> None:
        """보안 시드로 초기화"""
        self._seed = generate_secure_seed()
        self._rng.seed(self._seed)
        logger.debug(f"RandomManager 초기화 - 보안 시드 사용")
    
    def set_seed(self, seed: int) -> None:
        """시드 수동 설정 (테스트/디버깅용)"""
        self._seed = seed
        self._rng.seed(seed)
        logger.info(f"랜덤 시드 설정: {seed}")
    
    def reset_seed(self) -> None:
        """새로운 보안 시드로 재초기화"""
        self._initialize_with_secure_seed()
        logger.debug("랜덤 시드 리셋 (보안 시드)")
    
    def get_seed(self) -> Optional[int]:
        """현재 시드 반환"""
        return self._seed
    
    def set_debug_mode(self, enabled: bool) -> None:
        """디버그 모드 설정 (모든 랜덤 호출 로깅)"""
        self._debug_mode = enabled
        logger.info(f"랜덤 디버그 모드: {'ON' if enabled else 'OFF'}")
    
    def set_auto_reseed(self, enabled: bool) -> None:
        """자동 리시드 모드 설정 (매 호출마다 새 시드)"""
        self._auto_reseed = enabled
        logger.info(f"자동 리시드 모드: {'ON' if enabled else 'OFF'}")
    
    def _maybe_reseed(self) -> None:
        """auto_reseed가 켜져 있으면 리시드"""
        if self._auto_reseed:
            self._seed = generate_secure_seed()
            self._rng.seed(self._seed)
    
    # ============ 랜덤 함수들 ============
    
    def get_random(self) -> float:
        """0.0 ~ 1.0 사이의 랜덤 실수"""
        self._maybe_reseed()
        result = self._rng.get_random()
        if self._debug_mode:
            logger.debug(f"get_random() = {result}")
        return result
    
    def randint(self, a: int, b: int) -> int:
        """a ~ b 사이의 랜덤 정수 (양 끝 포함)"""
        self._maybe_reseed()
        result = self._rng.randint(a, b)
        if self._debug_mode:
            logger.debug(f"randint({a}, {b}) = {result}")
        return result
    
    def choice(self, seq: Sequence[T]) -> T:
        """시퀀스에서 랜덤 선택"""
        self._maybe_reseed()
        result = self._rng.choice(seq)
        if self._debug_mode:
            logger.debug(f"choice({list(seq)}) = {result}")
        return result
    
    def choices(self, population: Sequence[T], weights: Optional[Sequence[float]] = None, k: int = 1) -> List[T]:
        """시퀀스에서 k개 랜덤 선택 (중복 허용, 가중치 지원)"""
        self._maybe_reseed()
        result = self._rng.choices(population, weights=weights, k=k)
        if self._debug_mode:
            logger.debug(f"choices(k={k}, weights={weights}) = {result}")
        return result
    
    def shuffle(self, seq: List[T]) -> None:
        """리스트를 제자리에서 섞음"""
        self._maybe_reseed()
        if self._debug_mode:
            before = seq.copy()
        self._rng.shuffle(seq)
        if self._debug_mode:
            logger.debug(f"shuffle({before}) -> {seq}")
    
    def sample(self, population: Sequence[T], k: int) -> List[T]:
        """시퀀스에서 k개 랜덤 선택 (중복 없음)"""
        self._maybe_reseed()
        result = self._rng.sample(population, k)
        if self._debug_mode:
            logger.debug(f"sample(k={k}) = {result}")
        return result
    
    def uniform(self, a: float, b: float) -> float:
        """a ~ b 사이의 균등 분포 랜덤 실수"""
        self._maybe_reseed()
        result = self._rng.uniform(a, b)
        if self._debug_mode:
            logger.debug(f"uniform({a}, {b}) = {result}")
        return result
    
    def roll_dice(self, count: int, sides: int) -> int:
        """주사위 굴리기 (예: 2d10 = roll_dice(2, 10))"""
        self._maybe_reseed()
        total = sum(self._rng.randint(1, sides) for _ in range(count))
        if self._debug_mode:
            logger.debug(f"roll_dice({count}d{sides}) = {total}")
        return total
    
    def secure_randint(self, a: int, b: int) -> int:
        """암호학적으로 안전한 랜덤 정수 (secrets 모듈 사용)"""
        result = secrets.randbelow(b - a + 1) + a
        if self._debug_mode:
            logger.debug(f"secure_randint({a}, {b}) = {result}")
        return result


# 싱글톤 인스턴스
_manager = RandomManager()

# ============ 모듈 레벨 함수들 ============

def get_random() -> float:
    """0.0 ~ 1.0 사이의 랜덤 실수"""
    return _manager.get_random()

def randint(a: int, b: int) -> int:
    """a ~ b 사이의 랜덤 정수 (양 끝 포함)"""
    return _manager.randint(a, b)

def choice(seq: Sequence[T]) -> T:
    """시퀀스에서 랜덤 선택"""
    return _manager.choice(seq)

def choices(population: Sequence[T], weights: Optional[Sequence[float]] = None, k: int = 1) -> List[T]:
    """시퀀스에서 k개 랜덤 선택 (중복 허용, 가중치 지원)"""
    return _manager.choices(population, weights=weights, k=k)

def shuffle(seq: List[T]) -> None:
    """리스트를 제자리에서 섞음"""
    _manager.shuffle(seq)

def sample(population: Sequence[T], k: int) -> List[T]:
    """시퀀스에서 k개 랜덤 선택 (중복 없음)"""
    return _manager.sample(population, k)

def uniform(a: float, b: float) -> float:
    """a ~ b 사이의 균등 분포 랜덤 실수"""
    return _manager.uniform(a, b)

def roll_dice(count: int, sides: int) -> int:
    """주사위 굴리기 (예: 2d10 = roll_dice(2, 10))"""
    return _manager.roll_dice(count, sides)

def secure_randint(a: int, b: int) -> int:
    """암호학적으로 안전한 랜덤 정수"""
    return _manager.secure_randint(a, b)

def set_seed(seed: int) -> None:
    """시드 수동 설정 (테스트/디버깅용)"""
    _manager.set_seed(seed)

def reset_seed() -> None:
    """새로운 보안 시드로 재초기화"""
    _manager.reset_seed()

def get_seed() -> Optional[int]:
    """현재 시드 반환"""
    return _manager.get_seed()

def set_debug_mode(enabled: bool) -> None:
    """디버그 모드 설정"""
    _manager.set_debug_mode(enabled)

def set_auto_reseed(enabled: bool) -> None:
    """자동 리시드 모드 설정"""
    _manager.set_auto_reseed(enabled)