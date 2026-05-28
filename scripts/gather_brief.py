#!/usr/bin/env python3
"""
Gather data for today's morning brief from free public APIs.

Writes: briefs/YYYY-MM-DD.json  (in the form expected by build_episode.py)

All sections are FAULT-TOLERANT — if any fetch fails the brief still ships,
just without that section. No API keys required.
"""

import json
import random
import re
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BRIEFS_DIR = REPO_ROOT / "briefs"

TEL_AVIV_LAT = 32.0853
TEL_AVIV_LON = 34.7818
UA = "Mozilla/5.0 (compatible; SageeMorningBrief/2.0)"


# ─── HTTP helpers ──────────────────────────────────────────────────────────

def http_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def http_text(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# ─── Timezone (Israel DST) ─────────────────────────────────────────────────

def israel_today():
    now_utc = datetime.now(timezone.utc)
    year = now_utc.year

    def last_dow_of_month(year, month, weekday):
        d = datetime(year, month, 28, tzinfo=timezone.utc)
        while d.month == month:
            if d.weekday() == weekday:
                return d
            d += timedelta(days=1)
        d -= timedelta(days=1)
        while d.weekday() != weekday:
            d -= timedelta(days=1)
        return d

    last_sun_march = last_dow_of_month(year, 3, 6)
    idt_start = last_sun_march - timedelta(days=2)
    idt_end = last_dow_of_month(year, 10, 6)
    is_idt = idt_start <= now_utc < idt_end
    offset_hours = 3 if is_idt else 2
    local = now_utc + timedelta(hours=offset_hours)
    return local, offset_hours


# ─── Weather + sun + tomorrow + AQI ────────────────────────────────────────

WEATHER_CODE = {
    0: "clear and sunny", 1: "mostly sunny", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow",
    80: "rain showers", 81: "rain showers", 82: "heavy showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "severe thunderstorms",
}


def fetch_weather_bundle():
    """Returns dict with current, today, tomorrow, sunrise, sunset."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={TEL_AVIV_LAT}&longitude={TEL_AVIV_LON}"
        "&current=temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code,sunrise,sunset"
        "&timezone=Asia/Jerusalem&forecast_days=2"
    )
    data = http_json(url)
    cur = data["current"]
    d = data["daily"]
    return {
        "current_temp_c": round(cur["temperature_2m"]),
        "current_code": cur["weather_code"],
        "today_high_c": round(d["temperature_2m_max"][0]),
        "today_low_c": round(d["temperature_2m_min"][0]),
        "today_rain_pct": d["precipitation_probability_max"][0],
        "today_code": d["weather_code"][0],
        "tomorrow_high_c": round(d["temperature_2m_max"][1]),
        "tomorrow_low_c": round(d["temperature_2m_min"][1]),
        "tomorrow_rain_pct": d["precipitation_probability_max"][1],
        "tomorrow_code": d["weather_code"][1],
        "sunrise": d["sunrise"][0],  # "2026-05-28T05:42"
        "sunset": d["sunset"][0],
    }


def fetch_air_quality():
    """Open-Meteo AQ. Returns dict with us_aqi and category. Best effort."""
    try:
        url = (
            "https://air-quality-api.open-meteo.com/v1/air-quality"
            f"?latitude={TEL_AVIV_LAT}&longitude={TEL_AVIV_LON}"
            "&current=us_aqi,pm2_5,pm10&timezone=Asia/Jerusalem"
        )
        data = http_json(url)
        aqi = round(data["current"]["us_aqi"])
        if aqi <= 50: cat = "good"
        elif aqi <= 100: cat = "moderate"
        elif aqi <= 150: cat = "unhealthy for sensitive groups"
        elif aqi <= 200: cat = "unhealthy"
        elif aqi <= 300: cat = "very unhealthy"
        else: cat = "hazardous"
        return {"aqi": aqi, "category": cat}
    except Exception as e:
        print(f"Warning: AQI fetch failed ({e}).", file=sys.stderr)
        return None


def format_hm(iso_local_str):
    """'2026-05-28T19:34' -> '7:34 in the evening' (spoken-friendly)."""
    try:
        t = iso_local_str.split("T")[1]
        h, m = t.split(":")[:2]
        h = int(h)
        if h < 12:
            tail = "in the morning"
            hh = h if h > 0 else 12
        elif h == 12:
            tail = "noon" if m == "00" else "in the afternoon"
            hh = 12
        else:
            tail = "in the afternoon" if h < 17 else "in the evening"
            hh = h - 12
        # drop the leading zero on minutes for natural speech
        m_str = m.lstrip("0") or "0"
        if m == "00":
            return f"{hh} {tail}" if tail == "noon" else f"{hh} o'clock {tail}"
        return f"{hh}:{m} {tail}"
    except Exception:
        return iso_local_str


def describe_weather(w):
    cond = WEATHER_CODE.get(w["today_code"], "mild")
    rain = w["today_rain_pct"]
    if rain >= 60:
        rain_note = f" Rain is likely — a {rain} percent chance."
    elif rain >= 30:
        rain_note = f" There's a {rain} percent chance of rain."
    else:
        rain_note = " No rain expected."
    return (
        f"Currently {w['current_temp_c']} degrees Celsius and {cond}, "
        f"with a high of {w['today_high_c']} and a low of {w['today_low_c']} today.{rain_note}"
    )


def describe_tomorrow(w):
    cond = WEATHER_CODE.get(w["tomorrow_code"], "mild")
    today_high = w["today_high_c"]
    tom_high = w["tomorrow_high_c"]
    delta = tom_high - today_high
    if abs(delta) <= 1:
        framing = "Tomorrow looks similar"
    elif delta > 1:
        framing = f"Tomorrow warms up to {tom_high}"
    else:
        framing = f"Tomorrow cools to {tom_high}"
    if w["tomorrow_rain_pct"] >= 50:
        rain_note = ", with a good chance of rain"
    else:
        rain_note = ""
    return f"{framing}, {cond}{rain_note}."


# ─── Markets + narrative framing ───────────────────────────────────────────

def fetch_yahoo_quote(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
    data = http_json(url)
    meta = data["chart"]["result"][0]["meta"]
    return {
        "price": meta.get("regularMarketPrice") or meta.get("previousClose"),
        "previous_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
    }


def fetch_markets():
    return {
        "sp500": fetch_yahoo_quote("%5EGSPC"),
        "nasdaq100": fetch_yahoo_quote("%5ENDX"),
        "vix": fetch_yahoo_quote("%5EVIX"),
    }


def pct_change(p, prev):
    if not prev: return 0.0
    return (p - prev) / prev * 100


def market_narrative(markets, market_label):
    """Rule-based narrative that varies by magnitude/direction."""
    sp = markets["sp500"]
    nd = markets["nasdaq100"]
    vx = markets["vix"]
    sp_pct = pct_change(sp["price"], sp["previous_close"])
    nd_pct = pct_change(nd["price"], nd["previous_close"])
    vx_pct = pct_change(vx["price"], vx["previous_close"])

    # describe S&P move
    a = abs(sp_pct)
    if a < 0.25:
        verb = "barely moved"
    elif a < 0.75:
        verb = "edged " + ("higher" if sp_pct > 0 else "lower")
    elif a < 1.5:
        verb = "rose" if sp_pct > 0 else "pulled back"
    elif a < 2.5:
        verb = "rallied" if sp_pct > 0 else "fell sharply"
    else:
        verb = "surged" if sp_pct > 0 else "tumbled"

    # build sentence
    sp_str = f"finishing at {sp['price']:,.0f}"
    nd_str = f"the Nasdaq 100 {'added' if nd_pct >= 0 else 'lost'} {abs(nd_pct):.1f} percent, closing at {nd['price']:,.0f}"
    vix_dir = "higher" if vx_pct > 5 else "lower" if vx_pct < -5 else "little changed"
    vix_lab = "calm" if vx["price"] < 20 else "elevated" if vx["price"] < 30 else "high"

    if a < 0.25:
        first = f"At {market_label} close, U.S. stocks {verb}, with the S and P 500 essentially flat near {sp['price']:,.0f}."
    else:
        first = f"At {market_label} close, the S and P 500 {verb} {abs(sp_pct):.1f} percent, {sp_str}."

    second = f"{nd_str.capitalize()}."
    third = f"The VIX, Wall Street's fear gauge, settled {vix_dir} at {vx['price']:.1f} — in {vix_lab} territory."
    return " ".join([first, second, third])


# ─── Fear & Greed ──────────────────────────────────────────────────────────

def fetch_fear_and_greed():
    try:
        data = http_json("https://production.dataviz.cnn.io/index/fearandgreed/graphdata")
        fg = data.get("fear_and_greed", {})
        return {"score": round(fg.get("score", 50)), "rating": fg.get("rating", "neutral").title()}
    except Exception as e:
        print(f"Warning: F&G fetch failed ({e}).", file=sys.stderr)
        return None


# ─── News headline ─────────────────────────────────────────────────────────

def fetch_news_headline():
    """Pull the top headline from BBC World RSS. Best effort."""
    try:
        text = http_text("http://feeds.bbci.co.uk/news/world/rss.xml")
        root = ET.fromstring(text)
        items = root.findall(".//item")
        if not items:
            return None
        title = (items[0].findtext("title") or "").strip()
        # Tidy up if needed
        title = re.sub(r"\s+", " ", title)
        return title or None
    except Exception as e:
        print(f"Warning: news fetch failed ({e}).", file=sys.stderr)
        return None


# ─── On this day (Wikipedia) ───────────────────────────────────────────────

def fetch_on_this_day(local):
    """Pull a notable historical event for today's MM/DD from Wikipedia's REST API."""
    try:
        mm = f"{local.month:02d}"
        dd = f"{local.day:02d}"
        # Wikipedia REST: events
        url = f"https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/events/{mm}/{dd}"
        data = http_json(url)
        events = data.get("events", [])
        if not events:
            return None
        # Filter to events older than 50 years (more "historical") and prefer notable ones
        seed = int(local.strftime("%Y%m%d"))
        rnd = random.Random(seed)
        # Score events: prefer those with a featured page and reasonable text length
        scored = []
        cur_year = local.year
        for e in events:
            year = e.get("year")
            text = e.get("text", "").strip()
            if not text or not year:
                continue
            if cur_year - int(year) < 30:
                continue  # too recent
            if len(text) > 220:
                continue  # too long
            if len(text) < 30:
                continue  # too brief
            scored.append((year, text))
        if not scored:
            return None
        year, text = rnd.choice(scored)
        # ensure it ends with a period
        if not text.endswith("."):
            text += "."
        return {"year": year, "text": text}
    except Exception as e:
        print(f"Warning: on-this-day fetch failed ({e}).", file=sys.stderr)
        return None


# ─── Quotes (varied sources, no hustle-culture) ────────────────────────────

QUOTES = [
    ("Carl Sagan", "For small creatures such as we, the vastness is bearable only through love."),
    ("Marcus Aurelius", "You have power over your mind — not outside events. Realize this, and you will find strength."),
    ("Maya Angelou", "You may encounter many defeats, but you must not be defeated."),
    ("Annie Dillard", "How we spend our days is, of course, how we spend our lives."),
    ("Rainer Maria Rilke", "Be patient toward all that is unsolved in your heart, and try to love the questions themselves."),
    ("Toni Morrison", "If you surrendered to the air, you could ride it."),
    ("Viktor Frankl", "When we are no longer able to change a situation, we are challenged to change ourselves."),
    ("James Baldwin", "Not everything that is faced can be changed, but nothing can be changed until it is faced."),
    ("Rumi", "The wound is the place where the light enters you."),
    ("Joan Didion", "We tell ourselves stories in order to live."),
    ("Mary Oliver", "Tell me, what is it you plan to do with your one wild and precious life?"),
    ("Seneca", "Every new beginning comes from some other beginning's end."),
    ("Hannah Arendt", "The sad truth is that most evil is done by people who never make up their minds to be good or evil."),
    ("Audre Lorde", "Caring for myself is not self-indulgence, it is self-preservation."),
    ("Pema Chödrön", "Nothing ever goes away until it has taught us what we need to know."),
    ("Wendell Berry", "Be joyful though you have considered all the facts."),
    ("Lao Tzu", "Nature does not hurry, yet everything is accomplished."),
    ("Simone Weil", "Attention is the rarest and purest form of generosity."),
    ("Rebecca Solnit", "Hope is an embrace of the unknown and the unknowable."),
    ("Søren Kierkegaard", "Life can only be understood backwards; but it must be lived forwards."),
    ("Iris Murdoch", "Love is the extremely difficult realization that something other than oneself is real."),
    ("David Foster Wallace", "The really important kind of freedom involves attention, and awareness, and discipline."),
    ("Octavia Butler", "All that you touch you change. All that you change changes you."),
    ("Heraclitus", "No man ever steps in the same river twice, for it's not the same river and he's not the same man."),
    ("Jorge Luis Borges", "I have always imagined that paradise will be a kind of library."),
    ("Naomi Shihab Nye", "Before you know kindness as the deepest thing inside, you must know sorrow as the other deepest thing."),
    ("Zora Neale Hurston", "There are years that ask questions and years that answer."),
    ("Mary Shelley", "Nothing is so painful to the human mind as a great and sudden change."),
    ("Albert Camus", "In the depth of winter, I finally learned that within me there lay an invincible summer."),
    ("Kahlil Gibran", "Your pain is the breaking of the shell that encloses your understanding."),
    ("Anaïs Nin", "And then the day came when the risk to remain tight in a bud was more painful than the risk it took to blossom."),
    ("Henry David Thoreau", "It's not what you look at that matters, it's what you see."),
    ("Virginia Woolf", "Arrange whatever pieces come your way."),
    ("Albert Einstein", "Look deep into nature, and then you will understand everything better."),
    ("Hafiz", "The words you speak become the house you live in."),
    ("Etty Hillesum", "Sometimes the most important thing in a whole day is the rest we take between two deep breaths."),
    ("Lin-Manuel Miranda", "Look around, look around, at how lucky we are to be alive right now."),
    ("Confucius", "The man who moves a mountain begins by carrying away small stones."),
    ("Octavio Paz", "Wisdom lies neither in fixity nor in change, but in the dialectic between the two."),
    ("Aldous Huxley", "It is a little embarrassing that, after forty-five years of research, the best advice I can give people is to be a little kinder to each other."),
]


def pick_quote(local):
    rnd = random.Random(int(local.strftime("%Y%m%d")))
    return rnd.choice(QUOTES)


# ─── Reflection prompts (curated; not hustle, not corporate) ───────────────

REFLECTION_PROMPTS = [
    "What would today look like if you said no to one thing?",
    "Where could you choose generosity today, even quietly?",
    "Who could use a kind word from you this morning?",
    "What's something small you've been putting off that you could finish in five minutes?",
    "What would it mean to be fully present for the next hour?",
    "What are you carrying today that isn't yours to carry?",
    "What's one thing that's gone right recently that you haven't paused to notice?",
    "If today were to surprise you, what direction would you want the surprise to come from?",
    "What's a question you've been avoiding asking yourself?",
    "Where are you being harder on yourself than you'd be on a friend?",
    "What would patient you do today, instead of urgent you?",
    "What is one thing you'd like to remember about today, looking back from a year from now?",
    "Whose voice is in your head this morning — is it yours?",
    "What's something you're curious about that has nothing to do with work?",
    "Where could you let something be good enough today?",
    "What's the smallest possible version of the thing you've been over-thinking?",
    "Who in your life have you not thought about lately who deserves a quick message?",
    "What would it look like to begin today, instead of continue?",
    "Where could attention itself be the gift you give today?",
    "What's a way you've already been brave this week?",
    "If you trusted yourself a little more, what would you do differently this morning?",
    "What's the difference between rest and avoidance for you right now?",
    "Where could humor land softly today?",
    "What's a piece of beauty you'd like to make room for, even briefly?",
    "What's the most generous interpretation of something that frustrated you yesterday?",
    "Where are you confusing motion with progress?",
    "What's a small comfort you could give yourself before the day really starts?",
    "Whose company would feed you today, if you made time for it?",
    "Where could simplicity replace cleverness in something you're working on?",
    "What's a story you keep telling about yourself that maybe isn't true anymore?",
]


def pick_prompt(local):
    rnd = random.Random(int(local.strftime("%Y%m%d")) + 7)  # different seed than quote
    return rnd.choice(REFLECTION_PROMPTS)


# ─── Brief composer ────────────────────────────────────────────────────────

def market_day_label(local):
    wd = local.weekday()  # Mon=0
    return "Friday's" if wd in (5, 6, 0) else "yesterday's"


def day_of_year(local):
    return local.timetuple().tm_yday


def compose_brief(local, weather, aq, markets, fg, news, on_this_day, quote, prompt):
    parts = []
    date_words = local.strftime("%A, %B %d, %Y").replace(" 0", " ")
    doy = day_of_year(local)
    parts.append(f"Good morning, Sagee. It's {date_words} — day {doy} of the year.")

    # Sun
    parts.append(
        f"The sun rose this morning at {format_hm(weather['sunrise'])} and will set at {format_hm(weather['sunset'])}."
    )

    # Weather today + tomorrow + AQI
    parts.append(f"In Tel Aviv: {describe_weather(weather)} {describe_tomorrow(weather)}")
    if aq and aq["aqi"] > 100:
        parts.append(f"Air quality is {aq['category']} today — index reads {aq['aqi']}.")
    elif aq and aq["aqi"] > 50:
        parts.append(f"Air quality is {aq['category']}.")

    # Markets narrative + F&G
    if markets:
        parts.append(market_narrative(markets, market_day_label(local)))
    if fg:
        parts.append(f"CNN's Fear and Greed Index reads {fg['score']} — {fg['rating'].lower()}.")

    # News headline
    if news:
        parts.append(f"One headline you'll see today, from the BBC: {news}.")

    # On this day
    if on_this_day:
        parts.append(f"On this day in {on_this_day['year']}: {on_this_day['text']}")

    # Quote
    parts.append(f"Today's thought is from {quote[0]}: {quote[1]}")

    # Reflection prompt
    parts.append(f"And a question to carry with you: {prompt}")

    # Close
    parts.append(f"Have a wonderful {local.strftime('%A')}.")

    return " ".join(parts)


# ─── Main ──────────────────────────────────────────────────────────────────

def main():
    local, offset_hours = israel_today()
    date_str = local.strftime("%Y-%m-%d")
    out = BRIEFS_DIR / f"{date_str}.json"
    if out.exists():
        print(f"Brief already exists for {date_str} ({out}); leaving it untouched.")
        return

    print(f"Building brief for {date_str} (Israel local: {local.isoformat()})")

    # Fetch everything; each section is independently fault-tolerant
    try:
        weather = fetch_weather_bundle()
        print(f"Weather: {weather['current_temp_c']}°C, high {weather['today_high_c']}, sunset {weather['sunset']}")
    except Exception as e:
        sys.exit(f"FATAL: weather fetch failed: {e}")  # weather is required

    aq = fetch_air_quality()
    print(f"AQI: {aq}")

    try:
        markets = fetch_markets()
        print(f"Markets: SP500={markets['sp500']['price']:.0f}, NDX={markets['nasdaq100']['price']:.0f}, VIX={markets['vix']['price']:.1f}")
    except Exception as e:
        print(f"Warning: markets fetch failed ({e}). Skipping markets section.", file=sys.stderr)
        markets = None

    fg = fetch_fear_and_greed()
    news = fetch_news_headline()
    print(f"News: {news[:80] if news else None}")

    on_this_day = fetch_on_this_day(local)
    print(f"On this day: {on_this_day}")

    quote = pick_quote(local)
    prompt = pick_prompt(local)
    print(f"Quote: {quote[0]} — {quote[1][:50]}...")
    print(f"Prompt: {prompt[:60]}...")

    spoken_text = compose_brief(local, weather, aq, markets, fg, news, on_this_day, quote, prompt)

    pub_dt = local.replace(hour=7, minute=0, second=0, microsecond=0)
    aware = pub_dt.replace(tzinfo=timezone(timedelta(hours=offset_hours)))
    pub_date = format_datetime(aware)

    title = local.strftime("%A, %B %d, %Y").replace(" 0", " ")
    description = "Weather, markets, a headline, a thought,