import os
import time
import json
import re
import random
from enum import Enum

import feedparser
from binance.client import Client
from binance.enums import *
from dotenv import load_dotenv

from sentiment import get_sentiment, Signal

load_dotenv()

# ==================== CONFIG ====================
SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
FALLBACK_SYMBOL = os.getenv("FALLBACK_SYMBOL", "BTCUSDT")
LEVERAGE = int(os.getenv("LEVERAGE", "10"))
MARGIN_PERCENT = float(os.getenv("MARGIN_PERCENT", "0.5"))
SL_PERCENT = float(os.getenv("SL_PERCENT", "0.01"))
TP_PERCENT = float(os.getenv("TP_PERCENT", "0.01"))
FEE_RATE = float(os.getenv("FEE_RATE", "0.0004"))
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "100.0"))
MIN_NOTIONAL = float(os.getenv("MIN_NOTIONAL", "5.0"))
CONFLICT_MODE = os.getenv("CONFLICT_MODE", "BEAR_BIAS")
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "60"))

# RSS feed for oil news (Reuters via Google News)
RSS_URL = os.getenv("RSS_URL", "https://news.google.com/rss/search?q=crude+oil+OPEC+WTI+Brent&hl=en-US&gl=US&ceid=US:en")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))  # seconds between RSS checks

# Binance keys
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET = os.getenv("BINANCE_SECRET")

# ==================== FuturesAccount (unchanged) ====================
class FuturesAccount:
    # ... exactly the same as your original code ...
    def __init__(self, initial_balance):
        self.cash = initial_balance
        self.position = 0.0
        self.entry_price = None
        self.margin_used = 0.0
        self.realized_pnl = 0.0
        self.leverage = LEVERAGE
        self.current_price = 0.0

    @property
    def total_equity(self):
        unrealized = 0
        if self.position > 0:
            unrealized = self.position * (self.current_price - self.entry_price)
        elif self.position < 0:
            unrealized = -self.position * (self.entry_price - self.current_price)
        return self.cash + self.margin_used + unrealized

    @property
    def free_margin(self):
        return self.total_equity - self.margin_used

    def update_price(self, price):
        self.current_price = price

    def can_open(self, margin_required):
        return self.free_margin >= margin_required

    def open_position(self, side, amount, price):
        position_value = amount * price
        if position_value < MIN_NOTIONAL:
            print(f"[ERROR] Position value ${position_value:.2f} below min notional. Rejected.")
            return None
        margin_req = position_value / self.leverage
        if not self.can_open(margin_req):
            print("[ERROR] Not enough free margin.")
            return None
        self.cash -= margin_req
        self.margin_used += margin_req
        if side == "BUY":
            if self.position < 0:
                self._close_short_internal(price)
            self.position += amount
            self.entry_price = price
        else:
            if self.position > 0:
                self._close_long_internal(price)
            self.position -= amount
            self.entry_price = price
        fee = position_value * FEE_RATE
        self.cash -= fee
        print(f"[ACCOUNT] Opened. Margin: ${margin_req:.2f} | Free: ${self.free_margin:.2f}")
        return True

    def close_position(self, price):
        if self.position > 0:
            self._close_long_internal(price)
        elif self.position < 0:
            self._close_short_internal(price)

    def _close_long_internal(self, price):
        amount = self.position
        pnl = amount * (price - self.entry_price)
        fee = amount * price * FEE_RATE
        self.cash += self.margin_used + pnl - fee
        self.margin_used = 0
        self.position = 0
        self.entry_price = None
        self.realized_pnl += pnl
        print(f"[CLOSE LONG] PnL: ${pnl:.2f} | Fee: ${fee:.2f}")

    def _close_short_internal(self, price):
        amount = -self.position
        pnl = amount * (self.entry_price - price)
        fee = amount * price * FEE_RATE
        self.cash += self.margin_used + pnl - fee
        self.margin_used = 0
        self.position = 0
        self.entry_price = None
        self.realized_pnl += pnl
        print(f"[CLOSE SHORT] PnL: ${pnl:.2f} | Fee: ${fee:.2f}")


