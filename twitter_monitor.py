# twitter_monitor.py
import os
import time
import logging
import threading
import requests
import xml.etree.ElementTree as ET
import json
from datetime import datetime

class TwitterMonitor:
    def __init__(self, target_user, poll_interval=10):
        self.target_user = target_user
        self.poll_interval = poll_interval
        self.last_tweet_id = None
        self.running = False
        self.thread = None
        self.callback = None
        self.consecutive_errors = 0
        self.max_errors = 10
        
        # LAYER 1: Third-party RSS generators (most reliable)
        self.rss_generators = [
            f"https://seowebchecker.com/rss/feed-twitter?username={target_user}",
            f"https://www.feedspot.com/twitter-rss-feed-generator/?username={target_user}",
            f"https://rss.app/rss-feed/twitter-rss-feed-generator?username={target_user}",
        ]
        
        # LAYER 2: RSSHub public instances
        self.rsshub_instances = [
            f"https://rsshub.app/twitter/user/{target_user}",
            f"https://rsshub.rssforever.com/twitter/user/{target_user}",
        ]
        
        # LAYER 3: XCancel/Nitter forks (fallback)
        self.nitter_forks = [
            "https://xcancel.com",
            "https://nitter.privacyredirect.com",
            "https://nitter.tiekoetter.com",
        ]
        self.current_instance_index = 0

    def _fetch_rss_from_url(self, url):
        """Fetch and parse RSS from any URL."""
        try:
            logging.debug(f"[TWITTER] Fetching RSS from {url}")
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                try:
                    root = ET.fromstring(response.content)
                    entries = root.findall('.//item') or root.findall('.//{http://www.w3.org/2005/Atom}entry')
                    if entries:
                        return entries
                    else:
                        logging.warning(f"[TWITTER] No entries in feed from {url}")
                except ET.ParseError as e:
                    logging.warning(f"[TWITTER] XML parse error from {url}: {e}")
            else:
                logging.warning(f"[TWITTER] {url} returned status {response.status_code}")
            return None
        except Exception as e:
            logging.warning(f"[TWITTER] Failed to fetch from {url}: {e}")
            return None

    def _fetch_layer1_rss_generators(self):
        """Layer 1: Third-party RSS generators."""
        for url in self.rss_generators:
            entries = self._fetch_rss_from_url(url)
            if entries:
                logging.info("[TWITTER] Layer 1 (RSS generator) succeeded")
                return entries
            time.sleep(0.5)
        return None

    def _fetch_layer2_rsshub(self):
        """Layer 2: RSSHub public instances."""
        for url in self.rsshub_instances:
            entries = self._fetch_rss_from_url(url)
            if entries:
                logging.info("[TWITTER] Layer 2 (RSSHub) succeeded")
                return entries
            time.sleep(0.5)
        return None

    def _fetch_layer3_nitter(self):
        """Layer 3: Nitter forks (last resort)."""
        for attempt in range(len(self.nitter_forks)):
            instance = self.nitter_forks[self.current_instance_index]
            url = f"{instance}/{self.target_user}/rss"
            entries = self._fetch_rss_from_url(url)
            if entries:
                logging.info("[TWITTER] Layer 3 (Nitter fork) succeeded")
                return entries
            self.current_instance_index = (self.current_instance_index + 1) % len(self.nitter_forks)
            time.sleep(0.5)
        return None

    def _parse_entry(self, entry):
        """Parse RSS entry into tweet dict."""
        try:
            title_elem = entry.find('title') or entry.find('{http://www.w3.org/2005/Atom}title')
            link_elem = entry.find('link') or entry.find('{http://www.w3.org/2005/Atom}link')
            pub_elem = entry.find('pubDate') or entry.find('published') or entry.find('{http://www.w3.org/2005/Atom}published')
            
            title = title_elem.text.strip() if title_elem is not None and title_elem.text else ''
            link = link_elem.get('href') if link_elem is not None else ''
            if not link and link_elem is not None and link_elem.text:
                link = link_elem.text.strip()
            pub_date = pub_elem.text.strip() if pub_elem is not None and pub_elem.text else ''
            
            tweet_id = None
            if '/status/' in link:
                tweet_id = link.split('/status/')[-1]
            
            import html
            title = html.unescape(title)
            
            return {'id': tweet_id or str(int(time.time() * 1000)), 'text': title, 'created_at': pub_date, 'link': link}
        except Exception as e:
            logging.error(f"[TWITTER] Failed to parse entry: {e}")
            return None

    def get_latest_tweet(self):
        """Try all layers in order until one works."""
        # Layer 1: Third-party RSS generators
        entries = self._fetch_layer1_rss_generators()
        if entries:
            return self._parse_entry(entries[0])
        
        # Layer 2: RSSHub
        entries = self._fetch_layer2_rsshub()
        if entries:
            return self._parse_entry(entries[0])
        
        # Layer 3: Nitter forks
        entries = self._fetch_layer3_nitter()
        if entries:
            return self._parse_entry(entries[0])
        
        logging.warning("[TWITTER] All layers failed to fetch tweets")
        return None

    def start(self, callback):
        if not self.target_user:
            logging.error("[TWITTER] No target user specified.")
            return False
        
        self.callback = callback
        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        logging.info(f"[TWITTER] Started — polling @{self.target_user} every {self.poll_interval}s")
        logging.info(f"[TWITTER] Quadruple fallback: RSS Generators → RSSHub → Nitter forks")
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
