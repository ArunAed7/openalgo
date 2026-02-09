"""
Autonomous Options Buying System for OpenAlgo
==============================================

A fully autonomous intraday options buying bot that:
- Generates BUY signals using RSI + EMA crossover + VWAP on the underlying index
- Buys ATM/OTM Call or Put options based on directional signals
- Monitors positions via WebSocket for stop loss, target, and trailing stop loss
- Auto square-off before market close
- Enforces daily loss limits and max trade caps
- Runs on a scheduler with market-aware timing

Usage:
    1. Set your API key, host, and configuration in the CONFIG section below
    2. Run: uv run python examples/python/autonomous_options_buyer.py
    3. The bot will wait for market open and start trading autonomously

Supports: NIFTY, BANKNIFTY, FINNIFTY, SENSEX (any F&O underlying)
"""

import logging
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd
import pytz
from openalgo import api

# ============================================================================
# CONFIGURATION
# ============================================================================

CONFIG = {
    # --- OpenAlgo Connection ---
    "API_KEY": "your-openalgo-api-key",
    "HOST": "http://127.0.0.1:5000",
    "WS_URL": "ws://127.0.0.1:8765",

    # --- Underlying & Exchange ---
    "UNDERLYING": "NIFTY",          # NIFTY, BANKNIFTY, FINNIFTY, SENSEX
    "EXCHANGE": "NSE_INDEX",        # NSE_INDEX for indices
    "OPTIONS_EXCHANGE": "NFO",      # NFO for NSE F&O, BFO for BSE F&O
    "LOT_SIZE": 75,                 # NIFTY=75, BANKNIFTY=15, FINNIFTY=65

    # --- Strategy Settings ---
    "STRATEGY_NAME": "AutoOptionsBuyer",
    "LOTS": 1,                      # Number of lots per trade
    "PRODUCT": "MIS",               # MIS (intraday) or NRML (carry forward)
    "OPTION_OFFSET": "ATM",         # ATM, ITM1, ITM2, OTM1, OTM2, etc.

    # --- Signal Parameters ---
    "CANDLE_INTERVAL": "3m",        # Candle timeframe for signal generation
    "EMA_FAST": 9,                  # Fast EMA period
    "EMA_SLOW": 21,                 # Slow EMA period
    "RSI_PERIOD": 14,               # RSI lookback period
    "RSI_OVERBOUGHT": 65,          # RSI level to trigger CE buy
    "RSI_OVERSOLD": 35,            # RSI level to trigger PE buy
    "VWAP_ENABLED": True,          # Use VWAP confirmation
    "LOOKBACK_DAYS": 5,             # Days of history to fetch

    # --- Risk Management ---
    "STOP_LOSS_PCT": 20.0,          # Stop loss as % of option premium
    "TARGET_PCT": 40.0,             # Target as % of option premium
    "TRAILING_SL_PCT": 10.0,        # Trailing SL as % from peak (0 to disable)
    "MAX_LOSS_PER_DAY": 5000.0,     # Max loss in INR per day (0 to disable)
    "MAX_TRADES_PER_DAY": 4,        # Max number of trades per day
    "RE_ENTRY_COOLDOWN_SEC": 300,   # Seconds to wait after exit before re-entry

    # --- Market Timing ---
    "ENTRY_START_TIME": "09:20",    # Earliest entry time (HH:MM IST)
    "ENTRY_END_TIME": "14:45",      # Latest entry time (HH:MM IST)
    "SQUARE_OFF_TIME": "15:15",     # Force square-off time (HH:MM IST)
    "POLL_INTERVAL_SEC": 15,        # Seconds between signal checks
}

# ============================================================================
# LOGGING SETUP
# ============================================================================

IST = pytz.timezone("Asia/Kolkata")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            f"auto_options_{datetime.now(IST).strftime('%Y%m%d')}.log"
        ),
    ],
)
log = logging.getLogger("AutoOptionsBuyer")


# ============================================================================
# DATA CLASSES
# ============================================================================

