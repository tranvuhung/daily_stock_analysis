# -*- coding: utf-8 -*-
"""
===================================
Unified real-time quote types & circuit breaker
===================================

Design goals:
1. Unify the real-time quote structure returned by each data source
2. Implement a circuit-breaker/cooldown mechanism to avoid repeated requests after consecutive failures
3. Support failover across multiple data sources

Usage:
- Every Fetcher's get_realtime_quote() returns UnifiedRealtimeQuote
- CircuitBreaker manages the circuit-breaker state of each data source
"""

import logging
import time
from threading import RLock
from dataclasses import dataclass
from typing import Optional, Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)


# ============================================
# Common type conversion helpers
# ============================================
# Design notes:
# Raw data types returned by data sources are inconsistent (str/float/int/NaN);
# these functions convert them uniformly so each Fetcher does not redefine them.

def safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    """
    Safely convert to float

    Handles:
    - None / empty string -> default
    - pandas NaN / numpy NaN -> default
    - numeric string -> float
    - already numeric -> float

    Args:
        val: value to convert
        default: default value when conversion fails

    Returns:
        The converted float, or the default
    """
    try:
        if val is None:
            return default
        
        # Handle strings
        if isinstance(val, str):
            val = val.strip()
            if val == "" or val == "-" or val == "--":
                return default
        
        # Handle pandas/numpy NaN
        # Use math.isnan instead of pd.isna to avoid a hard dependency on pandas
        import math
        try:
            if math.isnan(float(val)):
                return default
        except (ValueError, TypeError):
            pass
        
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val: Any, default: Optional[int] = None) -> Optional[int]:
    """
    Safely convert to int

    Converts to float first, then truncates, to handle values like "123.0"

    Args:
        val: value to convert
        default: default value when conversion fails

    Returns:
        The converted int, or the default
    """
    f_val = safe_float(val, default=None)
    if f_val is not None:
        return int(f_val)
    return default


class RealtimeSource(Enum):
    """Nguồn giá realtime. / Real-time quote data source."""
    TVSI_MQTT = "tvsi_mqtt"         # Feed push MQTT của TVSI (Việt Nam)
    FALLBACK = "fallback"           # Degraded fallback


