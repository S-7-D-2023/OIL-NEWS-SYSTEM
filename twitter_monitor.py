# twitter_monitor.py
import os
import time
import logging
import threading
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

class TwitterMonitor:
    def __init__(self, target_user, poll_interval=10):
        self.target_user = target_user
        self.poll_interval = poll_interval
        self.last_tweet_id = None
        self.last_tweet_time = None
        self.running = False
        self.thread = None
        self.callback = None
        self.consecutive_errors = 0
        self.max_errors = 10
        
        # Public Nitter instances – free, no auth, no rate limits
        self.nitter_instances = [
            "https://nitter.net",
            "https://nitter.poast.org",
            "https://nitter.lunar.icu",
            "https://nitter.kavin.rocks",
            "https://nitter.1d4.us",
            "https://nitter.space",
            "https://nitter.nl",
            "https://nitter.mint.lgbt",
        ]
        self.current_instance_index = 0

    def get_next_nitter_instance(self):
        instance = self.nitter_instances[self.current_instance_index]
        self.current_instance_index = (self.current_instance_index + 1) % len(self.nitter_instances)
        return instance

    def fetch_rss(self):
        for attempt in range(len(self.nitter_instances)):
            instance = self.nitter_instances[self.current_instance_index]
            url = f"{instance}/{self.target_user}/rss"
            try:
                logging.debug(f"[TWITTER] Fetching RSS from {url}")
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                response = requests.get(url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    # Parse XML manually
                    try:
                        root = ET.fromstring(response.content)
                        # RSS namespace
                        ns = {'': 'http://www.w3.org/2005/Atom'}  # Nitter uses Atom
                        # Try to find entries
                        entries = root.findall('.//{http://www.w3.org/2005/Atom}entry')
                        if not entries:
                            # Fallback to RSS 2.0 <item>
                            entries = root.findall('.//item')
                        if entries:
                            # Return the feed and entries list
                            return {'entries': entries, 'namespace': ns}
                        else:
                            logging.warning(f"[TWITTER] No entries found in feed from {instance}")
                    except ET.ParseError as e:
                        logging.warning(f"[TWITTER] XML parse error from {instance}: {e}")
                else:
                    logging.warning(f"[TWITTER] RSS feed from {instance} returned status {response.status_code}")
                
                # Rotate to next instance
                self.current_instance_index = (self.current_instance_index + 1) % len(self.nitter_instances)
                
            except Exception as e:
                logging.warning(f"[TWITTER] Failed to fetch from {instance}: {e}")
                self.current_instance_index = (self.current_instance_index + 1) % len(self.nitter_instances)
                time.sleep(0.5)
        
        return None

    def get_latest_tweet(self):
        try:
            result = self.fetch_rss()
            if not result:
                return None
            entries = result['entries']
            if not entries:
                return None
            
            # Take the first entry (newest)
            entry = entries[0]
            
            # Extract title and link
            title_elem = entry.find('title') or entry.find('{http://www.w3.org/2005/Atom}title')
            link_elem = entry.find('link') or entry.find('{http://www.w3.org/2005/Atom}link')
            pub_elem = entry.find('published') or entry.find('pubDate') or entry.find('{http://www.w3.org/2005/Atom}published')
            
            title = title_elem.text.strip() if title_elem is not None and title_elem.text else ''
            link = link_elem.get('href') if link_elem is not None else ''
            if not link and link_elem is not None and link_elem.text:
                link = link_elem.text.strip()
            pub_date = pub_elem.text.strip() if pub_elem is not None and pub_elem.text else ''
            
            # Extract tweet ID from link (e.g., /username/status/123456789)
            tweet_id = None
            if '/status/' in link:
                tweet_id = link.split('/status/')[-1]
            
            if not title and not link:
                return None
            
            # Clean title from HTML entities (optional)
            import html
            title = html.unescape(title)
            
            return {
                'id': tweet_id or str(int(time.time() * 1000)),
                'text': title,
                'created_at': pub_date,
                'link': link
            }
        except Exception as e:
            logging.error(f"[TWITTER] RSS parsing error: {e}")
            return None

    def start(self, callback):
        if not self.target_user:
            logging.error("[TWITTER] No target user specified.")
            return False
        
        self.callback = callback
        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        logging.info(f"[TWITTER] Started — polling @{self.target_user} via Nitter every {self.poll_interval}s")
        logging.info(f"[TWITTER] Nitter instances: {len(self.nitter_instances)} (will rotate on failure)")
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
                    tweet_text = tweet.get('text', '')
                    
                    if tweet_id and tweet_id != self.last_tweet_id:
                        logging.info(f"[TWITTER] NEW TWEET from @{self.target_user}: {tweet_text[:100]}...")
                        if self.callback:
                            self.callback(tweet)
                        self.last_tweet_id = tweet_id
                        self.last_tweet_time = time.time()
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
                    backoff = min(self.poll_interval * 3, 60)
                    logging.warning(f"[TWITTER] Backing off for {backoff}s due to {consecutive_errors} errors.")
                    time.sleep(backoff)
                
                time.sleep(self.poll_interval)
            except Exception as e:
                logging.error(f"[TWITTER] Poll loop error: {e}")
                consecutive_errors += 1
                backoff = min(self.poll_interval * (2 ** min(consecutive_errors, 5)), 60)
                time.sleep(backoff)
