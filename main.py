import asyncio
import os
import time
import json
import re
import random
from enum import Enum

import tweepy
from binance import AsyncClient
from binance.enums import *
from dotenv import load_dotenv

# Import your sentiment engine and config
from sentiment import get_sentiment, Signal

load_dotenv()

# ==================== CONFIG (from your original) ====================
SYMBOL = os.getenv("SYMBOL", "BZUSDT")
FALLBACK_SYMBOL = os.getenv("FALLBACK_SYMBOL", "WTIUSDT")
LEVERAGE = int(os.getenv("LEVERAGE", "10"))
MARGIN_PERCENT = float(os.getenv("MARGIN_PERCENT", "0.5"))
SL_PERCENT = float(os.getenv("SL_PERCENT", "0.01"))
TP_PERCENT = float(os.getenv("TP_PERCENT", "0.01"))
FEE_RATE = float(os.getenv("FEE_RATE", "0.0004"))
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "100.0"))
REVERSE_ON_SIGNAL = os.getenv("REVERSE_ON_SIGNAL", "True").lower() == "true"
MIN_NOTIONAL = float(os.getenv("MIN_NOTIONAL", "5.0"))
CONFLICT_MODE = os.getenv("CONFLICT_MODE", "BEAR_BIAS")
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "5"))

# Binance keys
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET = os.getenv("BINANCE_SECRET")
TWITTER_BEARER = os.getenv("TWITTER_BEARER")

# ==================== FuturesAccount (your exact class) ====================
class FuturesAccount:
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


