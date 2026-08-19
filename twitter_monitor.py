import os
import time
import logging
import threading
import requests
import json

class TwitterMonitor:
    def __init__(self, target_user, auth_token, poll_interval=30):
        self.target_user = target_user
        self.auth_token = auth_token
        self.poll_interval = poll_interval
        self.last_tweet_id = None
        self.running = False
        self.thread = None
        self.callback = None
        self.reset_count = 0
        self.max_resets = 3
        self.consecutive_errors = 0

    def get_latest_tweet_direct(self):
        """Fetch latest tweet using direct API request with auth_token."""
        try:
            # Use the unofficial user timeline endpoint
            url = f"https://api.x.com/1.1/statuses/user_timeline.json?screen_name={self.target_user}&count=1"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
            }
            cookies = {"auth_token": self.auth_token}
            response = requests.get(url, headers=headers, cookies=cookies, timeout=10)
            logging.info(f"Direct API response status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                if data and isinstance(data, list) and len(data) > 0:
                    tweet = data[0]
                    return {
                        'id': tweet.get('id_str'),
                        'text': tweet.get('text', ''),
                        'created_at': tweet.get('created_at')
                    }
                else:
                    logging.warning("Direct API returned empty list.")
                    return None
            elif response.status_code == 429:
                # Rate limited
                logging.error("Direct API rate limited (429). Waiting longer.")
                return None
            elif response.status_code == 401:
                logging.error("Direct API unauthorized (401) – auth_token may be invalid or expired.")
                # Trigger reset? No, token is bad; need user to get new token.
                return None
            else:
                logging.error(f"Direct API error: {response.status_code} - {response.text[:200]}")
                return None
        except Exception as e:
            logging.error(f"Direct API exception: {e}")
            return None

    def get_latest_tweet_scweet(self):
        """Fallback: use Scweet (only if direct fails)."""
        try:
            if not hasattr(self, 'scweet') or self.scweet is None:
                from Scweet import Scweet
                self.scweet = Scweet(auth_token=self.auth_token)
            tweets = self.scweet.search(
                f"from:{self.target_user}",
                since="2026-01-01",
                limit=1,
                save=False
            )
            if tweets and len(tweets) > 0:
                return tweets[0]
            return None
        except Exception as e:
            logging.error(f"Scweet fallback error: {e}")
            return None

    def get_latest_tweet(self):
        """Primary method: direct API, fallback to Scweet if direct fails."""
        tweet = self.get_latest_tweet_direct()
        if tweet:
            return tweet
        logging.warning("Direct API failed, trying Scweet fallback...")
        return self.get_latest_tweet_scweet()

    def start(self, callback):
        if not self.auth_token:
            logging.error("No auth_token provided. Twitter monitor disabled.")
            return False
        self.callback = callback
        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        logging.info(f"TwitterMonitor started — polling @{self.target_user} every {self.poll_interval}s")
        return True

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)

    def _poll_loop(self):
        consecutive_errors = 0
        while self.running:
            try:
                tweet = self.get_latest_tweet()
                if tweet:
                    tweet_id = tweet.get('id')
                    if tweet_id and tweet_id != self.last_tweet_id:
                        logging.info(f"NEW TWEET from @{self.target_user}: {tweet.get('text', '')[:100]}...")
                        if self.callback:
                            self.callback(tweet)
                        self.last_tweet_id = tweet_id
                        consecutive_errors = 0
                    else:
                        logging.debug("No new tweet.")
                else:
                    logging.warning("No tweet fetched this cycle.")
                    consecutive_errors += 1
                # Backoff on repeated errors
                if consecutive_errors > 3:
                    backoff = min(self.poll_interval * 2, 60)
                    logging.info(f"Backing off for {backoff}s due to consecutive errors")
                    time.sleep(backoff)
                time.sleep(self.poll_interval)
            except Exception as e:
                logging.error(f"Poll loop error: {e}")
                consecutive_errors += 1
                backoff = min(self.poll_interval * (2 ** consecutive_errors), 60)
                logging.info(f"Backing off for {backoff}s")
                time.sleep(backoff)