@dataclass
class UnifiedRealtimeQuote:
    """
    Unified real-time quote data structure

    Design principles:
    - Fields may differ across data sources; missing fields are None
    - The main flow reads fields via getattr(quote, field, None) for compatibility
    - The source field records the data origin for debugging
    """
    code: str
    name: str = ""
    source: RealtimeSource = RealtimeSource.FALLBACK

    # === Data quality metadata (filled in uniformly by DataFetcherManager) ===
    fetched_at: Optional[str] = None             # Time fetched by this system (ISO 8601 datetime)
    provider_timestamp: Optional[str] = None     # Provider's actual quote time (ISO 8601 datetime)
    is_stale: Optional[bool] = None              # True when provider_timestamp exceeds the minimum TTL threshold
    stale_seconds: Optional[int] = None          # Seconds between provider_timestamp and fetched_at
    fallback_from: Optional[str] = None          # Token of the failed preferred source in a whole-source fallback
    market: Optional[str] = None                 # Market tag (cn/hk/us/jp/kr/tw)
    currency: Optional[str] = None               # Quote currency (JPY/KRW/TWD/USD/HKD/CNY, etc.)
    data_quality: Optional[str] = None           # ok/partial/unavailable
    missing_fields: Optional[list[str]] = None   # Key fields missing from the provider
    
    # === Core price data (available from almost all sources) ===
    price: Optional[float] = None           # Latest price
    change_pct: Optional[float] = None      # Change percent (%)
    change_amount: Optional[float] = None   # Change amount
    
    # === Volume/price metrics (may be missing from some sources) ===
    volume: Optional[int] = None            # Volume (shares, same basis as historical daily bars)
    amount: Optional[float] = None          # Turnover amount (CNY)
    volume_ratio: Optional[float] = None    # Volume ratio
    turnover_rate: Optional[float] = None   # Turnover rate (%)
    amplitude: Optional[float] = None       # Amplitude (%)
    
    # === Price range ===
    open_price: Optional[float] = None      # Open price
    high: Optional[float] = None            # High price
    low: Optional[float] = None             # Low price
    pre_close: Optional[float] = None       # Previous close
    
    # === Valuation metrics (only full-data APIs such as East Money provide these) ===
    pe_ratio: Optional[float] = None        # P/E ratio (dynamic)
    pb_ratio: Optional[float] = None        # P/B ratio
    total_mv: Optional[float] = None        # Total market cap (CNY)
    circ_mv: Optional[float] = None         # Circulating market cap (CNY)
    
    # === Other metrics ===
    change_60d: Optional[float] = None      # 60-day change percent (%)
    high_52w: Optional[float] = None        # 52-week high
    low_52w: Optional[float] = None         # 52-week low

    # === Trường riêng thị trường Việt Nam (chỉ nguồn đầy đủ như FiinTrade điền) ===
    # === Vietnam-specific fields (only full sources such as FiinTrade fill these) ===
    ref_price: Optional[float] = None               # Giá tham chiếu của sở / Exchange reference price
    ceiling: Optional[float] = None                 # Giá trần / Ceiling price
    floor: Optional[float] = None                   # Giá sàn / Floor price
    eps_ttm: Optional[float] = None                 # EPS 4 quý gần nhất (VND) / Trailing EPS
    roe: Optional[float] = None                     # ROE (%)
    beta: Optional[float] = None                    # Beta so với thị trường / Market beta
    foreign_buy_value: Optional[float] = None       # Khối ngoại mua trong phiên (VND)
    foreign_sell_value: Optional[float] = None      # Khối ngoại bán trong phiên (VND)
    foreign_net_value: Optional[float] = None       # Khối ngoại mua ròng (VND, âm = bán ròng)
    foreign_room_remaining: Optional[float] = None  # Room ngoại còn lại (cổ phiếu)
    foreign_ownership_pct: Optional[float] = None   # Tỷ lệ sở hữu nước ngoài (%)
    put_through_volume: Optional[float] = None      # KL giao dịch thoả thuận / Put-through volume
    put_through_value: Optional[float] = None       # Giá trị thoả thuận (VND) / Put-through value
    bid_ask_imbalance: Optional[float] = None       # KL 3 bước mua / (mua + bán), 0-1
    avg_volume_1m: Optional[int] = None             # KL khớp bình quân 1 tháng / 1-month avg volume

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a dict (None values filtered out)"""
        result = {
            'code': self.code,
            'name': self.name,
            'source': self.source.value,
        }
        # Only add non-None fields
        optional_fields = [
            'fetched_at', 'provider_timestamp', 'is_stale', 'stale_seconds',
            'fallback_from', 'market', 'currency', 'data_quality', 'missing_fields',
            'price', 'change_pct', 'change_amount', 'volume', 'amount',
            'volume_ratio', 'turnover_rate', 'amplitude',
            'open_price', 'high', 'low', 'pre_close',
            'pe_ratio', 'pb_ratio', 'total_mv', 'circ_mv',
            'change_60d', 'high_52w', 'low_52w',
            'ref_price', 'ceiling', 'floor', 'eps_ttm', 'roe', 'beta',
            'foreign_buy_value', 'foreign_sell_value', 'foreign_net_value',
            'foreign_room_remaining', 'foreign_ownership_pct',
            'put_through_volume', 'put_through_value', 'bid_ask_imbalance', 'avg_volume_1m',
        ]
        for f in optional_fields:
            val = getattr(self, f, None)
            if val is not None:
                result[f] = val
        return result
    
    def has_basic_data(self) -> bool:
        """Check whether basic price data is present"""
        return self.price is not None and self.price > 0
    
    def has_volume_data(self) -> bool:
        """Check whether volume/price data is present"""
        return self.volume_ratio is not None or self.turnover_rate is not None


class CircuitBreaker:
    """
    Circuit breaker - manages the circuit-breaker/cooldown state of data sources

    Strategy:
    - Open the circuit after N consecutive failures
    - Skip the data source while the circuit is open
    - Automatically move to half-open after the cooldown period
    - In half-open, a single success fully recovers; a failure re-opens the circuit

    State machine:
    CLOSED (normal) --N failures--> OPEN (tripped) --cooldown elapsed--> HALF_OPEN (half-open)
    HALF_OPEN --success--> CLOSED
    HALF_OPEN --failure--> OPEN
    """
    
    # State constants
    CLOSED = "closed"      # Normal state
    OPEN = "open"          # Tripped state (unavailable)
    HALF_OPEN = "half_open"  # Half-open state (probing requests)
    
    def __init__(
        self,
        failure_threshold: int = 3,       # Consecutive failure threshold
        cooldown_seconds: float = 300.0,  # Cooldown time (seconds), default 5 minutes
        half_open_max_calls: int = 1      # Max attempts in half-open state
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_max_calls = half_open_max_calls
        
        # Per-source state {source_name: {state, failures, last_failure_time, half_open_calls}}
        self._states: Dict[str, Dict[str, Any]] = {}
        self._lock = RLock()
    
    def _get_state_locked(self, source: str) -> Dict[str, Any]:
        """Get or initialize a data source's state (caller must hold the lock)."""
        if source not in self._states:
            self._states[source] = {
                'state': self.CLOSED,
                'failures': 0,
                'last_failure_time': 0.0,
                'half_open_calls': 0
            }
        return self._states[source]
    
    def is_available(self, source: str) -> bool:
        """
        Check whether the data source is available

        Returns True if a request may be attempted
        Returns False if the data source should be skipped
        """
        with self._lock:
            state = self._get_state_locked(source)
            current_time = time.time()

            if state['state'] == self.CLOSED:
                return True

            if state['state'] == self.OPEN:
                # Check the cooldown time
                time_since_failure = current_time - state['last_failure_time']
                if time_since_failure >= self.cooldown_seconds:
                    # Cooldown finished, enter half-open (no slot pre-reserved; the HALF_OPEN branch manages slots)
                    state['state'] = self.HALF_OPEN
                    state['half_open_calls'] = 0
                    state['last_failure_time'] = current_time
                    logger.info(f"[CircuitBreaker] {source} hết thời gian nghỉ, chuyển sang trạng thái nửa mở")
                    # Fall through to HALF_OPEN check below
                else:
                    remaining = self.cooldown_seconds - time_since_failure
                    logger.debug(f"[CircuitBreaker] {source} đang ngắt mạch, thời gian nghỉ còn lại: {remaining:.0f}s")
                    return False

            if state['state'] == self.HALF_OPEN:
                if state['half_open_calls'] < self.half_open_max_calls:
                    state['half_open_calls'] += 1
                    return True
                # All probe slots are used up; if the cooldown expires again without a
                # record_success/record_failure callback, reset the slots to allow re-probing,
                # so we never get stuck in HALF_OPEN forever.
                time_since_failure = current_time - state['last_failure_time']
                if time_since_failure >= self.cooldown_seconds:
                    state['half_open_calls'] = 1
                    state['last_failure_time'] = current_time
                    logger.info(f"[CircuitBreaker] {source} thăm dò ở trạng thái nửa mở quá hạn, thăm dò lại")
                    return True
                return False

            return True
    
    def record_inconclusive(self, source: str) -> None:
        """Record an inconclusive probe result (e.g. a None return).

        Only affects the HALF_OPEN state: moves it back to OPEN so it is re-probed after cooldown.
        A no-op in the CLOSED state; does not affect the failure count.
        """
        with self._lock:
            state = self._get_state_locked(source)
            if state['state'] == self.HALF_OPEN:
                state['state'] = self.OPEN
                state['half_open_calls'] = 0
                state['last_failure_time'] = time.time()
                logger.info(f"[CircuitBreaker] {source} kết quả thăm dò nửa mở không xác định, quay lại thời gian nghỉ")

    def record_success(self, source: str) -> None:
        """Record a successful request"""
        with self._lock:
            state = self._get_state_locked(source)

            if state['state'] == self.HALF_OPEN:
                # Success in half-open state, fully recover
                logger.info(f"[CircuitBreaker] {source} yêu cầu ở trạng thái nửa mở thành công, khôi phục bình thường")

            # Reset state
            state['state'] = self.CLOSED
            state['failures'] = 0
            state['half_open_calls'] = 0
    
    def record_failure(self, source: str, error: Optional[str] = None) -> None:
        """Record a failed request"""
        with self._lock:
            state = self._get_state_locked(source)
            current_time = time.time()

            state['failures'] += 1
            state['last_failure_time'] = current_time

            if state['state'] == self.HALF_OPEN:
                # Failure in half-open state, keep the circuit open
                state['state'] = self.OPEN
                state['half_open_calls'] = 0
                logger.warning(f"[CircuitBreaker] {source} yêu cầu ở trạng thái nửa mở thất bại, tiếp tục ngắt mạch {self.cooldown_seconds}s")
            elif state['failures'] >= self.failure_threshold:
                # Threshold reached, open the circuit
                state['state'] = self.OPEN
                logger.warning(f"[CircuitBreaker] {source} thất bại liên tiếp {state['failures']} lần, chuyển sang ngắt mạch "
                              f"(nghỉ {self.cooldown_seconds}s)")
                if error:
                    logger.warning(f"[CircuitBreaker] Lỗi cuối cùng: {error}")
    
    def get_status(self) -> Dict[str, str]:
        """Get the state of all data sources"""
        with self._lock:
            return {source: info['state'] for source, info in self._states.items()}
    
    def reset(self, source: Optional[str] = None) -> None:
        """Reset the circuit breaker state"""
        with self._lock:
            if source:
                if source in self._states:
                    del self._states[source]
            else:
                self._states.clear()


# Global circuit breaker instance (for real-time quotes)
_realtime_circuit_breaker = CircuitBreaker(
    failure_threshold=3,      # Trip after 3 consecutive failures
    cooldown_seconds=300.0,   # 5-minute cooldown
    half_open_max_calls=1
)

# Circuit breaker for the chip-distribution API (more conservative, since this API is less stable)
_chip_circuit_breaker = CircuitBreaker(
    failure_threshold=2,      # Trip after 2 consecutive failures
    cooldown_seconds=600.0,   # 10-minute cooldown
    half_open_max_calls=1
)


def get_realtime_circuit_breaker() -> CircuitBreaker:
    """Get the real-time quote circuit breaker"""
    return _realtime_circuit_breaker


def get_chip_circuit_breaker() -> CircuitBreaker:
    """Get the chip-distribution API circuit breaker"""
    return _chip_circuit_breaker