# ==================== OIL BOT (sync) ====================
class OilBot:
    def __init__(self):
        self.client = None
        self.account = FuturesAccount(INITIAL_CAPITAL)
        self.last_trade_time = 0
        self.active_symbol = SYMBOL
        self.seen_guids = set()   # to avoid processing same news twice

    def init_binance(self):
        """Connect to Binance Futures Testnet."""
        self.client = Client(BINANCE_API_KEY, BINANCE_SECRET, testnet=True)
        # Check symbol availability
        try:
            info = self.client.futures_exchange_info()
            symbols = [s['symbol'] for s in info['symbols']]
            if self.active_symbol not in symbols:
                print(f"[WARN] {self.active_symbol} not on testnet. Trying fallback {FALLBACK_SYMBOL}...")
                if FALLBACK_SYMBOL not in symbols:
                    print(f"[CRITICAL] Neither symbol found. Exiting.")
                    exit(1)
                self.active_symbol = FALLBACK_SYMBOL
            print(f"[INIT] Using symbol: {self.active_symbol}")
        except Exception as e:
            print(f"[ERROR] Cannot fetch exchange info: {e}")
            exit(1)

        # Set leverage
        try:
            self.client.futures_change_leverage(symbol=self.active_symbol, leverage=LEVERAGE)
        except Exception as e:
            print(f"[WARN] Could not set leverage: {e}")

    def fetch_price(self):
        """Get current mark price."""
        try:
            ticker = self.client.futures_symbol_ticker(symbol=self.active_symbol)
            return float(ticker['price'])
        except:
            try:
                mark = self.client.futures_mark_price(symbol=self.active_symbol)
                return float(mark['markPrice'])
            except Exception as e:
                print(f"[ERROR] Price fetch failed: {e}")
                return None

    def place_market_order(self, side, quantity):
        """Synchronous MARKET order."""
        try:
            order = self.client.futures_create_order(
                symbol=self.active_symbol,
                side=side,
                type=FUTURE_ORDER_TYPE_MARKET,
                quantity=round(quantity, 3)
            )
            print(f"[MARKET ORDER] {order}")
            return order
        except Exception as e:
            print(f"[MARKET ORDER ERROR] {e}")
            return None

    def place_stop_order(self, side, quantity, stop_price):
        """Synchronous STOP_MARKET order."""
        try:
            order = self.client.futures_create_order(
                symbol=self.active_symbol,
                side=side,
                type=FUTURE_ORDER_TYPE_STOP_MARKET,
                quantity=round(quantity, 3),
                stopPrice=round(stop_price, 2)
            )
            print(f"[STOP LOSS] {order}")
            return order
        except Exception as e:
            print(f"[STOP ORDER ERROR] {e}")
            return None

    def place_tp_order(self, side, quantity, tp_price):
        """Synchronous TAKE_PROFIT_MARKET order."""
        try:
            order = self.client.futures_create_order(
                symbol=self.active_symbol,
                side=side,
                type=FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
                quantity=round(quantity, 3),
                stopPrice=round(tp_price, 2)
            )
            print(f"[TAKE PROFIT] {order}")
            return order
        except Exception as e:
            print(f"[TP ORDER ERROR] {e}")
            return None

    def execute_trade(self, signal):
        """Execute trade based on signal (same logic as original)."""
        now = time.time()
        if now - self.last_trade_time < COOLDOWN_SECONDS:
            print(f"[COOLDOWN] Wait {COOLDOWN_SECONDS - (now - self.last_trade_time):.1f}s")
            return

        price = self.fetch_price()
        if price is None:
            return
        self.account.update_price(price)

        equity_before = self.account.total_equity
        margin_to_use = equity_before * MARGIN_PERCENT
        position_value = margin_to_use * LEVERAGE
        quantity = position_value / price
        print(f"[TRADE] Price: ${price:.2f} | Equity: ${equity_before:.2f} | Size: {quantity:.4f}")

        pos = self.account.position

        if pos == 0:
            if signal == Signal.BULL:
                side = SIDE_BUY
                sl_side = SIDE_SELL
                sl_price = price * (1 - SL_PERCENT)
                tp_price = price * (1 + TP_PERCENT)
            else:
                side = SIDE_SELL
                sl_side = SIDE_BUY
                sl_price = price * (1 + SL_PERCENT)
                tp_price = price * (1 - TP_PERCENT)

            if self.place_market_order(side, quantity):
                self.place_stop_order(sl_side, quantity, sl_price)
                self.place_tp_order(sl_side, quantity, tp_price)
                self.account.open_position("BUY" if side == SIDE_BUY else "SELL", quantity, price)
                print(f"[POSITION] SL: ${sl_price:.2f} | TP: ${tp_price:.2f}")
        else:
            if (pos > 0 and signal == Signal.BULL) or (pos < 0 and signal == Signal.BEAR):
                print("[HOLD] Same direction.")
            else:
                print("[REVERSE] Opposite signal – closing and reversing.")
                close_side = SIDE_SELL if pos > 0 else SIDE_BUY
                close_qty = abs(pos)
                self.place_market_order(close_side, close_qty)
                self.account.close_position(price)

                if signal == Signal.BULL:
                    side = SIDE_BUY
                    sl_side = SIDE_SELL
                    sl_price = price * (1 - SL_PERCENT)
                    tp_price = price * (1 + TP_PERCENT)
                else:
                    side = SIDE_SELL
                    sl_side = SIDE_BUY
                    sl_price = price * (1 + SL_PERCENT)
                    tp_price = price * (1 - TP_PERCENT)

                if self.place_market_order(side, quantity):
                    self.place_stop_order(sl_side, quantity, sl_price)
                    self.place_tp_order(sl_side, quantity, tp_price)
                    self.account.open_position("BUY" if side == SIDE_BUY else "SELL", quantity, price)

        self.last_trade_time = time.time()

    def check_news(self):
        """Fetch RSS feed, run sentiment on new headlines, trigger trade if strong signal."""
        print(f"[RSS] Fetching {RSS_URL}")
        feed = feedparser.parse(RSS_URL)
        if not feed.entries:
            print("[RSS] No entries found.")
            return

        for entry in feed.entries:
            guid = entry.get('id') or entry.get('link')
            if guid in self.seen_guids:
                continue
            self.seen_guids.add(guid)

            title = entry.get('title', '')
            summary = entry.get('summary', '')
            text = title + " " + summary
            if len(text) < 20:
                continue

            print(f"\n[NEWS] {title}")
            signal = get_sentiment(text, conflict_mode=CONFLICT_MODE)
            print(f"[SENTIMENT] {signal.value}")
            if signal != Signal.NEUTRAL:
                self.execute_trade(signal)

    def run(self):
        """Main loop."""
        self.init_binance()
        print("[START] News scanner active. Listening for oil headlines...")
        while True:
            try:
                self.check_news()
            except Exception as e:
                print(f"[ERROR] check_news: {e}")
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    bot = OilBot()
    bot.run()
