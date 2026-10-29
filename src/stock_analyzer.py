# -*- coding: utf-8 -*-
"""
===================================
Trend-trading analyzer based on the user's trading philosophy
===================================

Core trading principles:
1. Strict entry - never chase highs; maximize the win rate of every trade
2. Trend trading - MA5>MA10>MA20 bullish alignment; trade with the trend
3. Efficiency first - focus on stocks with a healthy chip distribution
4. Entry preference - buy on pullbacks near MA5/MA10

Technical criteria:
- Bullish alignment: MA5 > MA10 > MA20
- Bias: (Close - MA5) / MA5 < 5% (do not chase)
- Volume pattern: low-volume pullbacks preferred
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum

import pandas as pd
import numpy as np

from src.config import get_config
from src.schemas.decision_scale import signal_key_for_score

logger = logging.getLogger(__name__)


# Giá trị enum dùng tiếng Anh làm chuẩn (dữ liệu gửi cho LLM/báo cáo); bản địa hoá qua
# src/report_language.py. Logic chấm điểm chỉ so sánh enum member, không so `.value`.
# Enum values are canonical English; localize via src/report_language.py.
class TrendStatus(Enum):
    """Trend status enum."""
    STRONG_BULL = "Strong Bullish"      # MA5 > MA10 > MA20 with widening spread
    BULL = "Bullish Alignment"             # MA5 > MA10 > MA20
    WEAK_BULL = "Weak Bullish"        # MA5 > MA10, but MA10 < MA20
    CONSOLIDATION = "Consolidation"        # MAs intertwined
    WEAK_BEAR = "Weak Bearish"        # MA5 < MA10, but MA10 > MA20
    BEAR = "Bearish Alignment"             # MA5 < MA10 < MA20
    STRONG_BEAR = "Strong Bearish"      # MA5 < MA10 < MA20 with widening spread


class VolumeStatus(Enum):
    """Volume status enum."""
    HEAVY_VOLUME_UP = "Heavy Volume Up"       # Price and volume rising together
    HEAVY_VOLUME_DOWN = "Heavy Volume Down"     # Heavy-volume sell-off
    SHRINK_VOLUME_UP = "Low Volume Up"      # Rising on thin volume
    SHRINK_VOLUME_DOWN = "Low Volume Pullback"    # Low-volume pullback (positive)
    NORMAL = "Normal Volume"


class BuySignal(Enum):
    """Buy signal enum."""
    STRONG_BUY = "Strong Buy"       # Multiple conditions met
    BUY = "Buy"                  # Basic conditions met
    HOLD = "Hold"                 # Already held, can keep holding
    WAIT = "Wait"                 # Wait for a better entry
    SELL = "Sell"                 # Trend weakening
    STRONG_SELL = "Strong Sell"      # Trend broken


class MACDStatus(Enum):
    """MACD status enum."""
    GOLDEN_CROSS_ZERO = "Golden Cross Above Zero"      # DIF crosses above DEA, above the zero axis
    GOLDEN_CROSS = "Golden Cross"                # DIF crosses above DEA
    BULLISH = "Bullish"                    # DIF>DEA>0
    CROSSING_UP = "Crossing Above Zero"             # DIF crosses above zero
    CROSSING_DOWN = "Crossing Below Zero"           # DIF crosses below zero
    BEARISH = "Bearish"                    # DIF<DEA<0
    DEATH_CROSS = "Death Cross"                # DIF crosses below DEA


class RSIStatus(Enum):
    """RSI status enum."""
    OVERBOUGHT = "Overbought"        # RSI > 70
    STRONG_BUY = "Strong"    # 50 < RSI < 70
    NEUTRAL = "Neutral"          # 40 <= RSI <= 60
    WEAK = "Weak"             # 30 < RSI < 40
    OVERSOLD = "Oversold"         # RSI < 30


@dataclass
class TrendAnalysisResult:
    """Trend analysis result."""
    code: str
    
    # Trend assessment
    trend_status: TrendStatus = TrendStatus.CONSOLIDATION
    ma_alignment: str = ""           # MA alignment description
    trend_strength: float = 0.0      # Trend strength 0-100
    
    # Moving average data
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    current_price: float = 0.0
    
    # Bias (deviation from MA5)
    bias_ma5: float = 0.0            # (Close - MA5) / MA5 * 100
    bias_ma10: float = 0.0
    bias_ma20: float = 0.0
    
    # Volume analysis
    volume_status: VolumeStatus = VolumeStatus.NORMAL
    volume_ratio_5d: float = 0.0     # Today's volume / 5-day average volume
    volume_trend: str = ""           # Volume trend description
    # Cơ sở của volume_ratio_5d: "full_day" | "projected_intraday" | "intraday_too_early".
    # Basis of volume_ratio_5d for a still-forming bar.
    volume_ratio_basis: str = "full_day"
    
    # Support and resistance
    support_ma5: bool = False        # Whether MA5 acts as support
    support_ma10: bool = False       # Whether MA10 acts as support
    resistance_levels: List[float] = field(default_factory=list)
    support_levels: List[float] = field(default_factory=list)

    # MACD indicator
    macd_dif: float = 0.0          # DIF fast line
    macd_dea: float = 0.0          # DEA slow line
    macd_bar: float = 0.0           # MACD histogram
    macd_status: MACDStatus = MACDStatus.BULLISH
    macd_signal: str = ""            # MACD signal description

    # RSI indicator
    rsi_6: float = 0.0              # RSI(6) short term
    rsi_12: float = 0.0             # RSI(12) medium term
    rsi_24: float = 0.0             # RSI(24) long term
    rsi_status: RSIStatus = RSIStatus.NEUTRAL
    rsi_signal: str = ""              # RSI signal description

    # Buy signal
    buy_signal: BuySignal = BuySignal.WAIT
    signal_score: int = 0            # Composite score 0-100
    signal_reasons: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code,
            'trend_status': self.trend_status.value,
            'ma_alignment': self.ma_alignment,
            'trend_strength': self.trend_strength,
            'ma5': self.ma5,
            'ma10': self.ma10,
            'ma20': self.ma20,
            'ma60': self.ma60,
            'current_price': self.current_price,
            'bias_ma5': self.bias_ma5,
            'bias_ma10': self.bias_ma10,
            'bias_ma20': self.bias_ma20,
            'volume_status': self.volume_status.value,
            'volume_ratio_5d': self.volume_ratio_5d,
            'volume_trend': self.volume_trend,
            'volume_ratio_basis': self.volume_ratio_basis,
            'support_ma5': self.support_ma5,
            'support_ma10': self.support_ma10,
            'buy_signal': self.buy_signal.value,
            'signal_score': self.signal_score,
            'signal_reasons': self.signal_reasons,
            'risk_factors': self.risk_factors,
            'macd_dif': self.macd_dif,
            'macd_dea': self.macd_dea,
            'macd_bar': self.macd_bar,
            'macd_status': self.macd_status.value,
            'macd_signal': self.macd_signal,
            'rsi_6': self.rsi_6,
            'rsi_12': self.rsi_12,
            'rsi_24': self.rsi_24,
            'rsi_status': self.rsi_status.value,
            'rsi_signal': self.rsi_signal,
        }


class StockTrendAnalyzer:
    """Stock trend analyzer.

    Implements the user's trading philosophy:
    1. Trend - MA5>MA10>MA20 bullish alignment
    2. Bias check - never chase; do not buy when price is more than 5% above MA5
    3. Volume - prefer low-volume pullbacks
    4. Entry points - pullbacks to MA5/MA10 support
    5. MACD - trend confirmation plus golden/death cross signals
    6. RSI - overbought/oversold assessment
    """
    
    # Trading parameters (BIAS_THRESHOLD is read from Config, see _generate_signal)
    VOLUME_SHRINK_RATIO = 0.7   # Low-volume threshold (today's volume / 5-day average)
    VOLUME_HEAVY_RATIO = 1.5    # Heavy-volume threshold
    # Dưới mức này của phiên, quy đổi volume trong phiên ra cả ngày quá nhiễu để phân loại.
    # Below this session fraction, projecting intraday volume is too noisy to classify.
    MIN_SESSION_FRACTION_FOR_VOLUME = 0.2
    MA_SUPPORT_TOLERANCE = 0.02  # MA support tolerance (2%)

    # MACD parameters (standard 12/26/9)
    MACD_FAST = 12              # Fast line period
    MACD_SLOW = 26             # Slow line period
    MACD_SIGNAL = 9             # Signal line period

    # RSI parameters
    RSI_SHORT = 6               # Short-term RSI period
    RSI_MID = 12               # Medium-term RSI period
    RSI_LONG = 24              # Long-term RSI period
    RSI_OVERBOUGHT = 70        # Overbought threshold
    RSI_OVERSOLD = 30          # Oversold threshold
    
    def __init__(self):
        """Initialize the analyzer."""
        pass
    
    def analyze(
        self,
        df: pd.DataFrame,
        code: str,
        session_fraction: Optional[float] = None,
    ) -> TrendAnalysisResult:
        """Analyze a stock's trend.

        Args:
            df: DataFrame with OHLCV data
            code: Stock code
            session_fraction: phần phiên đã trôi qua (0..1) nếu nến cuối chưa đóng;
                ``None`` khi nến cuối là ngày đủ phiên. / Elapsed session fraction
                when the last bar is still forming.

        Returns:
            TrendAnalysisResult with the analysis
        """
        result = TrendAnalysisResult(code=code)
        
        if df is None or df.empty or len(df) < 20:
            logger.warning(f"{code}: insufficient data for trend analysis")
            result.risk_factors.append("Insufficient data to complete the analysis")
            return result
        
        # Make sure data is sorted by date
        df = df.sort_values('date').reset_index(drop=True)
        
        # Calculate moving averages
        df = self._calculate_mas(df)

        # Calculate MACD and RSI
        df = self._calculate_macd(df)
        df = self._calculate_rsi(df)

        # Take the latest row
        latest = df.iloc[-1]
        result.current_price = float(latest['close'])
        result.ma5 = float(latest['MA5'])
        result.ma10 = float(latest['MA10'])
        result.ma20 = float(latest['MA20'])
        result.ma60 = float(latest.get('MA60', 0))

        # 1. Trend assessment
        self._analyze_trend(df, result)

        # 2. Bias calculation
        self._calculate_bias(result)

        # 3. Volume analysis
        self._analyze_volume(df, result, session_fraction)

        # 4. Support/resistance analysis
        self._analyze_support_resistance(df, result)

        # 5. MACD analysis
        self._analyze_macd(df, result)

        # 6. RSI analysis
        self._analyze_rsi(df, result)

        # 7. Generate the buy signal
        self._generate_signal(result)

        return result
    
    def _calculate_mas(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate moving averages."""
        df = df.copy()
        df['MA5'] = df['close'].rolling(window=5).mean()
        df['MA10'] = df['close'].rolling(window=10).mean()
        df['MA20'] = df['close'].rolling(window=20).mean()
        if len(df) >= 60:
            df['MA60'] = df['close'].rolling(window=60).mean()
        else:
            df['MA60'] = df['MA20']  # Fall back to MA20 when there is not enough data
        return df

    def _calculate_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate the MACD indicator.

        Formulas:
        - EMA(12): 12-day exponential moving average
        - EMA(26): 26-day exponential moving average
        - DIF = EMA(12) - EMA(26)
        - DEA = EMA(DIF, 9)
        - MACD = (DIF - DEA) * 2
        """
        df = df.copy()

        # Calculate fast and slow EMAs
        ema_fast = df['close'].ewm(span=self.MACD_FAST, adjust=False).mean()
        ema_slow = df['close'].ewm(span=self.MACD_SLOW, adjust=False).mean()

        # Calculate the DIF fast line
        df['MACD_DIF'] = ema_fast - ema_slow

        # Calculate the DEA signal line
        df['MACD_DEA'] = df['MACD_DIF'].ewm(span=self.MACD_SIGNAL, adjust=False).mean()

        # Calculate the histogram
        df['MACD_BAR'] = (df['MACD_DIF'] - df['MACD_DEA']) * 2

        return df

    def _calculate_rsi(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate the RSI indicator (Wilder's EMA / SMMA convention).

        Formulas:
        - avg_gain / avg_loss use ewm(alpha=1/period, adjust=False)
        - RS = avg_gain / avg_loss
        - RSI = 100 - (100 / (1 + RS))
        """
        df = df.copy()

        for period in [self.RSI_SHORT, self.RSI_MID, self.RSI_LONG]:
            # Calculate price changes
            delta = df['close'].diff()

            # Split gains and losses
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)

            # Use Wilder's EMA / SMMA, consistent with common RSI charting tools.
            avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

            # Calculate RS and RSI
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

            # Fill NaN values
            rsi = rsi.fillna(50)  # Default to neutral

            # Add to the DataFrame
            col_name = f'RSI_{period}'
            df[col_name] = rsi

        return df
    
    def _analyze_trend(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """Analyze the trend status.

        Core logic: determine the MA alignment and trend strength.
        """
        ma5, ma10, ma20 = result.ma5, result.ma10, result.ma20
        
        # Determine the MA alignment
        if ma5 > ma10 > ma20:
            # Check whether the spread is widening (strong trend)
            prev = df.iloc[-5] if len(df) >= 5 else df.iloc[-1]
            prev_spread = (prev['MA5'] - prev['MA20']) / prev['MA20'] * 100 if prev['MA20'] > 0 else 0
            curr_spread = (ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0
            
            if curr_spread > prev_spread and curr_spread > 5:
                result.trend_status = TrendStatus.STRONG_BULL
                result.ma_alignment = "Strong bullish alignment, MAs diverging upward"
                result.trend_strength = 90
            else:
                result.trend_status = TrendStatus.BULL
                result.ma_alignment = "Bullish alignment MA5>MA10>MA20"
                result.trend_strength = 75
                
        elif ma5 > ma10 and ma10 <= ma20:
            result.trend_status = TrendStatus.WEAK_BULL
            result.ma_alignment = "Weak bullish, MA5>MA10 but MA10≤MA20"
            result.trend_strength = 55
            
        elif ma5 < ma10 < ma20:
            prev = df.iloc[-5] if len(df) >= 5 else df.iloc[-1]
            prev_spread = (prev['MA20'] - prev['MA5']) / prev['MA5'] * 100 if prev['MA5'] > 0 else 0
            curr_spread = (ma20 - ma5) / ma5 * 100 if ma5 > 0 else 0
            
            if curr_spread > prev_spread and curr_spread > 5:
                result.trend_status = TrendStatus.STRONG_BEAR
                result.ma_alignment = "Strong bearish alignment, MAs diverging downward"
                result.trend_strength = 10
            else:
                result.trend_status = TrendStatus.BEAR
                result.ma_alignment = "Bearish alignment MA5<MA10<MA20"
                result.trend_strength = 25
                
        elif ma5 < ma10 and ma10 >= ma20:
            result.trend_status = TrendStatus.WEAK_BEAR
            result.ma_alignment = "Weak bearish, MA5<MA10 but MA10≥MA20"
            result.trend_strength = 40
            
        else:
            result.trend_status = TrendStatus.CONSOLIDATION
            result.ma_alignment = "MAs intertwined, no clear trend"
            result.trend_strength = 50
    
    def _calculate_bias(self, result: TrendAnalysisResult) -> None:
        """Calculate the bias (deviation from moving averages).

        Bias = (price - MA) / MA * 100%

        Strict entry: do not chase when the bias exceeds 5%.
        """
        price = result.current_price
        
        if result.ma5 > 0:
            result.bias_ma5 = (price - result.ma5) / result.ma5 * 100
        if result.ma10 > 0:
            result.bias_ma10 = (price - result.ma10) / result.ma10 * 100
        if result.ma20 > 0:
            result.bias_ma20 = (price - result.ma20) / result.ma20 * 100
    
    def _analyze_volume(
        self,
        df: pd.DataFrame,
        result: TrendAnalysisResult,
        session_fraction: Optional[float] = None,
    ) -> None:
        """Analyze volume.

        Preference: low-volume pullback > rising on heavy volume > rising on low volume > falling on heavy volume
        """
        if len(df) < 5:
            return
        
        latest = df.iloc[-1]
        vol_5d_avg = df['volume'].iloc[-6:-1].mean()
        latest_volume = float(latest['volume'])

        # Nến trong phiên chỉ có volume tới hiện tại; so thẳng với ngày đủ phiên sẽ luôn ra
        # "thanh khoản thấp" giả (vd. 0.33 lúc 10:10). Quy đổi ra cả phiên trước khi so.
        # An intraday bar holds partial volume; project it to a full session first.
        if session_fraction is not None and 0 < session_fraction < 1:
            if session_fraction < self.MIN_SESSION_FRACTION_FOR_VOLUME:
                if vol_5d_avg > 0:
                    result.volume_ratio_5d = latest_volume / vol_5d_avg
                result.volume_ratio_basis = "intraday_too_early"
                result.volume_status = VolumeStatus.NORMAL
                result.volume_trend = "Too early in the session to compare volume with full days"
                return
            latest_volume = latest_volume / session_fraction
            result.volume_ratio_basis = "projected_intraday"

        if vol_5d_avg > 0:
            result.volume_ratio_5d = latest_volume / vol_5d_avg
        
        # Determine the price change
        prev_close = df.iloc[-2]['close']
        price_change = (latest['close'] - prev_close) / prev_close * 100
        
        # Determine the volume status
        if result.volume_ratio_5d >= self.VOLUME_HEAVY_RATIO:
            if price_change > 0:
                result.volume_status = VolumeStatus.HEAVY_VOLUME_UP
                result.volume_trend = "Rising on heavy volume, strong buying pressure"
            else:
                result.volume_status = VolumeStatus.HEAVY_VOLUME_DOWN
                result.volume_trend = "Falling on heavy volume, beware of risk"
        elif result.volume_ratio_5d <= self.VOLUME_SHRINK_RATIO:
            if price_change > 0:
                result.volume_status = VolumeStatus.SHRINK_VOLUME_UP
                result.volume_trend = "Rising on low volume, weak upward momentum"
            else:
                result.volume_status = VolumeStatus.SHRINK_VOLUME_DOWN
                result.volume_trend = "Low-volume pullback, clear shakeout pattern (positive)"
        else:
            result.volume_status = VolumeStatus.NORMAL
            result.volume_trend = "Normal volume"
    
    def _analyze_support_resistance(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """Analyze support and resistance levels.

        Entry preference: pullbacks that find support at MA5/MA10.
        """
        price = result.current_price
        
        # Check for support near MA5
        if result.ma5 > 0:
            ma5_distance = abs(price - result.ma5) / result.ma5
            if ma5_distance <= self.MA_SUPPORT_TOLERANCE and price >= result.ma5:
                result.support_ma5 = True
                result.support_levels.append(result.ma5)
        
        # Check for support near MA10
        if result.ma10 > 0:
            ma10_distance = abs(price - result.ma10) / result.ma10
            if ma10_distance <= self.MA_SUPPORT_TOLERANCE and price >= result.ma10:
                result.support_ma10 = True
                if result.ma10 not in result.support_levels:
                    result.support_levels.append(result.ma10)
        
        # MA20 as key support
        if result.ma20 > 0 and price >= result.ma20:
            result.support_levels.append(result.ma20)
        
        # Recent high as resistance
        if len(df) >= 20:
            recent_high = df['high'].iloc[-20:].max()
            if recent_high > price:
                result.resistance_levels.append(recent_high)

    def _analyze_macd(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """Analyze the MACD indicator.

        Key signals:
        - Golden cross above the zero axis: strongest buy signal
        - Golden cross: DIF crosses above DEA
        - Death cross: DIF crosses below DEA
        """
        if len(df) < self.MACD_SLOW:
            result.macd_signal = "Insufficient data"
            return

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        # Read MACD values
        result.macd_dif = float(latest['MACD_DIF'])
        result.macd_dea = float(latest['MACD_DEA'])
        result.macd_bar = float(latest['MACD_BAR'])

        # Detect golden/death crosses
        prev_dif_dea = prev['MACD_DIF'] - prev['MACD_DEA']
        curr_dif_dea = result.macd_dif - result.macd_dea

        # Golden cross: DIF crosses above DEA
        is_golden_cross = prev_dif_dea <= 0 and curr_dif_dea > 0

        # Death cross: DIF crosses below DEA
        is_death_cross = prev_dif_dea >= 0 and curr_dif_dea < 0

        # Zero-axis crossing
        prev_zero = prev['MACD_DIF']
        curr_zero = result.macd_dif
        is_crossing_up = prev_zero <= 0 and curr_zero > 0
        is_crossing_down = prev_zero >= 0 and curr_zero < 0

        # Determine the MACD status
        if is_golden_cross and curr_zero > 0:
            result.macd_status = MACDStatus.GOLDEN_CROSS_ZERO
            result.macd_signal = "⭐ Golden cross above the zero axis, strong buy signal!"
        elif is_crossing_up:
            result.macd_status = MACDStatus.CROSSING_UP
            result.macd_signal = "⚡ DIF crossed above zero, trend strengthening"
        elif is_golden_cross:
            result.macd_status = MACDStatus.GOLDEN_CROSS
            result.macd_signal = "✅ Golden cross, uptrend"
        elif is_death_cross:
            result.macd_status = MACDStatus.DEATH_CROSS
            result.macd_signal = "❌ Death cross, downtrend"
        elif is_crossing_down:
            result.macd_status = MACDStatus.CROSSING_DOWN
            result.macd_signal = "⚠️ DIF crossed below zero, trend weakening"
        elif result.macd_dif > 0 and result.macd_dea > 0:
            result.macd_status = MACDStatus.BULLISH
            result.macd_signal = "✓ Bullish alignment, rising steadily"
        elif result.macd_dif < 0 and result.macd_dea < 0:
            result.macd_status = MACDStatus.BEARISH
            result.macd_signal = "⚠ Bearish alignment, falling steadily"
        else:
            result.macd_status = MACDStatus.BULLISH
            result.macd_signal = " MACD neutral zone"

    def _analyze_rsi(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """Analyze the RSI indicator.

        Key thresholds:
        - RSI > 70: overbought, be careful chasing
        - RSI < 30: oversold, watch for a rebound
        - 40-60: neutral zone
        """
        if len(df) < self.RSI_LONG:
            result.rsi_signal = "Insufficient data"
            return

        latest = df.iloc[-1]

        # Read RSI values
        result.rsi_6 = float(latest[f'RSI_{self.RSI_SHORT}'])
        result.rsi_12 = float(latest[f'RSI_{self.RSI_MID}'])
        result.rsi_24 = float(latest[f'RSI_{self.RSI_LONG}'])

        # Use medium-term RSI(12) as the primary reading
        rsi_mid = result.rsi_12

        # Determine the RSI status
        if rsi_mid > self.RSI_OVERBOUGHT:
            result.rsi_status = RSIStatus.OVERBOUGHT
            result.rsi_signal = f"⚠️ RSI overbought ({rsi_mid:.1f}>70), high short-term pullback risk"
        elif rsi_mid > 60:
            result.rsi_status = RSIStatus.STRONG_BUY
            result.rsi_signal = f"✅ RSI strong ({rsi_mid:.1f}), buyers in control"
        elif rsi_mid >= 40:
            result.rsi_status = RSIStatus.NEUTRAL
            result.rsi_signal = f" RSI neutral ({rsi_mid:.1f}), range-bound consolidation"
        elif rsi_mid >= self.RSI_OVERSOLD:
            result.rsi_status = RSIStatus.WEAK
            result.rsi_signal = f"⚡ RSI weak ({rsi_mid:.1f}), watch for a rebound"
        else:
            result.rsi_status = RSIStatus.OVERSOLD
            result.rsi_signal = f"⭐ RSI oversold ({rsi_mid:.1f}<30), good rebound opportunity"

    def _generate_signal(self, result: TrendAnalysisResult) -> None:
        """Generate the buy signal.

        Composite scoring:
        - Trend (30 pts): bullish alignment scores high
        - Bias (20 pts): close to MA5 scores high
        - Volume (15 pts): low-volume pullback scores high
        - Support (10 pts): holding MA support scores high
        - MACD (15 pts): golden cross and bullish state score high
        - RSI (10 pts): oversold and strong states score high
        """
        score = 0
        reasons = []
        risks = []

        # === Trend score (30 pts) ===
        trend_scores = {
            TrendStatus.STRONG_BULL: 30,
            TrendStatus.BULL: 26,
            TrendStatus.WEAK_BULL: 18,
            TrendStatus.CONSOLIDATION: 12,
            TrendStatus.WEAK_BEAR: 8,
            TrendStatus.BEAR: 4,
            TrendStatus.STRONG_BEAR: 0,
        }
        trend_score = trend_scores.get(result.trend_status, 12)
        score += trend_score

        if result.trend_status in [TrendStatus.STRONG_BULL, TrendStatus.BULL]:
            reasons.append(f"✅ {result.trend_status.value}, go long with the trend")
        elif result.trend_status in [TrendStatus.BEAR, TrendStatus.STRONG_BEAR]:
            risks.append(f"⚠️ {result.trend_status.value}, avoid going long")

        # === Bias score (20 pts, with strong-trend compensation) ===
        bias = result.bias_ma5
        if bias != bias or bias is None:  # NaN or None defense
            bias = 0.0
        base_threshold = get_config().bias_threshold

        # Strong trend compensation: relax threshold for STRONG_BULL with high strength
        trend_strength = result.trend_strength if result.trend_strength == result.trend_strength else 0.0
        if result.trend_status == TrendStatus.STRONG_BULL and (trend_strength or 0) >= 70:
            effective_threshold = base_threshold * 1.5
            is_strong_trend = True
        else:
            effective_threshold = base_threshold
            is_strong_trend = False

        if bias < 0:
            # Price below MA5 (pullback)
            if bias > -3:
                score += 20
                reasons.append(f"✅ Price slightly below MA5 ({bias:.1f}%), pullback entry point")
            elif bias > -5:
                score += 16
                reasons.append(f"✅ Price pulling back to MA5 ({bias:.1f}%), watch support")
            else:
                score += 8
                risks.append(f"⚠️ Deviation too large ({bias:.1f}%), may break down")
        elif bias < 2:
            score += 18
            reasons.append(f"✅ Price close to MA5 ({bias:.1f}%), good entry timing")
        elif bias < base_threshold:
            score += 14
            reasons.append(f"⚡ Price slightly above MA5 ({bias:.1f}%), small position possible")
        elif bias > effective_threshold:
            score += 4
            risks.append(
                f"❌ Deviation too high ({bias:.1f}%>{effective_threshold:.1f}%), do not chase!"
            )
        elif bias > base_threshold and is_strong_trend:
            score += 10
            reasons.append(
                f"⚡ Elevated deviation in a strong trend ({bias:.1f}%), follow with a light position"
            )
        else:
            score += 4
            risks.append(
                f"❌ Deviation too high ({bias:.1f}%>{base_threshold:.1f}%), do not chase!"
            )

        # === Volume score (15 pts) ===
        volume_scores = {
            VolumeStatus.SHRINK_VOLUME_DOWN: 15,  # Low-volume pullback is best
            VolumeStatus.HEAVY_VOLUME_UP: 12,     # Rising on heavy volume is next
            VolumeStatus.NORMAL: 10,
            VolumeStatus.SHRINK_VOLUME_UP: 6,     # Rising on thin volume is weaker
            VolumeStatus.HEAVY_VOLUME_DOWN: 0,    # Falling on heavy volume is worst
        }
        vol_score = volume_scores.get(result.volume_status, 8)
        score += vol_score

        if result.volume_status == VolumeStatus.SHRINK_VOLUME_DOWN:
            reasons.append("✅ Low-volume pullback, major players shaking out")
        elif result.volume_status == VolumeStatus.HEAVY_VOLUME_DOWN:
            risks.append("⚠️ Falling on heavy volume, beware of risk")

        # === Support score (10 pts) ===
        if result.support_ma5:
            score += 5
            reasons.append("✅ MA5 support holding")
        if result.support_ma10:
            score += 5
            reasons.append("✅ MA10 support holding")

        # === MACD score (15 pts) ===
        macd_scores = {
            MACDStatus.GOLDEN_CROSS_ZERO: 15,  # Golden cross above zero is strongest
            MACDStatus.GOLDEN_CROSS: 12,      # Golden cross
            MACDStatus.CROSSING_UP: 10,       # Crossing above zero
            MACDStatus.BULLISH: 8,            # Bullish
            MACDStatus.BEARISH: 2,            # Bearish
            MACDStatus.CROSSING_DOWN: 0,       # Crossing below zero
            MACDStatus.DEATH_CROSS: 0,        # Death cross
        }
        macd_score = macd_scores.get(result.macd_status, 5)
        score += macd_score

        if result.macd_status in [MACDStatus.GOLDEN_CROSS_ZERO, MACDStatus.GOLDEN_CROSS]:
            reasons.append(f"✅ {result.macd_signal}")
        elif result.macd_status in [MACDStatus.DEATH_CROSS, MACDStatus.CROSSING_DOWN]:
            risks.append(f"⚠️ {result.macd_signal}")
        else:
            reasons.append(result.macd_signal)

        # === RSI score (10 pts) ===
        rsi_scores = {
            RSIStatus.OVERSOLD: 10,       # Oversold is best
            RSIStatus.STRONG_BUY: 8,     # Strong
            RSIStatus.NEUTRAL: 5,        # Neutral
            RSIStatus.WEAK: 3,            # Weak
            RSIStatus.OVERBOUGHT: 0,       # Overbought is worst
        }
        rsi_score = rsi_scores.get(result.rsi_status, 5)
        score += rsi_score

        if result.rsi_status in [RSIStatus.OVERSOLD, RSIStatus.STRONG_BUY]:
            reasons.append(f"✅ {result.rsi_signal}")
        elif result.rsi_status == RSIStatus.OVERBOUGHT:
            risks.append(f"⚠️ {result.rsi_signal}")
        else:
            reasons.append(result.rsi_signal)

        # === Overall verdict ===
        result.signal_score = score
        result.signal_reasons = reasons
        result.risk_factors = risks

        # Generate the buy signal (consistent with the canonical decision scale)
        score_signal = signal_key_for_score(score)
        if score_signal == "strong_buy" and result.trend_status in [TrendStatus.STRONG_BULL, TrendStatus.BULL]:
            result.buy_signal = BuySignal.STRONG_BUY
        elif score_signal in {"strong_buy", "buy"} and result.trend_status in [
            TrendStatus.STRONG_BULL,
            TrendStatus.BULL,
            TrendStatus.WEAK_BULL,
        ]:
            result.buy_signal = BuySignal.BUY
        elif score_signal in {"strong_buy", "buy"} and result.trend_status in [
            TrendStatus.CONSOLIDATION,
            TrendStatus.WEAK_BEAR,
        ]:
            result.buy_signal = BuySignal.WAIT
        elif score_signal == "watch":
            result.buy_signal = BuySignal.WAIT
        elif score_signal == "sell" or result.trend_status in [TrendStatus.BEAR, TrendStatus.STRONG_BEAR]:
            result.buy_signal = BuySignal.STRONG_SELL
        else:
            result.buy_signal = BuySignal.SELL
    
    def format_analysis(self, result: TrendAnalysisResult) -> str:
        """Format the analysis result as text.

        Args:
            result: Analysis result

        Returns:
            Formatted analysis text
        """
        lines = [
            f"=== {result.code} Trend Analysis ===",
            f"",
            f"📊 Trend: {result.trend_status.value}",
            f"   MA alignment: {result.ma_alignment}",
            f"   Trend strength: {result.trend_strength}/100",
            f"",
            f"📈 Moving averages:",
            f"   Price: {result.current_price:.2f}",
            f"   MA5:  {result.ma5:.2f} (bias {result.bias_ma5:+.2f}%)",
            f"   MA10: {result.ma10:.2f} (bias {result.bias_ma10:+.2f}%)",
            f"   MA20: {result.ma20:.2f} (bias {result.bias_ma20:+.2f}%)",
            f"",
            f"📊 Volume: {result.volume_status.value}",
            f"   Volume ratio (vs 5d): {result.volume_ratio_5d:.2f}",
            f"   Volume trend: {result.volume_trend}",
            f"",
            f"📈 MACD: {result.macd_status.value}",
            f"   DIF: {result.macd_dif:.4f}",
            f"   DEA: {result.macd_dea:.4f}",
            f"   MACD: {result.macd_bar:.4f}",
            f"   Signal: {result.macd_signal}",
            f"",
            f"📊 RSI: {result.rsi_status.value}",
            f"   RSI(6): {result.rsi_6:.1f}",
            f"   RSI(12): {result.rsi_12:.1f}",
            f"   RSI(24): {result.rsi_24:.1f}",
            f"   Signal: {result.rsi_signal}",
            f"",
            f"🎯 Action: {result.buy_signal.value}",
            f"   Overall score: {result.signal_score}/100",
        ]

        if result.signal_reasons:
            lines.append(f"")
            lines.append(f"✅ Reasons:")
            for reason in result.signal_reasons:
                lines.append(f"   {reason}")

        if result.risk_factors:
            lines.append(f"")
            lines.append(f"⚠️ Risk factors:")
            for risk in result.risk_factors:
                lines.append(f"   {risk}")

        return "\n".join(lines)


def analyze_stock(df: pd.DataFrame, code: str) -> TrendAnalysisResult:
    """Convenience function: analyze a single stock.

    Args:
        df: DataFrame with OHLCV data
        code: Stock code

    Returns:
        TrendAnalysisResult with the analysis
    """
    analyzer = StockTrendAnalyzer()
    return analyzer.analyze(df, code)


if __name__ == "__main__":
    # Test code
    logging.basicConfig(level=logging.INFO)
    
    # Simulated data test
    import numpy as np
    
    dates = pd.date_range(start='2025-01-01', periods=60, freq='D')
    np.random.seed(42)
    
    # Simulate a bullish-alignment series
    base_price = 10.0
    prices = [base_price]
    for i in range(59):
        change = np.random.randn() * 0.02 + 0.003  # Mild upward drift
        prices.append(prices[-1] * (1 + change))
    
    df = pd.DataFrame({
        'date': dates,
        'open': prices,
        'high': [p * (1 + np.random.uniform(0, 0.02)) for p in prices],
        'low': [p * (1 - np.random.uniform(0, 0.02)) for p in prices],
        'close': prices,
        'volume': [np.random.randint(1000000, 5000000) for _ in prices],
    })
    
    analyzer = StockTrendAnalyzer()
    result = analyzer.analyze(df, '000001')
    print(analyzer.format_analysis(result))
