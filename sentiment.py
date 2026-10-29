import re
from enum import Enum

class Signal(Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    NEUTRAL = "NEUTRAL"

# ======================================================================
# VOCABULARY TABLES
# Expanded from empirical study of ApeWire headlines (Apr–Oct 2026).
# Structure, keys and names are IDENTICAL to the original file.
# Only list contents grew, and CANCEL_BEAR_* was added (mirror of
# CANCEL_BULL_*).
# ======================================================================

# ----------------------------------------------------------------------
# 1. MULTI-WORD PHRASES (exact substring match, lowercase)
# ----------------------------------------------------------------------
BULL_PHRASES = [
    # ---- ORIGINAL (unchanged) ----
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
    "hostile",

    # ---- NEW: escalation phrases from study misses ----
    "will hit iran", "will be hitting", "hitting iran", "hit iran hard",
    "hitting them hard", "will hit them", "hit them",
    "conducting strikes", "conducting air strikes", "conducting airstrikes",
    "conducting military strikes", "launching strikes", "launching air strikes",
    "wave of strikes", "wave of air strikes", "wave of military strikes",
    "military strikes", "air strikes", "airstrike", "airstrikes",
    "began launching strikes", "began launching airstrikes",
    "strikes against", "strikes on",
    "fired missiles", "fires missiles", "launches missiles", "launching missiles",
    "missiles at", "missile attack", "missile attacks", "missile strikes",
    "launched drones", "launches drones", "drone attacks", "drones at",
    "targets in", "targeted in", "struck two", "struck the",
    "reports of strikes", "reports of missiles",
    "hormuz is closed", "hormuz closed", "strait is closed",
    "strait of hormuz is closed", "strait of hormuz closed",
    "hormuz shut", "strait shut",
    "closed until further notice",
    "control of strait", "control of hormuz", "total control over strait",
    "total control over hormuz",
    "naval blockade", "blockade of iran", "blockade of all",
    "self-defense strikes", "self defense strikes",
    "ceasefire violation", "ceasefire violations",
    "violates ceasefire", "violated ceasefire", "violation of ceasefire",
    "violation of the ceasefire", "violating ceasefire",
    "ceasefire broken", "breaks ceasefire", "broke ceasefire",
    "deal collapsed", "deal collapse", "agreement collapsed",
    "talks collapsed", "talks collapse", "talks failed",
    "war will resume", "resume war", "resumes war", "resuming war",
    "war will continue", "continue the war", "continue war",
    "finish off iran", "finish the job", "finishing the job",
    "wipe out", "wipe them out", "wipe iran",
    # Rejection / collapse phrases (deal-break escalation)
    "rejects ceasefire", "rejected ceasefire", "reject ceasefire",
    "refuses ceasefire", "refused ceasefire",
    "denies ceasefire", "denied ceasefire",
    "rejects deal", "rejected deal", "reject deal", "rejecting deal",
    "rejects agreement", "rejected agreement", "reject agreement",
    "rejects accord", "rejected accord", "reject accord",
]

BEAR_PHRASES = [
    # ---- ORIGINAL (unchanged) ----
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
    "nuclear negotiations",
    "end the war", "end war",
    "calls for conditions to end", "calls for meeting conditions",
    "conditions to end the war", "bring the war to an end",
    "neither war nor peace", "de-escalation", "deescalation",
    "ceasefire talks", "peace negotiations",
    "diplomatic solution", "off-ramp",
    "end the conflict", "resolve the conflict",

    # ---- NEW: de-escalation phrases from study misses ----
    "end of war", "end of the war", "war is over", "war over",
    "settlement of war", "settlement of the war",
    "finish the war", "finishing the war", "finish off the war",
    "bring war to an end", "bring the war to an end",
    "halt attacks", "halts attacks", "halted attacks", "halting attacks",
    "halt mutual attacks", "halts mutual attacks", "halting mutual attacks",
    "halt strikes", "halts strikes", "halted strikes", "halting strikes",
    "halt military operations", "halts military operations",
    "halt combat", "halts combat", "halt hostilities", "cease hostilities",
    "halt fire", "cease fire",
    "strait open", "strait is open", "strait of hormuz is open",
    "strait of hormuz open", "hormuz open", "hormuz is open",
    "open the strait", "reopen the strait", "reopens the strait",
    "opening of hormuz", "opening of the strait",
    "reopen hormuz", "reopens hormuz", "reopening hormuz",
    "mines removed", "remove mines", "removes mines", "clearing mines",
    "clear mines", "clears mines", "minesweepers", "minesweeping",
    "reduce tensions", "reduces tensions", "reducing tensions",
    "ease tensions", "eases tensions", "easing tensions",
    "de-escalate", "deescalate", "de-escalates", "de-escalated",
    "diplomatic path", "diplomatic pathway",
    "constructive discussions", "productive discussions", "productive talks",
    "progress in talks", "progress in negotiations",
    "agreement in principle",
    "final draft", "finalized draft", "finalised draft",
    "initial agreement", "preliminary agreement",
    "sign a deal", "signing of deal", "signing of agreement",
    "signing the deal", "signing the agreement",
    "deal reached", "deal signed", "agreement signed",
    "releases frozen assets", "release frozen assets",
    "release of frozen assets", "unfreeze", "unfrozen assets",
    "deal finalized", "deal finalised",
    "deal is near", "deal near", "near deal",
    "close to peace", "close to a deal", "close to deal",
    "close to agreement", "peace arrangement",
    "approval of final draft", "approves final draft",
    "approved final draft", "approval of the draft",
    # Oil-specific bearish (was completely missing)
    "oil prices will come down", "oil prices will fall", "oil prices fall",
    "oil price lower", "oil prices lower", "oil prices decline",
    "oil prices drop", "oil prices slide",
    "wti down", "wti heading down", "wti falls", "wti drops",
    "brent lower", "brent falls", "brent drops", "brent down",
    "increases oil supply", "increase oil supply", "boosts oil supply",
    "boost oil supply", "oil supply increase", "higher oil supply",
    "authorizes sale of", "authorizes export",
]

BULL_WORDS = [
    # ---- ORIGINAL (unchanged) ----
    "war", "attack", "strike", "conflict", "tension", "sanction",
    "sanctions", "drone", "missile", "bomb", "invasion", "crisis", "outage",
    "sabotage", "hurricane", "explosion", "halt", "shutdown",
    "collapse", "blockade", "embargo", "shuts", "shut",
    "closes", "seizes", "hijack", "bombard", "hostile",
    # ---- NEW: inflections of the ORIGINAL words (fix plural/past/gerund) ----
    "attacks", "attacked", "attacking",
    "strikes", "struck", "striking",
    "tensions",
    "sanctioned", "sanctioning",
    "drones",
    "missiles",
    "bombs", "bombed", "bombing",
    "invaded", "invades", "invading",
    "crises", "outages",
    "hurricanes",
    "explosions",
    "halts", "halted", "halting",
    "shutdowns",
    "collapsed", "collapsing",
    "blockades", "blockaded", "blockading",
    "embargoes",
    "shutting",
    "close", "closed", "closing",
    "seize", "seized", "seizing", "seizure", "seizures",
    "hijacks", "hijacked", "hijacking",
    "bombards", "bombarded", "bombarding", "bombardment",
    "hostilities",
    # ---- NEW: high-confidence military escalation vocabulary ----
    "targeted", "targeting",
    "shelling", "shelled",
    "clashes", "clash", "clashed",
    "violation", "violations", "violated", "violates",
    "breach", "breaches", "breached",
    "assassinate", "assassinated", "assassination",
    "retaliate", "retaliated", "retaliates",
    "launches", "launched", "launching", "launcher", "launchers",
    "destroys", "destroyed", "destroying", "destruction",
    "fires", "fired", "firing", "firefight", "firefights",
]

BEAR_WORDS = [
    # ---- ORIGINAL (unchanged) ----
    "peace", "deal", "agreement", "ceasefire", "truce",
    "surge", "glut", "recession", "lockdown", "resolution",
    "restores", "resumes", "reopens", "lifts", "eases",
    # ---- NEW: inflections of the ORIGINAL words ----
    "agreements", "agreed", "agreeing",
    "ceasefires",
    "truces",
    "gluts",
    "recessions",
    "lockdowns",
    "resolutions",
    "restore", "restored", "restoring",
    "resume", "resumed", "resuming",
    "reopen", "reopened", "reopening",
    "lift", "lifted", "lifting",
    "eased", "easing",
    # ---- NEW: diplomacy / de-escalation vocabulary ----
    "negotiation", "negotiations", "negotiate", "negotiated", "negotiating",
    "diplomatic", "diplomacy",
    "de-escalation", "deescalation",
    "settlement", "settlements", "settle", "settled", "settling",
    "accord", "accords",
    "mou", "memorandum",
    "reconciliation", "reconcile", "reconciled",
    "calm", "calms", "calmed", "calming",
]

DEMAND_BULL_WORDS = [
    "record", "all-time", "raises", "rebounds", "stimulus"
]

BULL_REGEX = [
    r'\binventories\s+(fall|drop|decline|draw)',
    r'\bcrude\s+stocks\s+(fall|drop|decline)',
    r'Crude Oil Inventories Actual:\s*-\d',
    r'Inventories Actual:\s*-\d',
    # NEW: military verbs targeting Iran / missiles / drones
    r'\b(hit|hits|hitting|struck|strike|strikes|striking)\b.{0,30}\biran\b',
    r'\biran\b.{0,30}\b(hit|hits|hitting|struck|strike|strikes|striking)\b',
    r'\bmissiles?\b.{0,20}\b(at|toward|towards|into|on)\b',
    r'\bdrones?\b.{0,20}\b(at|toward|towards|into|on)\b',
    r'\b(began|begins|launched|launches)\b.{0,30}\b(strikes|airstrikes|missiles|drones|attacks)\b',
]

BEAR_REGEX = [
    r'\b(slash|slashing|cut|cuts|lower|lowers|reduce|reduces)\s+[\w\s]{0,20}demand\s+(forecast|outlook)',
    r'\bGDP\s+(contracts|shrinks|falls)',
    r'\binventories\s+(rise|build|swell|increase)',
    r'\bcrude\s+stocks\s+(rise|build|increase)',
    r'Crude Oil Inventories Actual:\s*\+\d',
    r'Inventories Actual:\s*\+\d',
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
    "calling off attacks",
]

CANCEL_BULL_REGEX = [
    r'\bas\s+(\w+\s+)?expected\b',
    r'\bin\s+line\s+with\s+expectations\b',
    r'\bno\s+damage\s+after\b',
    r'\bno\s+impact\s+on\s+exports\b',
]

# ----------------------------------------------------------------------
# NEW: BEAR cancellation.
# Mirrors CANCEL_BULL_* exactly. Purpose: clear the BEAR matches that
# are fired by "deal", "ceasefire", "agreement", "accord" when the same
# text contains a rejection / violation / non-finalization signal.
# Fixes the study's biggest failure class (11% direction inversions).
# ----------------------------------------------------------------------
CANCEL_BEAR_STRINGS = [
    # Ceasefire violations / rejections
    "ceasefire violation", "ceasefire violations",
    "violates ceasefire", "violated ceasefire", "violating ceasefire",
    "ceasefire broken", "breaks ceasefire", "broke ceasefire",
    "ceasefire collapsed", "ceasefire collapses",
    "rejects ceasefire", "rejected ceasefire", "reject ceasefire",
    "denies ceasefire", "denied ceasefire", "deny ceasefire",
    "refuses ceasefire", "refused ceasefire",
    # Deal / agreement rejections and collapses
    "rejects deal", "rejected deal", "reject deal", "rejecting deal",
    "rejects agreement", "rejected agreement", "reject agreement",
    "rejects accord", "rejected accord", "reject accord",
    "rejects mou", "rejected mou", "reject mou",
    "denies deal", "denied deal", "deny deal",
    "denies agreement", "denied agreement", "deny agreement",
    "denies accord", "denied accord", "deny accord",
    "denies mou", "denied mou", "deny mou",
    "no deal", "no agreement", "no accord",
    "deal collapsed", "deal collapse", "deal is dead", "deal dead",
    "agreement collapsed", "agreement collapse", "agreement is dead",
    "accord collapsed", "accord is dead",
    "talks collapsed", "talks collapse", "talks have collapsed",
    "talks failed", "negotiations failed", "negotiations collapsed",
    # Non-finalisation
    "no final decision", "not finalized", "not finalised",
    "not yet finalized", "not yet finalised",
    "no final agreement", "no finalised agreement", "no finalized agreement",
    "differences remain", "differences persist", "differences still remain",
    "not finalized due to", "not finalised due to",
    # Reversals / changes
    "deal has changed", "text has changed", "mou has changed",
    "not true", "rejects reports", "denies reports",
    "calling off talks", "calls off talks",
    # Irrelevant-to-oil context escapes (prevent false BEARs on tech/politics)
    "merger", "greenland",
]

CANCEL_BEAR_REGEX = [
    # "rejects/denies/refuses ... <deal|ceasefire|agreement|accord|mou>"
    r'\b(rejects?|rejected|denies|denied|deny|refuses?|refused)\b'
    r'.{0,40}\b(ceasefire|deal|agreement|accord|mou)\b',
    # "<deal|ceasefire|agreement|accord|mou> ... violated/rejected/broken/collapsed/failed/over/dead"
    r'\b(ceasefire|deal|agreement|accord|mou)\b'
    r'.{0,40}\b(violat\w*|reject\w*|broken|broke|collaps\w*|fail\w*|over|dead)\b',
    # "violates ... <ceasefire|agreement|accord|mou>"
    r'\bviolat\w*\b.{0,40}\b(ceasefire|agreement|accord|mou)\b',
    # Non-finalisation
    r'\bno\s+(final\s+)?(decision|agreement|deal)\b',
    r'\bnot\s+(yet\s+)?(finalized|finalised|final)\b',
    r'\bdifferences?\s+(remain|persist|still)\b',
]

SPR_REGEX = r'(release|releases|releasing).*(strategic petroleum reserve|SPR)|(strategic petroleum reserve|SPR).*(release|releases|releasing)'


# ======================================================================
# SENTIMENT ENGINE
# Signature, return values and decision rules are UNCHANGED.
# Only addition: a symmetric CANCEL_BEAR_* pass (same shape as the
# existing CANCEL_BULL_* pass).
# ======================================================================
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

    # 4a. Cancellation — BULL (unchanged)
    for cancel_str in CANCEL_BULL_STRINGS:
        if cancel_str in text:
            bull_matches.clear()
            break
    for pattern in CANCEL_BULL_REGEX:
        if re.search(pattern, text, re.IGNORECASE):
            bull_matches.clear()
            break

    # 4b. Cancellation — BEAR (NEW, symmetric to 4a)
    for cancel_str in CANCEL_BEAR_STRINGS:
        if cancel_str in text:
            bear_matches.clear()
            break
    for pattern in CANCEL_BEAR_REGEX:
        if re.search(pattern, text, re.IGNORECASE):
            bear_matches.clear()
            break

    # 5. Debug (format unchanged)
    if bull_matches:
        print(f"[SENTIMENT] BULL matches: {list(bull_matches)}")
    if bear_matches:
        print(f"[SENTIMENT] BEAR matches: {list(bear_matches)}")

    # 6. Decision (unchanged)
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
