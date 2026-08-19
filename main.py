import os
import time
import json
import re
import random
import logging
import sys
from enum import Enum
from datetime import datetime, timedelta
import requests
import xml.etree.ElementTree as ET
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException
from dotenv import load_dotenv
from sentiment import get_sentiment, Signal
from twitter_monitor import TwitterMonitor

# ---- Force unbuffered output ----
sys.stdout.reconfigure(line_buffering=True)

load_dotenv()

# ==================== CONFIG ====================
SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
FALLBACK_SYMBOL = os.getenv("FALLBACK_SYMBOL", "BTCUSDT")

# FORCE 20x LEVERAGE
ENV_LEVERAGE = int(os.getenv("LEVERAGE", "20"))
LEVERAGE = 20 if ENV_LEVERAGE == 10 else ENV_LEVERAGE
if ENV_LEVERAGE == 10:
    print("⚠️  WARNING: LEVERAGE env is 10, but we force 20x for your strategy.", flush=True)
    logging.warning("LEVERAGE forced to 20x (env was 10)")

POSITION_PERCENT = float(os.getenv("POSITION_PERCENT", "0.5"))   # 50% of equity as margin
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "0"))      # ZERO cooldown – instant trades

SL_PERCENT = float(os.getenv("SL_PERCENT", "0.10"))        # 10% stop loss
TP_PERCENT = float(os.getenv("TP_PERCENT", "0.10"))        # 10% take profit

FEE_RATE = float(os.getenv("FEE_RATE", "0.0004"))
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "100.0"))   # fallback
CONFLICT_MODE = os.getenv("CONFLICT_MODE", "BEAR_BIAS")

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET = os.getenv("BINANCE_SECRET")

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ==================== FuturesAccount ====================
class FuturesAccount:
    def __init__(self, initial_balance):
        self.cash = initial_balance
        self.position = 0.0
        self.entry_price = None
        self.margin_used = 0.0
        self.realized_pnl = 0.0
        self.leverage = LEVERAGE
        self.current_price = 0.0
        self.initial_balance = initial_balance

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

    def set_balance(self, real_balance):
        self.cash = real_balance
        self.initial_balance = real_balance

    def sync_position(self, pos_info):
        for pos in pos_info:
            if pos['symbol'] == self.active_symbol:
                amount = float(pos['positionAmt'])
                if amount != 0:
                    self.position = amount
                    self.entry_price = float(pos['entryPrice'])
                    self.margin_used = float(pos.get('isolatedMargin', 0))
                    return
        self.position = 0.0
        self.entry_price = None
        self.margin_used = 0.0

    def open_position(self, side, amount, price):
        position_value = amount * price
        margin_req = position_value / self.leverage
        if not self.can_open(margin_req):
            msg = "Not enough free margin."
            print(f"[ERROR] {msg}")
            logging.error(msg)
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
        msg = f"Opened. Margin: ${margin_req:.2f} | Free: ${self.free_margin:.2f}"
        print(f"[ACCOUNT] {msg}")
        logging.info(msg)
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
        msg = f"CLOSE LONG - PnL: ${pnl:.2f} | Fee: ${fee:.2f}"
        print(f"[{msg}]")
        logging.info(msg)

    def _close_short_internal(self, price):
        amount = -self.position
        pnl = amount * (self.entry_price - price)
        fee = amount * price * FEE_RATE
        self.cash += self.margin_used + pnl - fee
        self.margin_used = 0
        self.position = 0
        self.entry_price = None
        self.realized_pnl += pnl
        msg = f"CLOSE SHORT - PnL: ${pnl:.2f} | Fee: ${fee:.2f}"
        print(f"[{msg}]")
        logging.info(msg)