# ==================== OIL BOT (async) ====================
class OilBot:
    def __init__(self):
        self.client = None          # Binance AsyncClient
        self.account = FuturesAccount(INITIAL_CAPITAL)
        self.last_trade_time = 0
        self.active_symbol = SYMBOL  # may switch to fallback

    async def init_binance(self):
        """Connect to Binance Futures Testnet and set leverage."""
        self.client = await AsyncClient.create(
            BINANCE_API_KEY, BINANCE_SECRET, testnet=True
        )
        # Check if symbol exists
        exchange_info = await self.client.futures_exchange_info()
        symbols = [s['symbol'] for s in exchange_info['symbols']]
        if SYMBOL not in symbols:
            print(f"[WARN] {SYMBOL} not on testnet. Trying fallback {FALLBACK_SYMBOL}...")
            if FALLBACK_SYMBOL not in symbols:
                print(f"[CRITICAL] Neither {SYMBOL} nor {FALLBACK_SYMBOL} found on testnet. Exiting.")
                exit(1)
            self.active_symbol = FALLBACK_SYMBOL
        else:
            self.active_symbol = SYMBOL
        print(f"[INIT] Using symbol: {self.active_symbol}")

        # Set leverage (futures only)
        try:
            await self.client.futures_change_leverage(symbol=self.active_symbol, leverage=LEVERAGE)
        except Exception as e:
            print(f"[WARN] Could not set leverage: {e}")

    async def fetch_price(self):
        """Async price fetch using Binance ticker."""
        try:
            ticker = await self.client.futures_symbol_ticker(symbol=self.active_symbol)
            price = float(ticker['price'])
            return price
        except Exception:
            # Fallback to REST if websocket ticker fails (rare)
            try:
                info = await self.client.futures_mark_price(symbol=self.active_symbol)
                price = float(info['markPrice'])
                return price
            except Exception as e:
                print(f"[ERROR] Price fetch failed: {e}")
                return None

    async def place_market_order(self, side, quantity):
        """Place a MARKET order on Binance Futures."""
        try:
            order = await self.client.futures_create_order(
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

    async def place_stop_order(self, side, quantity, stop_price):
        """Place a STOP_MARKET order (stop-loss)."""
        try:
            order = await self.client.futures_create_order(
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

    async def place_tp_order(self, side, quantity, tp_price):
        """Place a TAKE_PROFIT_MARKET order."""
        try:
            order = await self.client.futures_create_order(
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

    async def execute_trade(self, signal):
        """The exact trading logic from your original main loop."""
        current_time = time.time()
        if current_time - self.last_trade_time < COOLDOWN_SECONDS:
            print(f"[COOLDOWN] Skipping trade (wait {COOLDOWN_SECONDS - (current_time - self.last_trade_time):.1f}s)")
            return

        price = await self.fetch_price()
        if price is None:
            print("[ERROR] Could not fetch price, aborting trade.")
            return
        self.account.update_price(price)

        # Margin & size calculation (your formula)
        equity_before = self.account.total_equity
        margin_to_use = equity_before * MARGIN_PERCENT
        position_value = margin_to_use * LEVERAGE
        quantity = position_value / price
        print(f"[TRADE] Price: ${price:.2f} | Equity: ${equity_before:.2f} | Size: {quantity:.4f} contracts")

        pos = self.account.position

        if pos == 0:
            # Open new position
            if signal == Signal.BULL:
                side = SIDE_BUY
                sl_side = SIDE_SELL
                sl_price = price * (1 - SL_PERCENT)
                tp_price = price * (1 + TP_PERCENT)
            else:  # BEAR
                side = SIDE_SELL
                sl_side = SIDE_BUY
                sl_price = price * (1 + SL_PERCENT)
                tp_price = price * (1 - TP_PERCENT)

            # Place orders on Binance
            market_ok = await self.place_market_order(side, quantity)
            if market_ok:
                await self.place_stop_order(sl_side, quantity, sl_price)
                await self.place_tp_order(sl_side, quantity, tp_price)
                # Update internal account
                self.account.open_position("BUY" if side == SIDE_BUY else "SELL", quantity, price)
                print(f"[POSITION] SL: ${sl_price:.2f} | TP: ${tp_price:.2f}")
        else:
            # Existing position
            if (pos > 0 and signal == Signal.BULL) or (pos < 0 and signal == Signal.BEAR):
                print("[HOLD] Same direction signal. No action.")
            else:
                print("[REVERSE] Opposite signal – closing and reversing.")
                # Close current position on exchange: we can place a market order of same quantity in opposite direction
                # Binance will net the position. For simplicity, we place a reduce-only order or just a plain opposite order.
                if pos > 0:
                    close_side = SIDE_SELL
                    close_qty = pos
                else:
                    close_side = SIDE_BUY
                    close_qty = -pos
                await self.place_market_order(close_side, close_qty)
                # Update internal account
                self.account.close_position(price)

                # Now open new position in opposite direction
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

                await self.place_market_order(side, quantity)
                await self.place_stop_order(sl_side, quantity, sl_price)
                await self.place_tp_order(sl_side, quantity, tp_price)
                self.account.open_position("BUY" if side == SIDE_BUY else "SELL", quantity, price)

        self.last_trade_time = current_time

    async def on_tweet(self, tweet):
        """Called for every incoming tweet from the filtered stream."""
        if tweet.referenced_tweets:  # ignore retweets, quotes, replies
            return
        text = tweet.text
        if not text or len(text) < 20:
            return

        # Use your sentiment engine
        signal = get_sentiment(text, conflict_mode=CONFLICT_MODE)
        print(f"\n[TWEET] {text[:150]}...")
        print(f"[SENTIMENT] {signal.value}")

        if signal == Signal.NEUTRAL:
            return

        await self.execute_trade(signal)

    async def start_stream(self):
        """Initialize Twitter filtered stream."""
        stream = tweepy.AsyncStreamingClient(bearer_token=TWITTER_BEARER)

        # Clear old rules
        rules = await stream.get_rules()
        if rules.data:
            await stream.delete_rules([r.id for r in rules.data])

        # Rules: follow specific accounts and keywords
        await stream.add_rules([
            tweepy.StreamRule("from:RaoulGMI OR from:PeterLBrandt OR #OOTT OR #crude OR #WTI OR #OPEC OR #oil"),
            tweepy.StreamRule("Brent OR crude oil OR oil prices OR energy market"),
        ])

        # Override the on_tweet callback
        stream.on_tweet = self.on_tweet
        print("[STREAM] Twitter stream started. Listening for oil news...")
        await stream.filter(tweet_fields=["author_id", "created_at", "referenced_tweets"])


async def main():
    bot = OilBot()
    await bot.init_binance()
    await bot.start_stream()

if __name__ == "__main__":
    asyncio.run(main())
