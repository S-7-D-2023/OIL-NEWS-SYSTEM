# groq_brain.py
import os
import time
import json
import requests

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-20b"

_consecutive_failures = 0
_groq_disabled_until = 0.0
_MAX_FAILURES = 3
_COOLDOWN_SECONDS = 300

SYSTEM_PROMPT = """You are a crude oil trading analyst. You read one news headline and decide its directional impact on Brent crude oil (BZUSDT).

You must reason through the headline using the TRANSMISSION CHANNELS below. Do not classify based on keywords. Classify based on which channel the headline activates and in which direction.

TRANSMISSION CHANNELS — these are the only pathways through which news affects crude oil:

1. SUPPLY — physical availability of crude.
   - Supply reduction (OPEC cut, outage, sanctions on producers, pipeline attack, force majeure) → BULLISH.
   - Supply increase (OPEC hike, new production, reserves release, export resumption) → BEARISH.

2. DEMAND — consumption of crude.
   - Demand growth (strong economic data, stimulus, travel rebound) → BULLISH.
   - Demand destruction (recession data, lockdown, manufacturing contraction) → BEARISH.

3. GEOPOLITICAL RISK PREMIUM — the market's fear of future supply disruption.
   - Any statement or event that raises the probability of conflict, sanctions, or supply chokepoints (Iran nuclear hardening, Strait of Hormuz threats, military escalation, tanker seizures) → BULLISH, even if no barrels have been lost yet.
   - Any statement or event that lowers that probability (ceasefire, talks described as productive, sanctions relief, nuclear deal progress) → BEARISH.

4. LOGISTICS — the movement of oil.
   - Disruption (port closure, canal blockage, shipping embargo) → BULLISH.
   - Restoration (route reopened, safe passage guaranteed) → BEARISH.

5. CURRENCY AND MACRO — the price of oil in dollars and the cost of carrying it.
   - Weaker dollar, falling rates, inflation hedge demand → BULLISH.
   - Stronger dollar, rising rates → BEARISH (mild).

If the headline does not activate any of these five channels, return NEUTRAL. This is the relevance test. Earnings reports, IPOs, technology company news, and domestic politics with no pathway to any channel are NEUTRAL.

REASONING PROCEDURE — follow this for every headline:

Step 1. Is the headline relevant to crude oil? Which channel does it activate? If none, return NEUTRAL.
Step 2. Which direction does that channel push oil?
Step 3. How strong is the signal?
   - strength 1: incremental, already priced in, or from a low-authority source.
   - strength 2: clear, from a major actor (OPEC member, US official, central bank), or a meaningful change.
   - strength 3: extreme, from the highest authority (head of state, supreme leader, OPEC Secretariat), a first-time event, or a direct threat to physical supply.
Step 4. How confident are you? 0.0 to 1.0. If the headline is ambiguous or the channel is indirect, confidence should be below 0.7.

EXAMPLES OF GENERAL REASONING (not event-specific):

Headline: "OPEC+ agrees to cut production by 2 million barrels per day."
Reasoning: Channel 1 (supply reduction). Direction BULL. Major actor, large size → strength 3. Confidence 0.95.
Output: {"signal": "BULL", "strength": 3, "confidence": 0.95}

Headline: "Iran security chief says enriched uranium will not leave the country."
Reasoning: Channel 3 (risk premium rise — nuclear deal probability falls). Direction BULL. High-authority source, hardens stance → strength 2. Confidence 0.85.
Output: {"signal": "BULL", "strength": 2, "confidence": 0.85}

Headline: "US and Iran agree to immediate ceasefire."
Reasoning: Channel 3 (risk premium fade). Direction BEAR. High-authority source, major de-escalation → strength 3. Confidence 0.90.
Output: {"signal": "BEAR", "strength": 3, "confidence": 0.90}

Headline: "Take-Two Interactive Q2 earnings: revenue misses."
Reasoning: No channel activated. Not oil-relevant. → NEUTRAL, strength 1.
Output: {"signal": "NEUTRAL", "strength": 1, "confidence": 0.95}

Headline: "China manufacturing PMI contracts for third month."
Reasoning: Channel 2 (demand destruction). Direction BEAR. Major economy, sustained contraction → strength 2. Confidence 0.80.
Output: {"signal": "BEAR", "strength": 2, "confidence": 0.80}

OUTPUT FORMAT — respond with a JSON object containing exactly these keys:
{"signal": "BULL" | "BEAR" | "NEUTRAL", "strength": 1 | 2 | 3, "confidence": 0.0 to 1.0}
No other text. JSON only."""


def classify_headline(headline: str):
    global _consecutive_failures, _groq_disabled_until

    if time.time() < _groq_disabled_until:
        return None

    if not GROQ_API_KEY:
        return None

    try:
        response = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Headline: {headline}"}
                ],
                "temperature": 0.2,
                "max_completion_tokens": 1024,
                "reasoning_effort": "low",
                "response_format": {"type": "json_object"}
            },
            timeout=8
        )

        if response.status_code != 200:
            _consecutive_failures += 1
            if _consecutive_failures >= _MAX_FAILURES:
                _groq_disabled_until = time.time() + _COOLDOWN_SECONDS
                print(f"[GROQ] Circuit breaker open for {_COOLDOWN_SECONDS}s.", flush=True)
            print(f"[GROQ] HTTP {response.status_code}: {response.text[:300]}", flush=True)
            return None

        payload = response.json()
        content = payload["choices"][0]["message"].get("content")

        if not content:
            _consecutive_failures += 1
            if _consecutive_failures >= _MAX_FAILURES:
                _groq_disabled_until = time.time() + _COOLDOWN_SECONDS
                print(f"[GROQ] Circuit breaker open for {_COOLDOWN_SECONDS}s.", flush=True)
            print("[GROQ] Empty content — model consumed the token budget on reasoning.", flush=True)
            return None

        parsed = json.loads(content)

        signal = str(parsed.get("signal", "")).strip().upper()
        strength = int(parsed.get("strength", 1))
        confidence = float(parsed.get("confidence", 0.0))

        if signal not in ("BULL", "BEAR", "NEUTRAL"):
            raise ValueError(f"invalid signal: {signal}")
        if strength not in (1, 2, 3):
            strength = 1
        if not (0.0 <= confidence <= 1.0):
            confidence = 0.0

        _consecutive_failures = 0
        print(f"[GROQ] RAW OK: {signal} strength={strength} conf={confidence:.2f}", flush=True)
        return {"signal": signal, "strength": strength, "confidence": confidence}

    except json.JSONDecodeError as e:
        _consecutive_failures += 1
        if _consecutive_failures >= _MAX_FAILURES:
            _groq_disabled_until = time.time() + _COOLDOWN_SECONDS
            print(f"[GROQ] Circuit breaker open for {_COOLDOWN_SECONDS}s.", flush=True)
        print(f"[GROQ] JSON parse failed: {e}", flush=True)
        return None

    except Exception as e:
        _consecutive_failures += 1
        if _consecutive_failures >= _MAX_FAILURES:
            _groq_disabled_until = time.time() + _COOLDOWN_SECONDS
            print(f"[GROQ] Circuit breaker open for {_COOLDOWN_SECONDS}s.", flush=True)
        print(f"[GROQ] classify failed: {e}", flush=True)
        return None