# ==================== OIL BOT ====================
class OilBot:
    def __init__(self):
        self.client = None
        self.account = FuturesAccount(INITIAL_CAPITAL)
        self.last_trade_time = 0
        self.active_symbol = SYMBOL
        self.seen_guids = set()
        self.consecutive_errors = 0
        self.max_consecutive_errors = 5

        self._price_cache = None
        self._price_cache_time = 0
        self._price_cache_ttl = 2

        self.sl_price = None
        self.tp_price = None
        self.monitor_thread = None
        self.stop_monitoring = False

        self.min_qty = None
        self.step_size = None
        self.min_notional = None

        # ---- Twitter Monitor ----
        self.twitter_monitor = None
        self.twitter_target = os.getenv("TWITTER_TARGET_USER")
        self.twitter_auth = os.getenv("TWITTER_AUTH_TOKEN")
        self.twitter_interval = int(os.getenv("TWITTER_POLL_INTERVAL", "15"))

    def init_binance(self):
        try:
            self.client = Client(BINANCE_API_KEY, BINANCE_SECRET, testnet=True)
            self.client.API_URL = 'https://testnet.binance.vision/api'

            info = self.client.futures_exchange_info()
            symbols = [s['symbol'] for s in info['symbols']]
            print(f"[DIAG] Futures exchange info OK. Symbols: {len(symbols)}", flush=True)
            if self.active_symbol not in symbols:
                print(f"[WARN] {self.active_symbol} not on testnet. Trying fallback {FALLBACK_SYMBOL}...")
                logging.warning(f"{self.active_symbol} not found. Trying fallback.")
                if FALLBACK_SYMBOL not in symbols:
                    print(f"[CRITICAL] Neither symbol found. Exiting.")
                    logging.critical("No valid symbols found.")
                    exit(1)
                self.active_symbol = FALLBACK_SYMBOL

            for s in info['symbols']:
                if s['symbol'] == self.active_symbol:
                    for f in s['filters']:
                        if f['filterType'] == 'LOT_SIZE':
                            self.step_size = float(f['stepSize'])
                            self.min_qty = float(f['minQty'])
                        elif f['filterType'] == 'MIN_NOTIONAL':
                            self.min_notional = float(f['notional'])
                    break

            if self.min_qty is None:
                self.min_qty = 0.001
                self.step_size = 0.001
                self.min_notional = 5.0

            print(f"[DIAG] Symbol filters: minQty={self.min_qty}, stepSize={self.step_size}, minNotional={self.min_notional}", flush=True)

            account = self.client.futures_account()
            total_balance = float(account.get('totalWalletBalance', 0))
            print(f"[DIAG] Futures account access OK. Total Wallet Balance: ${total_balance:.2f}", flush=True)
            self.account.set_balance(total_balance)
            print(f"[INFO] Account balance updated to ${total_balance:.2f}", flush=True)
            logging.info(f"Real balance set to ${total_balance:.2f}")

            # ---- Set leverage (force 20x) ----
            try:
                self.client.futures_change_leverage(symbol=self.active_symbol, leverage=LEVERAGE)
                logging.info(f"Leverage set to {LEVERAGE}x")
            except Exception as e:
                print(f"[WARN] Leverage setting failed: {e}")

            # ---- Set margin type to ISOLATED ----
            for attempt in range(3):
                try:
                    self.client.futures_change_margin_type(symbol=self.active_symbol, marginType='ISOLATED')
                    logging.info(f"Margin type set to ISOLATED for {self.active_symbol}")
                    break
                except Exception as e:
                    if "No need to change margin type" in str(e):
                        logging.info("Margin type already ISOLATED")
                        break
                    else:
                        print(f"[WARN] Margin type attempt {attempt+1} failed: {e}")
                        time.sleep(0.5)

            print(f"[INIT] Connected to Futures Demo. Using symbol: {self.active_symbol}")
            logging.info(f"Connected to Futures Demo. Symbol: {self.active_symbol}")
        except Exception as e:
            print(f"[ERROR] Cannot connect to Binance Futures Demo: {e}")
            logging.error(f"Connection failed: {e}")
            exit(1)

    def fetch_price(self):
        now = time.time()
        if self._price_cache is not None and (now - self._price_cache_time) < self._price_cache_ttl:
            return self._price_cache

        for attempt in range(3):
            try:
                ticker = self.client.futures_symbol_ticker(symbol=self.active_symbol)
                price = float(ticker['price'])
                self._price_cache = price
                self._price_cache_time = time.time()
                return price
            except BinanceAPIException as e:
                if e.code == -1003:
                    wait = 0.5 * (2 ** attempt)
                    print(f"[RATE LIMIT] Waiting {wait}s before retry...")
                    time.sleep(wait)
                else:
                    if attempt < 2:
                        time.sleep(0.3)
                    else:
                        print(f"[ERROR] Price fetch failed: {e}")
                        logging.error(f"Price fetch failed: {e}")
                        return None
            except Exception as e:
                if attempt < 2:
                    time.sleep(0.3)
                else:
                    print(f"[ERROR] Price fetch failed: {e}")
                    logging.error(f"Price fetch failed: {e}")
                    return None
        return None

    def place_market_order(self, side, quantity):
        if self.step_size:
            quantity = round(quantity / self.step_size) * self.step_size
        quantity = round(quantity, 8)
        for attempt in range(3):
            try:
                order = self.client.futures_create_order(
                    symbol=self.active_symbol,
                    side=side,
                    type=FUTURE_ORDER_TYPE_MARKET,
                    quantity=quantity
                )
                print(f"[MARKET ORDER] {order}")
                logging.info(f"MARKET ORDER: {order}")
                return order
            except BinanceAPIException as e:
                if e.code == -1003:
                    wait = 0.5 * (2 ** attempt)
                    print(f"[RATE LIMIT] Waiting {wait}s before retry...")
                    time.sleep(wait)
                else:
                    print(f"[MARKET ORDER ERROR] Attempt {attempt+1}: {e}")
                    if attempt < 2:
                        time.sleep(0.5)
                    else:
                        logging.error(f"Market order failed: {e}")
                        return None
            except Exception as e:
                print(f"[MARKET ORDER ERROR] Attempt {attempt+1}: {e}")
                if attempt < 2:
                    time.sleep(0.5)
                else:
                    logging.error(f"Market order failed: {e}")
                    return None
        return None

    def place_stop_order(self, side, quantity, stop_price):
        if self.step_size:
            quantity = round(quantity / self.step_size) * self.step_size
        quantity = round(quantity, 8)
        for attempt in range(3):
            try:
                order = self.client.futures_create_algo_order(
                    symbol=self.active_symbol,
                    side=side,
                    type=FUTURE_ORDER_TYPE_STOP_MARKET,
                    quantity=quantity,
                    triggerPrice=round(stop_price, 2),
                    workingType='MARK_PRICE',
                    reduceOnly=True,
                    algoType='CONDITIONAL',
                    priceProtect=True,
                )
                print(f"[STOP LOSS] ✅ ORDER PLACED - AlgoId: {order.get('algoId', 'N/A')} | Trigger: ${stop_price:.2f}")
                logging.info(f"STOP LOSS PLACED: {order}")
                return order
            except BinanceAPIException as e:
                if e.code == -1003:
                    wait = 0.5 * (2 ** attempt)
                    print(f"[RATE LIMIT] Waiting {wait}s before retry...")
                    time.sleep(wait)
                else:
                    print(f"[STOP ORDER ERROR] Attempt {attempt+1}: {e}")
                    if attempt < 2:
                        time.sleep(0.5)
                    else:
                        logging.error(f"Stop order failed: {e}")
                        return None
            except Exception as e:
                print(f"[STOP ORDER ERROR] Attempt {attempt+1}: {e}")
                if attempt < 2:
                    time.sleep(0.5)
                else:
                    logging.error(f"Stop order failed: {e}")
                    return None
        return None

    def place_tp_order(self, side, quantity, tp_price):
        if self.step_size:
            quantity = round(quantity / self.step_size) * self.step_size
        quantity = round(quantity, 8)
        for attempt in range(3):
            try:
                order = self.client.futures_create_algo_order(
                    symbol=self.active_symbol,
                    side=side,
                    type=FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
                    quantity=quantity,
                    triggerPrice=round(tp_price, 2),
                    workingType='MARK_PRICE',
                    reduceOnly=True,
                    algoType='CONDITIONAL',
                    priceProtect=True,
                )
                print(f"[TAKE PROFIT] ✅ ORDER PLACED - AlgoId: {order.get('algoId', 'N/A')} | Trigger: ${tp_price:.2f}")
                logging.info(f"TAKE PROFIT PLACED: {order}")
                return order
            except BinanceAPIException as e:
                if e.code == -1003:
                    wait = 0.5 * (2 ** attempt)
                    print(f"[RATE LIMIT] Waiting {wait}s before retry...")
                    time.sleep(wait)
                else:
                    print(f"[TP ORDER ERROR] Attempt {attempt+1}: {e}")
                    if attempt < 2:
                        time.sleep(0.5)
                    else:
                        logging.error(f"TP order failed: {e}")
                        return None
            except Exception as e:
                print(f"[TP ORDER ERROR] Attempt {attempt+1}: {e}")
                if attempt < 2:
                    time.sleep(0.5)
                else:
                    logging.error(f"TP order failed: {e}")
                    return None
        return None

    def monitor_position(self):
        print("[MONITOR] Starting position monitor thread.")
        while not self.stop_monitoring:
            if self.account.position == 0:
                break
            current_price = self.fetch_price()
            if current_price is None:
                time.sleep(0.5)
                continue
            self.account.update_price(current_price)

            if self.account.position > 0:
                if current_price <= self.sl_price:
                    print(f"[SL HIT] Long closed at ${current_price:.2f}")
                    self.place_market_order(SIDE_SELL, abs(self.account.position))
                    self.account.close_position(current_price)
                    break
                elif current_price >= self.tp_price:
                    print(f"[TP HIT] Long closed at ${current_price:.2f}")
                    self.place_market_order(SIDE_SELL, abs(self.account.position))
                    self.account.close_position(current_price)
                    break
            elif self.account.position < 0:
                if current_price >= self.sl_price:
                    print(f"[SL HIT] Short closed at ${current_price:.2f}")
                    self.place_market_order(SIDE_BUY, abs(self.account.position))
                    self.account.close_position(current_price)
                    break
                elif current_price <= self.tp_price:
                    print(f"[TP HIT] Short closed at ${current_price:.2f}")
                    self.place_market_order(SIDE_BUY, abs(self.account.position))
                    self.account.close_position(current_price)
                    break
            time.sleep(0.5)
        print("[MONITOR] Monitor thread ended.")

    def sync_position_from_exchange(self):
        try:
            positions = self.client.futures_position_information(symbol=self.active_symbol)
            for pos in positions:
                if pos['symbol'] == self.active_symbol:
                    amt = float(pos['positionAmt'])
                    if amt != 0:
                        self.account.position = amt
                        self.account.entry_price = float(pos['entryPrice'])
                        self.account.margin_used = float(pos.get('isolatedMargin', 0))
                        return
            self.account.position = 0.0
            self.account.entry_price = None
            self.account.margin_used = 0.0
        except Exception as e:
            print(f"[WARN] Could not sync position: {e}")

    def init_twitter_monitor(self):
        if not self.twitter_target or not self.twitter_auth:
            logging.warning("Twitter credentials missing. Twitter monitor disabled.")
            return

        self.twitter_monitor = TwitterMonitor(
            target_user=self.twitter_target,
            auth_token=self.twitter_auth,
            poll_interval=self.twitter_interval
        )

        def on_new_tweet(tweet):
            tweet_text = tweet.get('text', '')
            logging.info(f"Processing tweet from @{self.twitter_target}: {tweet_text[:100]}...")
            
            signal = get_sentiment(tweet_text, conflict_mode=CONFLICT_MODE)
            logging.info(f"Sentiment result: {signal.value}")
            
            if signal == Signal.NEUTRAL:
                logging.info("Tweet neutral — no trade.")
                return
            
            trade_result = self.execute_trade(signal)
            logging.info(f"Trade result: {trade_result}")

        success = self.twitter_monitor.start(on_new_tweet)
        if success:
            print(f"[TWITTER] Monitoring @{self.twitter_target} every {self.twitter_interval}s.", flush=True)
        else:
            print("[TWITTER] Failed to start monitor. Check auth token.", flush=True)

    def execute_trade(self, signal):
        now = time.time()
        if now - self.last_trade_time < COOLDOWN_SECONDS:
            wait = COOLDOWN_SECONDS - (now - self.last_trade_time)
            if wait > 0:
                print(f"[COOLDOWN] Wait {wait:.3f}s")
            return {"status": "cooldown", "wait": wait}

        self.sync_position_from_exchange()

        if self.account.position != 0:
            print(f"[SKIP] Position already open. Ignoring signal {signal.value}.", flush=True)
            return {"status": "position_active", "message": "Position already open. Ignoring signal."}

        price = self.fetch_price()
        if price is None:
            self.consecutive_errors += 1
            if self.consecutive_errors >= self.max_consecutive_errors:
                print("[CRITICAL] Too many errors. Waiting 60s...")
                time.sleep(60)
                self.consecutive_errors = 0
            return {"status": "error", "message": "Price fetch failed"}

        self.consecutive_errors = 0
        self.account.update_price(price)

        equity = self.account.total_equity
        target_margin = equity * POSITION_PERCENT
        desired_position_value = target_margin * LEVERAGE
        quantity = desired_position_value / price

        if self.step_size:
            quantity = round(quantity / self.step_size) * self.step_size
        quantity = round(quantity, 8)
        position_value = quantity * price

        if self.min_notional is not None and position_value < self.min_notional:
            print(f"[ERROR] Position value ${position_value:.2f} below minimum notional ${self.min_notional:.2f}. Cannot trade.", flush=True)
            return {"status": "error", "message": f"Position ${position_value:.2f} below min notional ${self.min_notional:.2f}"}

        if self.min_qty is not None and quantity < self.min_qty:
            print(f"[ERROR] Quantity {quantity:.8f} below minimum {self.min_qty}. Cannot trade.", flush=True)
            return {"status": "error", "message": f"Quantity {quantity:.8f} below min {self.min_qty}"}

        margin_required = position_value / LEVERAGE
        free_margin = self.account.free_margin

        max_margin_use = free_margin * 0.98
        max_position_value = max_margin_use * LEVERAGE
        max_quantity = max_position_value / price
        if self.step_size:
            max_quantity = round(max_quantity / self.step_size) * self.step_size
        max_quantity = round(max_quantity, 8)
        max_position_value = max_quantity * price

        print(f"[MARGIN DEBUG] price: {price:.2f}, target margin: ${target_margin:.2f}, desired position: ${desired_position_value:.2f}, position: ${position_value:.2f}, margin_required: ${margin_required:.2f}, free_margin: ${free_margin:.2f}, max_margin_use (98%): ${max_margin_use:.2f}", flush=True)

        if position_value > max_position_value:
            print(f"[MARGIN] Reducing position from ${position_value:.2f} to ${max_position_value:.2f} (using 98% of available margin)", flush=True)
            logging.warning(f"Reduced position from ${position_value:.2f} to ${max_position_value:.2f} to leave margin buffer")
            quantity = max_quantity
            position_value = max_position_value

        if position_value < self.min_notional or quantity < self.min_qty:
            print(f"[ERROR] After margin adjustment, position would be below minimum. Cannot trade.", flush=True)
            return {"status": "error", "message": "Position below minimum after margin adjustment"}

        print(f"[TRADE] Price: ${price:.2f} | Equity: ${equity:.2f} | Margin used: ${position_value/LEVERAGE:.2f} ({POSITION_PERCENT*100:.0f}% of equity) | Leverage: {LEVERAGE}x | Position: ${position_value:.2f} | Size: {quantity:.8f}", flush=True)
        logging.info(f"Trade signal: {signal.value} | Price: ${price:.2f} | Equity: ${equity:.2f} | Position: ${position_value:.2f}")

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

        market_ok = self.place_market_order(side, quantity)
        if market_ok:
            self.sl_price = sl_price
            self.tp_price = tp_price
            sl_ok = self.place_stop_order(sl_side, quantity, sl_price)
            tp_ok = self.place_tp_order(sl_side, quantity, tp_price)
            if not sl_ok or not tp_ok:
                print("[WARN] Algo SL/TP failed, starting manual monitor.")
                self.stop_monitoring = False
                if self.monitor_thread is None or not self.monitor_thread.is_alive():
                    self.monitor_thread = threading.Thread(target=self.monitor_position, daemon=True)
                    self.monitor_thread.start()
            self.account.open_position("BUY" if side == SIDE_BUY else "SELL", quantity, price)
            self.last_trade_time = time.time()
            return {"status": "executed", "signal": signal.value, "price": price, "quantity": quantity, "position_value": position_value}
        else:
            return {"status": "order_failed"}

    def run(self):
        self.init_binance()
        self.init_twitter_monitor()
        print("[START] Bot is ready. Monitoring Twitter and waiting for signals.", flush=True)
        logging.info("=== BOT STARTED (TWITTER + MANUAL MODE) ===")
        while True:
            time.sleep(60)


# ==================== HEALTH + MANUAL TEST SERVER ====================
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

bot = None

class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/test':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data)
                news = data.get('news', '')
            except:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'Invalid JSON')
                return

            if not news:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'Missing "news" field')
                return

            signal = get_sentiment(news, conflict_mode=CONFLICT_MODE)
            print(f"\n[MANUAL TEST] News: {news}")
            print(f"[SENTIMENT] {signal.value}")

            if signal == Signal.NEUTRAL:
                result = {"status": "neutral", "signal": "NEUTRAL"}
            else:
                if bot is None:
                    result = {"status": "error", "message": "Bot not initialized yet"}
                else:
                    trade_result = bot.execute_trade(signal)
                    result = {
                        "status": "trade_attempted",
                        "signal": signal.value,
                        "trade_result": trade_result
                    }

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
        else:
            self.send_response(404)
            self.end_headers()

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), RequestHandler)
    print(f"Health & test server running on port {port}")
    server.serve_forever()

if __name__ == "__main__":
    bot = OilBot()
    bot.init_binance()

    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()

    bot.run()