class Direction(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass
class Signal:
    direction: Direction
    strength: float          # 0.0 to 1.0
    ema_fast: float
    ema_slow: float
    rsi: float
    vwap: Optional[float]
    ltp: float
    timestamp: datetime


@dataclass
class Position:
    symbol: str              # Full option symbol e.g. NIFTY30DEC2526000CE
    option_type: str         # CE or PE
    order_id: str
    entry_price: float
    quantity: int
    stop_loss: float
    target: float
    peak_price: float        # For trailing SL
    trailing_sl: float       # Current trailing SL level
    entry_time: datetime = field(default_factory=lambda: datetime.now(IST))


@dataclass
class DailyStats:
    date: str
    trades_taken: int = 0
    realized_pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    last_exit_time: Optional[datetime] = None


# ============================================================================
# TECHNICAL INDICATORS
# ============================================================================

def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def calculate_rsi(series: pd.Series, period: int) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """Volume Weighted Average Price (intraday reset)."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cum_tp_vol = (typical_price * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum()
    vwap = cum_tp_vol / cum_vol
    return vwap


# ============================================================================
# SIGNAL ENGINE
# ============================================================================

class SignalEngine:
    """Generates directional signals from underlying index data."""

    def __init__(self, client: api, config: dict):
        self.client = client
        self.cfg = config

    def fetch_data(self) -> pd.DataFrame:
        """Fetch historical candle data for the underlying."""
        end_date = datetime.now(IST).strftime("%Y-%m-%d")
        start_date = (
            datetime.now(IST) - timedelta(days=self.cfg["LOOKBACK_DAYS"])
        ).strftime("%Y-%m-%d")

        df = self.client.history(
            symbol=self.cfg["UNDERLYING"],
            exchange=self.cfg["EXCHANGE"],
            interval=self.cfg["CANDLE_INTERVAL"],
            start_date=start_date,
            end_date=end_date,
        )
        return df

    def generate_signal(self) -> Signal:
        """Compute indicators and return a trading signal."""
        df = self.fetch_data()

        if df is None or df.empty or len(df) < self.cfg["EMA_SLOW"] + 5:
            log.warning("Insufficient data for signal generation")
            return Signal(
                direction=Direction.NEUTRAL, strength=0.0,
                ema_fast=0, ema_slow=0, rsi=50, vwap=None,
                ltp=0, timestamp=datetime.now(IST),
            )

        close = df["close"]

        # Calculate indicators
        ema_fast = calculate_ema(close, self.cfg["EMA_FAST"])
        ema_slow = calculate_ema(close, self.cfg["EMA_SLOW"])
        rsi = calculate_rsi(close, self.cfg["RSI_PERIOD"])

        vwap = None
        if self.cfg["VWAP_ENABLED"] and "volume" in df.columns:
            vwap_series = calculate_vwap(df)
            vwap = float(vwap_series.iloc[-2])

        # Use completed candle values (iloc[-2]) to avoid partial candle
        curr_fast = float(ema_fast.iloc[-2])
        curr_slow = float(ema_slow.iloc[-2])
        prev_fast = float(ema_fast.iloc[-3])
        prev_slow = float(ema_slow.iloc[-3])
        curr_rsi = float(rsi.iloc[-2])
        ltp = float(close.iloc[-1])

        # --- Signal Logic ---
        direction = Direction.NEUTRAL
        strength = 0.0

        # Bullish: EMA crossover + RSI confirmation
        ema_bullish_cross = (prev_fast <= prev_slow) and (curr_fast > curr_slow)
        ema_bullish_trend = curr_fast > curr_slow
        rsi_bullish = curr_rsi > self.cfg["RSI_OVERBOUGHT"]
        vwap_bullish = (vwap is None) or (ltp > vwap)

        # Bearish: EMA crossunder + RSI confirmation
        ema_bearish_cross = (prev_fast >= prev_slow) and (curr_fast < curr_slow)
        ema_bearish_trend = curr_fast < curr_slow
        rsi_bearish = curr_rsi < self.cfg["RSI_OVERSOLD"]
        vwap_bearish = (vwap is None) or (ltp < vwap)

        # Strong bullish: fresh crossover + RSI momentum + VWAP above
        if ema_bullish_cross and rsi_bullish and vwap_bullish:
            direction = Direction.BULLISH
            strength = 1.0
        # Moderate bullish: trend + RSI momentum + VWAP
        elif ema_bullish_trend and rsi_bullish and vwap_bullish:
            direction = Direction.BULLISH
            strength = 0.7
        # Strong bearish: fresh crossunder + RSI weakness + VWAP below
        elif ema_bearish_cross and rsi_bearish and vwap_bearish:
            direction = Direction.BEARISH
            strength = 1.0
        # Moderate bearish: trend + RSI weakness + VWAP
        elif ema_bearish_trend and rsi_bearish and vwap_bearish:
            direction = Direction.BEARISH
            strength = 0.7

        sig = Signal(
            direction=direction,
            strength=strength,
            ema_fast=curr_fast,
            ema_slow=curr_slow,
            rsi=curr_rsi,
            vwap=vwap,
            ltp=ltp,
            timestamp=datetime.now(IST),
        )

        log.info(
            "Signal: %s (%.1f) | LTP=%.2f EMA_F=%.2f EMA_S=%.2f RSI=%.1f VWAP=%s",
            sig.direction.value, sig.strength, sig.ltp,
            sig.ema_fast, sig.ema_slow, sig.rsi,
            f"{sig.vwap:.2f}" if sig.vwap else "N/A",
        )

        return sig


# ============================================================================
# OPTIONS TRADER (CORE ENGINE)
# ============================================================================

class AutonomousOptionsTrader:
    """
    Main trading engine that:
    1. Polls for signals on the underlying
    2. Places ATM/OTM option buy orders
    3. Monitors via WebSocket for SL/Target/Trailing SL
    4. Enforces daily risk limits
    5. Auto square-off before market close
    """

    def __init__(self, config: dict):
        self.cfg = config
        self.client = api(
            api_key=config["API_KEY"],
            host=config["HOST"],
            ws_url=config["WS_URL"],
        )
        self.signal_engine = SignalEngine(self.client, config)

        self.position: Optional[Position] = None
        self.daily_stats = DailyStats(
            date=datetime.now(IST).strftime("%Y-%m-%d")
        )
        self.ws_connected = False
        self.running = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Time Helpers
    # ------------------------------------------------------------------

    def _now(self) -> datetime:
        return datetime.now(IST)

    def _time_str_to_today(self, time_str: str) -> datetime:
        """Convert 'HH:MM' to today's datetime in IST."""
        h, m = map(int, time_str.split(":"))
        return self._now().replace(hour=h, minute=m, second=0, microsecond=0)

    def _is_entry_window(self) -> bool:
        now = self._now()
        start = self._time_str_to_today(self.cfg["ENTRY_START_TIME"])
        end = self._time_str_to_today(self.cfg["ENTRY_END_TIME"])
        return start <= now <= end

    def _is_square_off_time(self) -> bool:
        return self._now() >= self._time_str_to_today(self.cfg["SQUARE_OFF_TIME"])

    def _is_market_day(self) -> bool:
        """Basic weekday check (Mon-Fri). For holidays, use client.holidays()."""
        return self._now().weekday() < 5

    # ------------------------------------------------------------------
    # Daily Stats
    # ------------------------------------------------------------------

    def _reset_daily_stats_if_needed(self):
        today = self._now().strftime("%Y-%m-%d")
        if self.daily_stats.date != today:
            log.info("New trading day detected. Resetting daily stats.")
            self.daily_stats = DailyStats(date=today)

    def _can_trade(self) -> bool:
        """Check if daily limits allow another trade."""
        stats = self.daily_stats

        if self.cfg["MAX_TRADES_PER_DAY"] > 0:
            if stats.trades_taken >= self.cfg["MAX_TRADES_PER_DAY"]:
                log.info("Max trades per day reached (%d)", stats.trades_taken)
                return False

        if self.cfg["MAX_LOSS_PER_DAY"] > 0:
            if stats.realized_pnl <= -self.cfg["MAX_LOSS_PER_DAY"]:
                log.info(
                    "Max daily loss reached (PnL=%.2f)", stats.realized_pnl
                )
                return False

        # Check cooldown after last exit
        if stats.last_exit_time:
            elapsed = (self._now() - stats.last_exit_time).total_seconds()
            if elapsed < self.cfg["RE_ENTRY_COOLDOWN_SEC"]:
                log.debug(
                    "Re-entry cooldown: %.0fs remaining",
                    self.cfg["RE_ENTRY_COOLDOWN_SEC"] - elapsed,
                )
                return False

        return True

    # ------------------------------------------------------------------
    # Get Nearest Expiry
    # ------------------------------------------------------------------

    def _get_nearest_expiry(self) -> Optional[str]:
        """Fetch the nearest weekly expiry date in DDMMMYY format."""
        try:
            result = self.client.expiry(
                symbol=self.cfg["UNDERLYING"],
                exchange=self.cfg["OPTIONS_EXCHANGE"],
                instrumenttype="options",
            )
            if result.get("status") == "success" and result.get("data"):
                # Expiry dates come as 'DD-MMM-YY' like '10-JUL-25'
                # Convert to DDMMMYY format like '10JUL25'
                nearest = result["data"][0]
                return nearest.replace("-", "")
            log.error("Failed to fetch expiry dates: %s", result)
        except Exception as e:
            log.error("Error fetching expiry: %s", e)
        return None

    # ------------------------------------------------------------------
    # Entry Logic
    # ------------------------------------------------------------------

    def _place_entry(self, signal: Signal):
        """Place an option buy order based on the signal direction."""
        if self.position is not None:
            log.debug("Already in position, skipping entry")
            return

        if not self._can_trade():
            return

        option_type = "CE" if signal.direction == Direction.BULLISH else "PE"
        quantity = self.cfg["LOTS"] * self.cfg["LOT_SIZE"]

        # Get nearest expiry
        expiry = self._get_nearest_expiry()
        if not expiry:
            log.error("Could not determine expiry date, skipping entry")
            return

        log.info(
            "ENTRY SIGNAL: %s %s | Strength=%.1f | Expiry=%s | Qty=%d",
            option_type, self.cfg["OPTION_OFFSET"],
            signal.strength, expiry, quantity,
        )

        try:
            response = self.client.optionsorder(
                strategy=self.cfg["STRATEGY_NAME"],
                underlying=self.cfg["UNDERLYING"],
                exchange=self.cfg["EXCHANGE"],
                expiry_date=expiry,
                offset=self.cfg["OPTION_OFFSET"],
                option_type=option_type,
                action="BUY",
                quantity=quantity,
                pricetype="MARKET",
                product=self.cfg["PRODUCT"],
            )

            log.info("Order response: %s", response)

            if response.get("status") != "success":
                log.error("Order placement failed: %s", response)
                return

            order_id = response.get("orderid", "")
            symbol = response.get("symbol", "")

            # Wait for execution and fetch fill price
            time.sleep(2)
            entry_price = self._get_fill_price(order_id)
            if entry_price <= 0:
                log.error(
                    "Could not get fill price for order %s. "
                    "Position may exist but is untracked.",
                    order_id,
                )
                return

            # Calculate SL and target
            sl_amount = entry_price * (self.cfg["STOP_LOSS_PCT"] / 100)
            target_amount = entry_price * (self.cfg["TARGET_PCT"] / 100)
            stop_loss = round(entry_price - sl_amount, 2)
            target = round(entry_price + target_amount, 2)

            with self._lock:
                self.position = Position(
                    symbol=symbol,
                    option_type=option_type,
                    order_id=order_id,
                    entry_price=entry_price,
                    quantity=quantity,
                    stop_loss=max(stop_loss, 0.05),
                    target=target,
                    peak_price=entry_price,
                    trailing_sl=max(stop_loss, 0.05),
                )

            self.daily_stats.trades_taken += 1

            log.info(
                "POSITION OPENED: %s @ %.2f | SL=%.2f | Target=%.2f | Qty=%d",
                symbol, entry_price, self.position.stop_loss,
                target, quantity,
            )

            # Start WebSocket monitoring
            self._start_ws_monitoring()

        except Exception as e:
            log.error("Error placing entry order: %s", e, exc_info=True)

    def _get_fill_price(self, order_id: str) -> float:
        """Poll order status to get average fill price."""
        for attempt in range(10):
            try:
                status_resp = self.client.orderstatus(
                    order_id=order_id,
                    strategy=self.cfg["STRATEGY_NAME"],
                )
                data = status_resp.get("data", {})
                order_status = str(data.get("order_status", "")).lower()

                if order_status == "complete":
                    price = float(data.get("average_price", 0))
                    if price > 0:
                        return price
                elif order_status == "rejected":
                    log.error("Order %s was rejected", order_id)
                    return 0

            except Exception as e:
                log.warning("Error checking order status (attempt %d): %s", attempt + 1, e)

            time.sleep(1)

        log.error("Order %s did not complete within timeout", order_id)
        return 0

    # ------------------------------------------------------------------
    # WebSocket Monitoring (SL / Target / Trailing SL)
    # ------------------------------------------------------------------

    def _start_ws_monitoring(self):
        """Subscribe to LTP updates for the option position."""
        if self.position is None:
            return

        try:
            if not self.ws_connected:
                self.client.connect()
                self.ws_connected = True
                time.sleep(1)

            instruments = [{
                "exchange": self.cfg["OPTIONS_EXCHANGE"],
                "symbol": self.position.symbol,
            }]
            self.client.subscribe_ltp(instruments, on_data_received=self._on_ltp)
            log.info("WebSocket monitoring started for %s", self.position.symbol)

        except Exception as e:
            log.error("WebSocket connection error: %s", e)

    def _stop_ws_monitoring(self):
        """Unsubscribe from LTP updates."""
        if self.position is None:
            return

        try:
            instruments = [{
                "exchange": self.cfg["OPTIONS_EXCHANGE"],
                "symbol": self.position.symbol,
            }]
            self.client.unsubscribe_ltp(instruments)
            log.info("WebSocket monitoring stopped for %s", self.position.symbol)
        except Exception as e:
            log.warning("Error unsubscribing WebSocket: %s", e)

    def _on_ltp(self, data):
        """WebSocket callback: check SL, target, and trailing SL."""
        with self._lock:
            if self.position is None:
                return

            try:
                ltp = float(data["data"]["ltp"])
            except (KeyError, ValueError, TypeError):
                return

            pos = self.position

            # Update peak price for trailing SL
            if ltp > pos.peak_price:
                pos.peak_price = ltp
                # Recalculate trailing SL
                if self.cfg["TRAILING_SL_PCT"] > 0:
                    trail_amount = pos.peak_price * (self.cfg["TRAILING_SL_PCT"] / 100)
                    new_trailing_sl = round(pos.peak_price - trail_amount, 2)
                    if new_trailing_sl > pos.trailing_sl:
                        pos.trailing_sl = new_trailing_sl
                        log.info(
                            "Trailing SL updated: %.2f (peak=%.2f)",
                            pos.trailing_sl, pos.peak_price,
                        )

            # Determine effective SL (higher of fixed SL and trailing SL)
            effective_sl = max(pos.stop_loss, pos.trailing_sl)

            # Check stop loss
            if ltp <= effective_sl:
                pnl = (ltp - pos.entry_price) * pos.quantity
                log.info(
                    "STOP LOSS HIT: %s LTP=%.2f SL=%.2f | PnL=%.2f",
                    pos.symbol, ltp, effective_sl, pnl,
                )
                # Release lock before exit to avoid deadlock
                self._lock.release()
                try:
                    self._exit_position("STOP_LOSS", ltp)
                finally:
                    self._lock.acquire()
                return

            # Check target
            if ltp >= pos.target:
                pnl = (ltp - pos.entry_price) * pos.quantity
                log.info(
                    "TARGET HIT: %s LTP=%.2f Target=%.2f | PnL=%.2f",
                    pos.symbol, ltp, pos.target, pnl,
                )
                self._lock.release()
                try:
                    self._exit_position("TARGET", ltp)
                finally:
                    self._lock.acquire()
                return

    # ------------------------------------------------------------------
    # Exit Logic
    # ------------------------------------------------------------------

    def _exit_position(self, reason: str, exit_price: float = 0):
        """Place a sell order to exit the position."""
        with self._lock:
            if self.position is None:
                return
            pos_symbol = self.position.symbol
            pos_qty = self.position.quantity
            pos_entry = self.position.entry_price
            pos_otype = self.position.option_type

        log.info("EXIT [%s]: %s | Qty=%d", reason, pos_symbol, pos_qty)

        try:
            self._stop_ws_monitoring()

            response = self.client.placeorder(
                strategy=self.cfg["STRATEGY_NAME"],
                symbol=pos_symbol,
                action="SELL",
                exchange=self.cfg["OPTIONS_EXCHANGE"],
                price_type="MARKET",
                product=self.cfg["PRODUCT"],
                quantity=pos_qty,
            )
            log.info("Exit order response: %s", response)

            # Calculate PnL
            if exit_price > 0:
                pnl = (exit_price - pos_entry) * pos_qty
            else:
                # Estimate from order fill
                if response.get("status") == "success":
                    time.sleep(1)
                    fill = self._get_fill_price(response.get("orderid", ""))
                    pnl = (fill - pos_entry) * pos_qty if fill > 0 else 0
                else:
                    pnl = 0

            # Update daily stats
            self.daily_stats.realized_pnl += pnl
            self.daily_stats.last_exit_time = self._now()
            if pnl >= 0:
                self.daily_stats.wins += 1
            else:
                self.daily_stats.losses += 1

            log.info(
                "TRADE CLOSED: %s %s @ entry=%.2f exit=%.2f | PnL=%.2f | "
                "Day PnL=%.2f | W/L=%d/%d",
                pos_otype, pos_symbol, pos_entry, exit_price,
                pnl, self.daily_stats.realized_pnl,
                self.daily_stats.wins, self.daily_stats.losses,
            )

        except Exception as e:
            log.error("Error placing exit order: %s", e, exc_info=True)

        with self._lock:
            self.position = None

    def _force_square_off(self):
        """Force close any open position (market close)."""
        if self.position is None:
            return

        log.info("FORCE SQUARE-OFF triggered at %s", self._now().strftime("%H:%M:%S"))

        # Get current LTP for PnL calculation
        try:
            quote = self.client.quotes(
                symbol=self.position.symbol,
                exchange=self.cfg["OPTIONS_EXCHANGE"],
            )
            ltp = float(quote.get("data", {}).get("ltp", 0))
        except Exception:
            ltp = 0

        self._exit_position("SQUARE_OFF", ltp)

    # ------------------------------------------------------------------
    # Main Trading Loop
    # ------------------------------------------------------------------

    def run(self):
        """Main loop: poll signals and manage positions."""
        self.running = True
        log.info("=" * 60)
        log.info("Autonomous Options Buyer STARTED")
        log.info("Underlying: %s | Lots: %d | Offset: %s",
                 self.cfg["UNDERLYING"], self.cfg["LOTS"],
                 self.cfg["OPTION_OFFSET"])
        log.info("SL: %.1f%% | Target: %.1f%% | Trailing SL: %.1f%%",
                 self.cfg["STOP_LOSS_PCT"], self.cfg["TARGET_PCT"],
                 self.cfg["TRAILING_SL_PCT"])
        log.info("Entry Window: %s - %s | Square-off: %s",
                 self.cfg["ENTRY_START_TIME"], self.cfg["ENTRY_END_TIME"],
                 self.cfg["SQUARE_OFF_TIME"])
        log.info("Max Trades/Day: %d | Max Loss/Day: %.0f",
                 self.cfg["MAX_TRADES_PER_DAY"], self.cfg["MAX_LOSS_PER_DAY"])
        log.info("=" * 60)

        while self.running:
            try:
                self._reset_daily_stats_if_needed()

                # Skip non-market days
                if not self._is_market_day():
                    log.debug("Non-market day. Sleeping...")
                    time.sleep(60)
                    continue

                now = self._now()

                # Before market open - wait
                entry_start = self._time_str_to_today(self.cfg["ENTRY_START_TIME"])
                if now < entry_start:
                    wait = (entry_start - now).total_seconds()
                    if wait > 120:
                        log.info(
                            "Waiting for market entry window (%s). "
                            "Sleeping %.0f seconds...",
                            self.cfg["ENTRY_START_TIME"], min(wait, 60),
                        )
                        time.sleep(min(wait, 60))
                    else:
                        time.sleep(wait)
                    continue

                # Check square-off time
                if self._is_square_off_time():
                    self._force_square_off()
                    # Wait until next day
                    log.info(
                        "Square-off done. Day stats: PnL=%.2f W=%d L=%d Trades=%d",
                        self.daily_stats.realized_pnl, self.daily_stats.wins,
                        self.daily_stats.losses, self.daily_stats.trades_taken,
                    )
                    # Sleep until next day 9:00 AM
                    tomorrow_9am = (
                        self._now().replace(hour=9, minute=0, second=0)
                        + timedelta(days=1)
                    )
                    sleep_secs = (tomorrow_9am - self._now()).total_seconds()
                    log.info("Sleeping until tomorrow 09:00 AM (%.0f seconds)", sleep_secs)
                    time.sleep(max(sleep_secs, 60))
                    continue

                # If no position, look for entry signals
                if self.position is None and self._is_entry_window():
                    signal = self.signal_engine.generate_signal()
                    if signal.direction != Direction.NEUTRAL and signal.strength >= 0.7:
                        self._place_entry(signal)

                # If in position, log status periodically
                elif self.position is not None:
                    try:
                        quote = self.client.quotes(
                            symbol=self.position.symbol,
                            exchange=self.cfg["OPTIONS_EXCHANGE"],
                        )
                        ltp = float(quote.get("data", {}).get("ltp", 0))
                        unrealized = (ltp - self.position.entry_price) * self.position.quantity
                        effective_sl = max(self.position.stop_loss, self.position.trailing_sl)
                        log.info(
                            "POSITION: %s LTP=%.2f Entry=%.2f SL=%.2f "
                            "Target=%.2f Trail=%.2f | Unrealized=%.2f",
                            self.position.symbol, ltp, self.position.entry_price,
                            effective_sl, self.position.target,
                            self.position.trailing_sl, unrealized,
                        )
                    except Exception:
                        pass

                time.sleep(self.cfg["POLL_INTERVAL_SEC"])

            except KeyboardInterrupt:
                log.info("Keyboard interrupt received. Shutting down...")
                self.stop()
                break

            except Exception as e:
                log.error("Unexpected error in main loop: %s", e, exc_info=True)
                time.sleep(self.cfg["POLL_INTERVAL_SEC"])

    def stop(self):
        """Graceful shutdown."""
        log.info("Stopping Autonomous Options Buyer...")
        self.running = False

        # Square off any open position
        self._force_square_off()

        # Disconnect WebSocket
        if self.ws_connected:
            try:
                self.client.disconnect()
                self.ws_connected = False
                log.info("WebSocket disconnected")
            except Exception:
                pass

        log.info(
            "FINAL STATS: PnL=%.2f | Wins=%d | Losses=%d | Trades=%d",
            self.daily_stats.realized_pnl, self.daily_stats.wins,
            self.daily_stats.losses, self.daily_stats.trades_taken,
        )
        log.info("Autonomous Options Buyer STOPPED")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    trader = AutonomousOptionsTrader(CONFIG)

    # Handle graceful shutdown on SIGINT/SIGTERM
    def signal_handler(signum, frame):
        log.info("Signal %s received. Shutting down...", signum)
        trader.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    trader.run()


if __name__ == "__main__":
    main()
