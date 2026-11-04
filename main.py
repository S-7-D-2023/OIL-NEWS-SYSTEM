import nest_asyncio
nest_asyncio.apply()

import os
import time
import json
import logging
import sys
import re
import asyncio
import threading
import hmac
import hashlib
from datetime import datetime
import requests
from binance.client import Client as BinanceClient
from binance.enums import *
from binance.exceptions import BinanceAPIException
from binance import ThreadedWebsocketManager
from dotenv import load_dotenv
from sentiment import get_sentiment, Signal
from twitter_monitor import TwitterMonitor

sys.stdout.reconfigure(line_buffering=True)
load_dotenv()

print(f"[DEBUG] TWITTER_TARGET_USER: {os.getenv('TWITTER_TARGET_USER')}", flush=True)
print(f"[DEBUG] TWITTER_CT0 exists: {bool(os.getenv('TWITTER_CT0'))}", flush=True)

# ==================== CONFIG ====================
SYMBOL = os.getenv("SYMBOL", "BZUSDT")
FALLBACK_SYMBOL = os.getenv("FALLBACK_SYMBOL", "BZUSDT")

ENV_LEVERAGE = int(os.getenv("LEVERAGE", "20"))
LEVERAGE = 20 if ENV_LEVERAGE == 10 else ENV_LEVERAGE
if ENV_LEVERAGE == 10:
    print("⚠️  LEVERAGE env is 10 — forcing 20x.", flush=True)

POSITION_PERCENT = float(os.getenv("POSITION_PERCENT", "0.5"))
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "0"))
SL_PERCENT = float(os.getenv("SL_PERCENT", "0.10"))
TP_PERCENT = float(os.getenv("TP_PERCENT", "0.10"))
FEE_RATE = float(os.getenv("FEE_RATE", "0.0004"))
CONFLICT_MODE = os.getenv("CONFLICT_MODE", "BEAR_BIAS")

# NO INITIAL_CAPITAL — the bot fetches the real balance at trade time.

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET = os.getenv("BINANCE_SECRET")

logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')


def parse_ban_until_ms(exc):
    m = re.search(r'banned until (\d{13})', str(exc))
    return int(m.group(1)) if m else None


