# twitter_monitor.py
import os
import time
import logging
import threading
import json
import tempfile
from Scweet import Scweet

class TwitterMonitor:
    def __init__(self, target_user, poll_interval=2):
        self.target_user = target_user
        self.poll_interval = poll_interval
        self.last_tweet_id = None
        self.running = False
        self.thread = None
        self.callback = None
        self.scweet = None
        self.consecutive_errors = 0
        self.max_errors = 10

    def _get_auth_tokens_from_env(self):
        """Read all TWITTER_AUTH_TOKEN_1, _2, _3... from environment and return list."""
        tokens = []
        i = 1
        while True:
            token = os.getenv(f"TWITTER_AUTH_TOKEN_{i}")
            if token:
                logging.info(f"[TWITTER] Found token #{i} (length: {len(token)} chars)")
                tokens.append(token)
                i += 1
            else:
                break
        
        # If no numbered tokens, fallback to the single TWITTER_AUTH_TOKEN
        if not tokens:
            single = os.getenv("TWITTER_AUTH_TOKEN")
            if single:
                logging.info(f"[TWITTER] Found single TWITTER_AUTH_TOKEN (length: {len(single)} chars)")
                tokens.append(single)
            else:
                logging.error("[TWITTER] No auth tokens found in environment. Check your Render environment variables.")
        
        logging.info(f"[TWITTER] Total tokens loaded: {len(tokens)}")
        return tokens

    def _build_cookies_json(self, tokens):
        """Build a list of account dicts for Scweet multi-account format."""
        accounts = []
        for idx, token in enumerate(tokens, start=1):
            accounts.append({
                "username": f"account_{idx}",
                "cookies": {
                    "auth_token": token
                }
            })
        return accounts

    def init_scweet(self):
        """Initialize Scweet with either single auth_token or multi-account from env."""
        try:
            tokens = self._get_auth_tokens_from_env()
            if not tokens:
                logging.error("[TWITTER] No auth tokens found in environment.")
                return False

            if len(tokens) == 1:
                # Single account – use auth_token directly
                self.scweet = Scweet(auth_token=tokens[0])
                logging.info(f"[TWITTER] Scweet initialized with single account (total=1)")
            else:
                # Multiple accounts – write temp cookies.json and use multi-account
                accounts = self._build_cookies_json(tokens)
                with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                    json.dump(accounts, f, indent=2)
                    temp_path = f.name
                self.scweet = Scweet(cookies_file=temp_path)
                logging.info(f"[TWITTER] Scweet initialized with multi-account pool (total={len(tokens)})")
                self._temp_file = temp_path

            logging.info(f"[TWITTER] Monitoring @{self.target_user}")
            return True
        except Exception as e:
            logging.error(f"[TWITTER] Failed to init Scweet: {e}")
            return False

    def get_latest_tweet(self):
        """Get latest tweet using Scweet's GraphQL API with auto-account rotation."""
        try:
            tweets = self.scweet.search(
                f"from:{self.target_user}",
                since="2026-01-01",
                limit=1,
                save=False
            )
            if tweets and len(tweets) > 0:
                tweet = tweets[0]
                logging.debug(f"[TWITTER] Scweet found tweet: {tweet.get('text', '')[:50]}...")
                return {
                    'id': tweet.get('id'),
                    'text': tweet.get('text', ''),
                    'created_at': tweet.get('created_at')
                }
            return None
        except Exception as e:
            error_msg = str(e)
            logging.error(f"[TWITTER] Scweet search error: {error_msg}")
            if "No eligible accounts" in error_msg:
                logging.warning("[TWITTER] All accounts rate-limited. Waiting longer...")
            return None

    def start(self, callback):
        if not self.init_scweet():
            logging.error("[TWITTER] Failed to initialize Scweet. Twitter monitor disabled.")
            return False
        
        self.callback = callback
        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        logging.info(f"[TWITTER] Started — polling @{self.target_user} every {self.poll_interval}s")
        return True

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)

    def _poll_loop(self):
        consecutive_errors = 0
        while self.running:
            try:
                logging.debug(f"[TWITTER] Polling @{self.target_user}...")
                tweet = self.get_latest_tweet()
                if tweet:
                    tweet_id = tweet.get('id')
                    if tweet_id and tweet_id != self.last_tweet_id:
                        tweet_text = tweet.get('text', '')
                        logging.info(f"[TWITTER] NEW TWEET from @{self.target_user}: {tweet_text[:100]}...")
                        if self.callback:
                            self.callback(tweet)
                        self.last_tweet_id = tweet_id
                        consecutive_errors = 0
                    else:
                        logging.debug("[TWITTER] No new tweet.")
                else:
                    consecutive_errors += 1
                    if consecutive_errors == 1:
                        logging.info("[TWITTER] No tweet fetched this cycle.")
                    elif consecutive_errors > 5:
                        logging.warning(f"[TWITTER] {consecutive_errors} consecutive empty polls.")
                
                if consecutive_errors > self.max_errors:
                    backoff = min(self.poll_interval * 3, 120)
                    logging.warning(f"[TWITTER] Backing off for {backoff}s due to {consecutive_errors} errors.")
                    time.sleep(backoff)
                
                time.sleep(self.poll_interval)
            except Exception as e:
                logging.error(f"[TWITTER] Poll loop error: {e}")
                consecutive_errors += 1
                backoff = min(self.poll_interval * (2 ** min(consecutive_errors, 5)), 120)
                time.sleep(backoff)
