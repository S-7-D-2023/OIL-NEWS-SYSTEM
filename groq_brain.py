# groq_brain.py
import os
import time
import json
import requests

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# CORRECT MODEL — llama-3.1-8b-instant was retired 2026-08-16.
# Groq's official replacement: openai/gpt-oss-20b (free tier: 30 RPM, 1000 RPD).
GROQ_MODEL = "openai/gpt-oss-20b"

_consecutive_failures = 0
_groq_disabled_until = 0.0
_MAX_FAILURES = 3
_COOLDOWN_SECONDS = 300

SYSTEM_PROMPT = (
    "You are a crude oil trading analyst. You read one news headline and decide "
    "its directional impact on Brent crude oil (BZUSDT).\n\n"
    "Rules:\n"
    "- If the headline is NOT about oil, energy, or geopolitics affecting crude "
    "oil supply or demand, return signal NEUTRAL with strength 1.\n"
    "- If it is about oil and clearly bullish, return BULL.\n"
    "- If it is about oil and clearly bearish, return BEAR.\n"
    "- strength 1 = normal signal.\n"
    "- strength 2 = strong signal (large, clear, high-impact).\n"
    "- strength 3 = extreme signal (war, embargo, major supply shock).\n"
    "- confidence is a number between 0.0 and 1.0.\n\n"
    "Respond with a JSON object containing exactly these keys:\n"
    '{"signal": "BULL" | "BEAR" | "NEUTRAL", "strength": 1 | 2 | 3, '
    '"confidence": 0.0 to 1.0}\n'
    "No other text. JSON only."
)


def classify_headline(headline: str):
    """
    Returns dict {"signal": str, "strength": int, "confidence": float}
    or None if Groq is unavailable / failed / rate-limited.
    """
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
                "temperature": 0,
                "max_tokens": 60,
                "response_format": {"type": "json_object"}
            },
            timeout=5
        )

        if response.status_code != 200:
            _consecutive_failures += 1
            if _consecutive_failures >= _MAX_FAILURES:
                _groq_disabled_until = time.time() + _COOLDOWN_SECONDS
                print(f"[GROQ] Circuit breaker open for {_COOLDOWN_SECONDS}s.", flush=True)
            print(f"[GROQ] HTTP {response.status_code}: {response.text[:200]}", flush=True)
            return None

        content = response.json()["choices"][0]["message"]["content"].strip()
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

    except Exception as e:
        _consecutive_failures += 1
        if _consecutive_failures >= _MAX_FAILURES:
            _groq_disabled_until = time.time() + _COOLDOWN_SECONDS
            print(f"[GROQ] Circuit breaker open for {_COOLDOWN_SECONDS}s.", flush=True)
        print(f"[GROQ] classify failed: {e}", flush=True)
        return None