def safe_binance_call(func, *args, max_wait_sec=10, **kwargs):
    try:
        return func(*args, **kwargs)
    except BinanceAPIException as e:
        if e.code == -1003:
            ban_until_ms = parse_ban_until_ms(e)
            now_ms = int(time.time() * 1000)
            if ban_until_ms:
                wait_sec = max(1, (ban_until_ms - now_ms) // 1000 + 1)
                if wait_sec <= max_wait_sec:
                    print(f"[BAN] Short ban — waiting {wait_sec}s...", flush=True)
                    time.sleep(wait_sec)
                    return func(*args, **kwargs)
                raise RuntimeError(f"IP ban lasts {wait_sec}s — skipping call") from e
            raise RuntimeError("IP banned (no timestamp) — skipping call") from e
        raise


# ==================== FuturesAccount ====================
class FuturesAccount:
    def __init__(self):
        self.cash = 0.0
        self.position = 0.0
        self.entry_price = None
        self.margin_used = 0.0
        self.realized_pnl = 0.0
        self.leverage = LEVERAGE
        self.current_price = 0.0
        self.active_symbol = SYMBOL

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

    def set_real_balance(self, real_balance):
        self.cash = real_balance
        print(f"[ACCOUNT] Real balance locked: ${real_balance:.2f}", flush=True)

    def open_position(self, side, amount, price):
        position_value = amount * price
        margin_req = position_value / self.leverage
        if not self.can_open(margin_req):
            print("[ERROR] Not enough free margin.", flush=True)
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
        print(f"[ACCOUNT] Opened. Margin: ${margin_req:.2f} | Free: ${self.free_margin:.2f}", flush=True)
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
        print(f"[CLOSE LONG] PnL: ${pnl:.2f} | Fee: ${fee:.2f}", flush=True)

    def _close_short_internal(self, price):
        amount = -self.position
        pnl = amount * (self.entry_price - price)
        fee = amount * price * FEE_RATE
        self.cash += self.margin_used + pnl - fee
        self.margin_used = 0
        self.position = 0
        self.entry_price = None
        self.realized_pnl += pnl
        print(f"[CLOSE SHORT] PnL: ${pnl:.2f} | Fee: {fee:.2f}", flush=True)


# ==================== OIL BOT ====================
class OilBot:
    def __init__(self):
        self.client = None
        self.account = FuturesAccount()
        self.last_trade_time = 0
        self.active_symbol = SYMBOL
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

        self._ws_price = None
        self._ws_manager = None
        self._ws_stream_key = None

        self.twitter_monitor = None
        self.twitter_target = os.getenv("TWITTER_TARGET_USER")
        self.twitter_auth = os.getenv("TWITTER_AUTH_TOKEN") or os.getenv("TWITTER_AUTH_TOKEN_1")
        self.twitter_ct0 = os.getenv("TWITTER_CT0")
        self.twitter_interval = int(os.getenv("TWITTER_POLL_INTERVAL", "20"))

    # ---------- WEBSOCKET ----------
    def start_ws_price_stream(self):
        try:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            nest_asyncio.apply(loop)

            self._ws_manager = ThreadedWebsocketManager(
                api_key=BINANCE_API_KEY,
                api_secret=BINANCE_SECRET
            )
            self._ws_manager._loop = loop
            self._ws_manager.start()

            def handle_mark_price(msg):
                if isinstance(msg, dict) and msg.get('e') == 'markPriceUpdate':
                    try:
                        self._ws_price = float(msg['p'])
                    except (KeyError, ValueError):
                        pass

            self._ws_stream_key = self._ws_manager.start_symbol_mark_price_socket(
                symbol=self.active_symbol,
                callback=handle_mark_price
            )
            print(f"[WS] Mark price stream started for {self.active_symbol}", flush=True)
        except Exception as e:
            print(f"[WARN] WebSocket start failed: {e}", flush=True)
            self._ws_manager = None

    # ---------- REAL BALANCE FETCH (AT TRADE TIME) ----------
    def fetch_real_balance(self):
        """Fetch real balance from Binance. Returns float or None. No fake fallback."""
        for attempt in range(3):
            try:
                account = self.client.futures_account()
                balance = float(account.get('totalWalletBalance', 0))
                if balance > 0:
                    return balance
                else:
                    print(f"[WARN] Balance returned ${balance:.2f} — retrying...", flush=True)
            except BinanceAPIException as e:
                if e.code == -1003:
                    print("[BAN] Balance fetch banned — trade aborted (order would be banned too).", flush=True)
                    return None
                print(f"[WARN] Balance fetch attempt {attempt+1}: {e}", flush=True)
            except Exception as e:
                print(f"[WARN] Balance fetch attempt {attempt+1}: {e}", flush=True)
            if attempt < 2:
                time.sleep(2)
        return None

    # ---------- REST INIT (NO BALANCE FETCH) ----------
    def init_binance_rest(self):
        try:
            self.client = BinanceClient(BINANCE_API_KEY, BINANCE_SECRET)

            # Only fetch symbol filters at startup
            for attempt in range(5):
                try:
                    info = safe_binance_call(self.client.futures_exchange_info)
                    symbols = [s['symbol'] for s in info['symbols']]
                    if self.active_symbol not in symbols and FALLBACK_SYMBOL in symbols:
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
                    print(f"[DIAG] Live Futures OK. Symbols: {len(symbols)}", flush=True)
                    break
                except Exception as e:
                    print(f"[WARN] Exchange info attempt {attempt+1} failed: {e}", flush=True)
                    time.sleep(5)

            if self.min_qty is None:
                print("[WARN] Filters not fetched — using BZUSDT fallback values.", flush=True)
                self.min_qty = 0.01
                self.step_size = 0.01
                self.min_notional = 5.0

            print(f"[DIAG] Filters: minQty={self.min_qty}, stepSize={self.step_size}, minNotional={self.min_notional}", flush=True)

            # Leverage
            try:
                safe_binance_call(self.client.futures_change_leverage,
                                  symbol=self.active_symbol, leverage=LEVERAGE)
                print(f"[INIT] Leverage set to {LEVERAGE}x", flush=True)
            except Exception as e:
                print(f"[WARN] Leverage skipped: {e}", flush=True)

            # Margin type
            try:
                safe_binance_call(self.client.futures_change_margin_type,
                                  symbol=self.active_symbol, marginType='ISOLATED')
                print("[INIT] Margin type ISOLATED", flush=True)
            except Exception as e:
                if "No need to change margin type" in str(e):
                    print("[INIT] Margin type already ISOLATED", flush=True)
                else:
                    print(f"[WARN] Margin type skipped: {e}", flush=True)

            print(f"[INIT] REST init complete. Symbol: {self.active_symbol}", flush=True)

        except Exception as e:
            print(f"[ERROR] REST init failed: {e}", flush=True)

    # ---------- PRICE ----------
    def fetch_price(self):
        if self._ws_price is not None and self._ws_price > 0:
            return self._ws_price

        now = time.time()
        if self._price_cache and (now - self._price_cache_time) < self._price_cache_ttl:
            return self._price_cache
        for attempt in range(2):
            try:
                ticker = self.client.futures_symbol_ticker(symbol=self.active_symbol)
                price = float(ticker['price'])
                self._price_cache = price
                self._price_cache_time = time.time()
                return price
            except BinanceAPIException as e:
                if e.code == -1003:
                    print("[BAN] Price REST banned — using last WS price.", flush=True)
                    break
                if attempt < 1:
                    time.sleep(0.5)
            except Exception:
                if attempt < 1:
                    time.sleep(0.5)
        return self._price_cache

    # ---------- ORDERS ----------
    def place_market_order(self, side, quantity):
        if self.step_size:
            quantity = round(quantity / self.step_size) * self.step_size
        quantity = round(quantity, 8)
        for attempt in range(3):
            try:
                order = self.client.futures_create_order(
                    symbol=self.active_symbol, side=side,
                    type=FUTURE_ORDER_TYPE_MARKET, quantity=quantity)
                print(f"[MARKET ORDER] {order}", flush=True)
                return order
            except BinanceAPIException as e:
                if e.code == -1003:
                    print("[BAN] Market order banned.", flush=True)
                    return None
                print(f"[MARKET ORDER ERROR] {attempt+1}: {e}", flush=True)
                if attempt < 2:
                    time.sleep(0.5)
                else:
                    return None
            except Exception as e:
                print(f"[MARKET ORDER ERROR] {attempt+1}: {e}", flush=True)
                if attempt < 2:
                    time.sleep(0.5)
                else:
                    return None
        return None

    def place_stop_order(self, side, quantity, stop_price):
        if self.step_size:
            quantity = round(quantity / self.step_size) * self.step_size
        quantity = round(quantity, 8)
        for attempt in range(3):
            try:
                order = self.client.futures_create_algo_order(
                    symbol=self.active_symbol, side=side,
                    type=FUTURE_ORDER_TYPE_STOP_MARKET,
                    quantity=quantity,
                    triggerPrice=round(stop_price, 2),
                    workingType='MARK_PRICE',
                    reduceOnly=True,
                    algoType='CONDITIONAL',
                    priceProtect=True)
                print(f"[STOP LOSS] ✅ AlgoId: {order.get('algoId', 'N/A')} | Trigger: ${stop_price:.2f}", flush=True)
                return order
            except BinanceAPIException as e:
                if e.code == -1003:
                    print("[BAN] Stop order banned.", flush=True)
                    return None
                print(f"[STOP ORDER ERROR] {attempt+1}: {e}", flush=True)
                if attempt < 2:
                    time.sleep(0.5)
                else:
                    return None
            except Exception as e:
                print(f"[STOP ORDER ERROR] {attempt+1}: {e}", flush=True)
                if attempt < 2:
                    time.sleep(0.5)
                else:
                    return None
        return None

    def place_tp_order(self, side, quantity, tp_price):
        if self.step_size:
            quantity = round(quantity / self.step_size) * self.step_size
        quantity = round(quantity, 8)
        for attempt in range(3):
            try:
                order = self.client.futures_create_algo_order(
                    symbol=self.active_symbol, side=side,
                    type=FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
                    quantity=quantity,
                    triggerPrice=round(tp_price, 2),
                    workingType='MARK_PRICE',
                    reduceOnly=True,
                    algoType='CONDITIONAL',
                    priceProtect=True)
                print(f"[TAKE PROFIT] ✅ AlgoId: {order.get('algoId', 'N/A')} | Trigger: ${tp_price:.2f}", flush=True)
                return order
            except BinanceAPIException as e:
                if e.code == -1003:
                    print("[BAN] TP order banned.", flush=True)
                    return None
                print(f"[TP ORDER ERROR] {attempt+1}: {e}", flush=True)
                if attempt < 2:
                    time.sleep(0.5)
                else:
                    return None
            except Exception as e:
                print(f"[TP ORDER ERROR] {attempt+1}: {e}", flush=True)
                if attempt < 2:
                    time.sleep(0.5)
                else:
                    return None
        return None

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
            print(f"[WARN] Position sync skipped: {e}", flush=True)

    # ---------- TWITTER ----------
    def init_twitter_monitor(self):
        if not self.twitter_target or not self.twitter_auth or not self.twitter_ct0:
            print("[TWITTER] Credentials missing. Monitor disabled.", flush=True)
            return
        self.twitter_monitor = TwitterMonitor(
            target_user=self.twitter_target,
            auth_token=self.twitter_auth,
            ct0=self.twitter_ct0,
            poll_interval=self.twitter_interval)

        def on_new_tweet(tweet):
            text = tweet.get('text', '')
            print(f"\n[TWITTER] NEW TWEET: {text[:120]}...", flush=True)
            signal = get_sentiment(text, conflict_mode=CONFLICT_MODE)
            print(f"[SENTIMENT] {signal.value}", flush=True)
            if signal == Signal.NEUTRAL:
                print("[SKIP] Neutral — no trade.", flush=True)
                return
            self.execute_trade(signal)

        if self.twitter_monitor.start(on_new_tweet):
            print(f"[TWITTER] Monitoring @{self.twitter_target} every {self.twitter_interval}s", flush=True)
        else:
            print("[TWITTER] Failed to start.", flush=True)

    # ---------- TRADE ----------
    def execute_trade(self, signal):
        now = time.time()
        if now - self.last_trade_time < COOLDOWN_SECONDS:
            print("[COOLDOWN]", flush=True)
            return

        self.sync_position_from_exchange()
        if self.account.position != 0:
            print(f"[SKIP] Position already open. Ignoring {signal.value}.", flush=True)
            return

        # FETCH REAL BALANCE NOW — no fake number, no startup cache
        balance = self.fetch_real_balance()
        if balance is None:
            print("[SKIP] Real balance unavailable — trade aborted to avoid fake sizing.", flush=True)
            return
        self.account.set_real_balance(balance)

        price = self.fetch_price()
        if price is None:
            print("[ERROR] No price available.", flush=True)
            return

        self.account.update_price(price)
        equity = self.account.total_equity
        target_margin = equity * POSITION_PERCENT
        desired_value = target_margin * LEVERAGE
        quantity = desired_value / price

        if self.step_size:
            quantity = round(quantity / self.step_size) * self.step_size
        quantity = round(quantity, 8)
        position_value = quantity * price

        if self.min_notional and position_value < self.min_notional:
            print(f"[ERROR] Position ${position_value:.2f} below min notional ${self.min_notional:.2f}", flush=True)
            return
        if self.min_qty and quantity < self.min_qty:
            print(f"[ERROR] Quantity {quantity} below min {self.min_qty}", flush=True)
            return

        margin_req = position_value / LEVERAGE
        free_margin = self.account.free_margin
        max_margin = free_margin * 0.98
        max_value = max_margin * LEVERAGE
        max_qty = max_value / price
        if self.step_size:
            max_qty = round(max_qty / self.step_size) * self.step_size
        max_qty = round(max_qty, 8)
        max_value = max_qty * price

        print(f"[MARGIN] price={price:.2f} equity={equity:.2f} target_margin={target_margin:.2f} position={position_value:.2f} margin_req={margin_req:.2f} free={free_margin:.2f}", flush=True)

        if position_value > max_value:
            print(f"[MARGIN] Reducing to ${max_value:.2f}", flush=True)
            quantity = max_qty
            position_value = max_value

        if position_value < self.min_notional or quantity < self.min_qty:
            print("[ERROR] After reduction below minimum. Cannot trade.", flush=True)
            return

        print(f"[TRADE] {signal.value} | Price ${price:.2f} | Equity ${equity:.2f} | Margin ${position_value/LEVERAGE:.2f} | Position ${position_value:.2f} | Size {quantity:.8f}", flush=True)

        if signal == Signal.BULL:
            side, sl_side = SIDE_BUY, SIDE_SELL
            sl_price = price * (1 - SL_PERCENT)
            tp_price = price * (1 + TP_PERCENT)
        else:
            side, sl_side = SIDE_SELL, SIDE_BUY
            sl_price = price * (1 + SL_PERCENT)
            tp_price = price * (1 - TP_PERCENT)

        market_ok = self.place_market_order(side, quantity)
        if market_ok:
            self.sl_price = sl_price
            self.tp_price = tp_price
            sl_ok = self.place_stop_order(sl_side, quantity, sl_price)
            tp_ok = self.place_tp_order(sl_side, quantity, tp_price)
            if not sl_ok or not tp_ok:
                print("[WARN] Algo SL/TP failed — manual monitor starting.", flush=True)
                self.stop_monitoring = False
                if self.monitor_thread is None or not self.monitor_thread.is_alive():
                    self.monitor_thread = threading.Thread(target=self.monitor_position, daemon=True)
                    self.monitor_thread.start()
            self.account.open_position("BUY" if side == SIDE_BUY else "SELL", quantity, price)
            self.last_trade_time = time.time()

    def monitor_position(self):
        print("[MONITOR] Manual SL/TP thread started.", flush=True)
        while not self.stop_monitoring:
            if self.account.position == 0:
                break
            price = self.fetch_price()
            if price is None:
                time.sleep(0.5)
                continue
            self.account.update_price(price)
            if self.account.position > 0:
                if price <= self.sl_price:
                    print(f"[SL HIT] Long at ${price:.2f}", flush=True)
                    self.place_market_order(SIDE_SELL, abs(self.account.position))
                    self.account.close_position(price)
                    break
                elif price >= self.tp_price:
                    print(f"[TP HIT] Long at ${price:.2f}", flush=True)
                    self.place_market_order(SIDE_SELL, abs(self.account.position))
                    self.account.close_position(price)
                    break
            elif self.account.position < 0:
                if price >= self.sl_price:
                    print(f"[SL HIT] Short at ${price:.2f}", flush=True)
                    self.place_market_order(SIDE_BUY, abs(self.account.position))
                    self.account.close_position(price)
                    break
                elif price <= self.tp_price:
                    print(f"[TP HIT] Short at ${price:.2f}", flush=True)
                    self.place_market_order(SIDE_BUY, abs(self.account.position))
                    self.account.close_position(price)
                    break
            time.sleep(0.5)
        print("[MONITOR] Thread ended.", flush=True)


# ==================== HEALTH SERVER ====================
from http.server import HTTPServer, BaseHTTPRequestHandler

bot = None

class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        path = getattr(self, 'path', None)
        if path in ('/health', '/'):
            return
        super().log_message(format, *args)

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
            length = int(self.headers['Content-Length'])
            data = self.rfile.read(length)
            try:
                payload = json.loads(data)
                news = payload.get('news', '')
            except:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'Invalid JSON')
                return
            if not news:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'Missing "news"')
                return
            signal = get_sentiment(news, conflict_mode=CONFLICT_MODE)
            print(f"\n[MANUAL TEST] {news}", flush=True)
            print(f"[SENTIMENT] {signal.value}", flush=True)
            if signal != Signal.NEUTRAL and bot:
                bot.execute_trade(signal)
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"signal": signal.value}).encode())
        else:
            self.send_response(404)
            self.end_headers()

def run_health():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), RequestHandler)
    print(f"Health server on port {port}", flush=True)
    server.serve_forever()


# ==================== STARTUP ====================
if __name__ == "__main__":
    threading.Thread(target=run_health, daemon=True).start()
    print("[START] Health server started.", flush=True)

    bot = OilBot()
    bot.start_ws_price_stream()
    threading.Thread(target=bot.init_binance_rest, daemon=True).start()
    bot.init_twitter_monitor()

    while True:
        time.sleep(60)
