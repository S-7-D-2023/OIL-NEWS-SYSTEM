import re
from enum import Enum

class Signal(Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    NEUTRAL = "NEUTRAL"

# ----------------------------------------------------------------------
# 1. MULTI‑WORD PHRASES (exact substring match)
# ----------------------------------------------------------------------
BULL_PHRASES = [
    "production cut", "output cut", "supply cut", "production halt",
    "supply disruption", "export ban", "pipeline attack", "refinery fire",
    "war premium", "military escalation", "blockade", "embargo",
    "sanctions tighten", "OPEC cut", "OPEC+ cut", "output reduction",
    "supply squeeze", "geopolitical risk", "missile strike", "drone attack",
    "Saudi facility attack", "Gulf of Mexico shutdown", "Venezuela collapse",
    "Libya output loss", "Nigeria force majeure", "force majeure",
    "hurricane disrupt", "storm shut", "freeze off", "civil unrest",
    "shut oil", "shut major", "shut production", "shuts production",
    "suspends production", "suspended production", "suspension of production",
    "closes oil", "closes port", "closes exports", "shut down pipeline",
    "seizes tanker", "seizes oil", "hijacks tanker",
    "cuts production", "cutting production", "cut output", "cutting output",
    "reduces output", "reducing output", "curtails production",
    "pipeline blast", "terminal explosion", "storage tank fire",
    "oil field attacked", "field shut", "platform evacuate",
    "port blockade", "shipping disrupted", "strait closed",
    "imports record", "record imports", "strategic purchase",
    "all-time high", "highest level", "raises demand forecast",
    "raises growth forecast", "raises economic outlook",
    "growth forecast raised", "demand forecast raised",
    "infrastructure program", "stimulus program", "economic expansion",
    "travel reaches", "gasoline demand hits",
    "inventories fall", "crude inventories drop", "inventories decline",
    "crude stocks decline", "inventories draw", "unexpected drop in inventories",
    "gasoline inventories fall",
    "preparing to bombard", "prepare to strike", "prepare to attack",
    "hitting them hard", "hit them hard", "strike hard",
    "denies talks", "denied talks", "deny talks",
    "military action", "suspends cooperation", "suspend cooperation",
    "spare capacity fallen", "spare capacity fell", "spare capacity low",
    "production capacity fallen", "production capacity fell",
    "nonfarm payrolls exceed", "payrolls beat", "payrolls exceed",
    "exceed expectations",
    "threatens shipping", "military escorts", "deploys warships",
    "naval exercises", "military exercises", "warns ships to avoid",
    "commercial ships avoid", "military options remain available",
    "military options", "retaliation", "immediate retaliation",
    "hostile"
]

BEAR_PHRASES = [
    "peace deal", "ceasefire", "truce", "talks progress", "agreement reached",
    "supply surge", "production increase", "increase production",
    "increases production", "increasing production", "increased production",
    "OPEC increase", "output hike", "higher output", "raise output",
    "ramp up production", "output boost", "increase output", "boosts production",
    "restores production", "restoring production", "resumes exports",
    "resuming exports", "reopens oil", "resumes crude", "lifts force majeure",
    "force majeure lifted", "export resumed", "pipeline reopened",
    "output returned", "supply returned", "production resumes",
    "production restarts", "restarting production",
    "record production", "record output", "record crude production",
    "demand destruction", "recession fears", "economic slowdown",
    "lockdown", "COVID restrictions", "release from SPR",
    "strategic reserve release", "IEA release", "reserve release",
    "supply return", "Libya restarts", "Saudi output boost",
    "US shale growth", "rig count rise",
    "demand drop", "global recession", "China lockdown",
    "manufacturing contraction", "demand fears", "weaker demand",
    "fueling fears of weaker oil demand", "demand outlook lower",
    "factory activity falls", "manufacturing slumps", "industrial output drops",
    "PMI contracts", "deep contraction", "demand outlook cut",
    "demand falls", "demand declines", "demand weakens",
    "GDP contracts", "GDP shrinks", "economic contraction",
    "denies production cut", "denies output cut", "rejects production cut",
    "rejects output cut", "no production cut", "no output cut",
    "inventories rise", "crude inventories increase", "inventories build",
    "crude stocks increase", "inventories swell", "unexpected rise in inventories",
    "gasoline inventories rise",
    "secure shipping lanes", "safe passage",
    "navy secures", "tankers safely transit",
    "peace talks", "negotiations progress", "constructive talks",
    "talks progress", "resume talks",
    "direct talks", "hold talks", "face-to-face talks",
    "navy escorts", "navy escort", "escorts tanker", "escort tanker",
    "agrees to inspections", "international inspections",
    "eases sanctions", "sanctions eased",
    "PMI falls", "factory activity contracts",
    "manufacturing contracts",
    "continue nuclear talks", "nuclear talks",
    "exports continue without disruption", "without disruption",
    "diplomatic channels open", "diplomatic channels remain open",
    "accepts mediation", "mediation proposal",
    "not looking for war", "willingness to reduce tensions",
    "reduce regional tensions", "new round of negotiations",
    "nuclear negotiations"
]

BULL_WORDS = [
    "war", "attack", "strike", "conflict", "tension", "sanction",
    "sanctions", "drone", "missile", "bomb", "invasion", "crisis", "outage",
    "sabotage", "hurricane", "explosion", "halt", "shutdown",
    "collapse", "blockade", "embargo", "shuts", "shut",
    "closes", "seizes", "hijack", "bombard", "hostile"
]

BEAR_WORDS = [
    "peace", "deal", "agreement", "ceasefire", "truce",
    "surge", "glut", "recession", "lockdown", "resolution",
    "restores", "resumes", "reopens", "lifts", "eases"
]

DEMAND_BULL_WORDS = [
    "record", "all-time", "raises", "rebounds", "stimulus"
]

BULL_REGEX = [
    r'\binventories\s+(fall|drop|decline|draw)',
    r'\bcrude\s+stocks\s+(fall|drop|decline)',
    r'Crude Oil Inventories Actual:\s*-\d',
    r'Inventories Actual:\s*-\d'
]

BEAR_REGEX = [
    r'\b(slash|slashing|cut|cuts|lower|lowers|reduce|reduces)\s+[\w\s]{0,20}demand\s+(forecast|outlook)',
    r'\bGDP\s+(contracts|shrinks|falls)',
    r'\binventories\s+(rise|build|swell|increase)',
    r'\bcrude\s+stocks\s+(rise|build|increase)',
    r'Crude Oil Inventories Actual:\s*\+\d',
    r'Inventories Actual:\s*\+\d'
]

CANCEL_BULL_STRINGS = [
    "no impact on exports", "no impact on supply", "no disruption to output",
    "production unchanged", "crude production unchanged", "output unchanged",
    "exports continue normally", "operations continue normally",
    "no damage after", "no damage reported", "proven false", "rumors false",
    "denies reports of production cut", "rejects proposal for production cut",
    "hurricane changes course", "hurricane misses", "storm veers away",
    "platforms remain operational", "force majeure lifted before",
    "already priced in", "as expected", "as forecast", "in line with expectations",
    "priced in", "no supply disruption", "no disruption expected",
    "no effect on production", "no effect on supply",
    "hold off attacks", "hold off", "threatens to close", "threatens closure",
    "calling off attacks"
]

CANCEL_BULL_REGEX = [
    r'\bas\s+(\w+\s+)?expected\b',
    r'\bin\s+line\s+with\s+expectations\b',
    r'\bno\s+damage\s+after\b',
    r'\bno\s+impact\s+on\s+exports\b',
]

SPR_REGEX = r'(release|releases|releasing).*(strategic petroleum reserve|SPR)|(strategic petroleum reserve|SPR).*(release|releases|releasing)'

# ----------------------------------------------------------------------
# SENTIMENT ENGINE
# ----------------------------------------------------------------------
def get_sentiment(news_text, conflict_mode="BEAR_BIAS"):
    text = news_text.lower()
    bull_matches = set()
    bear_matches = set()

    # 1. Regex patterns
    for pattern in BULL_REGEX:
        if re.search(pattern, text, re.IGNORECASE):
            bull_matches.add(f"regex:{pattern}")
    for pattern in BEAR_REGEX:
        if re.search(pattern, text, re.IGNORECASE):
            bear_matches.add(f"regex:{pattern}")

    # 2. Exact phrases
    for phrase in BULL_PHRASES:
        if phrase in text:
            bull_matches.add(phrase)
    for phrase in BEAR_PHRASES:
        if phrase in text:
            bear_matches.add(phrase)

    # 3. Single words with boundaries
    for word in BULL_WORDS:
        if re.search(r'\b' + re.escape(word) + r'\b', text):
            bull_matches.add(word)
    for word in BEAR_WORDS:
        if re.search(r'\b' + re.escape(word) + r'\b', text):
            bear_matches.add(word)
    for word in DEMAND_BULL_WORDS:
        if re.search(r'\b' + re.escape(word) + r'\b', text):
            bull_matches.add(word)

    # SPR special
    if re.search(SPR_REGEX, text, re.IGNORECASE):
        bear_matches.add("SPR release (regex)")

    # 4. Cancellation
    for cancel_str in CANCEL_BULL_STRINGS:
        if cancel_str in text:
            bull_matches.clear()
            break
    for pattern in CANCEL_BULL_REGEX:
        if re.search(pattern, text, re.IGNORECASE):
            bull_matches.clear()
            break

    # 5. Debug (logs will appear in Koyeb)
    if bull_matches:
        print(f"[SENTIMENT] BULL matches: {list(bull_matches)}")
    if bear_matches:
        print(f"[SENTIMENT] BEAR matches: {list(bear_matches)}")

    # 6. Decision
    if bull_matches and bear_matches:
        print("[CONFLICT] Both bullish and bearish signals detected.")
        if conflict_mode == "BEAR_BIAS":
            return Signal.BEAR
        elif conflict_mode == "BULL_BIAS":
            return Signal.BULL
        elif conflict_mode == "STAY_FLAT":
            return Signal.NEUTRAL
        else:  # FIRST_MATCH fallback
            return Signal.BEAR if bear_matches else Signal.BULL
    elif bear_matches:
        return Signal.BEAR
    elif bull_matches:
        return Signal.BULL
    else:
        return Signal.NEUTRAL
