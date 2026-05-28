#!/usr/bin/env python3
"""
Gather data for today's morning brief from free public APIs.

Writes: briefs/YYYY-MM-DD.json  (in the form expected by build_episode.py)

Runs in GitHub Actions — needs no API keys. All data sources are public and free.
"""

import json
import random
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BRIEFS_DIR = REPO_ROOT / "briefs"

TEL_AVIV_LAT = 32.0853
TEL_AVIV_LON = 34.7818
ISRAEL_TZ_OFFSET_HOURS = 3  # IDT = +3 (late March to late October), IST = +2 in winter

UA = "Mozilla/5.0 (compatible; SageeMorningBrief/1.0)"


def http_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def israel_today():
    """Return (datetime in Israel local, tz_offset_str like '+0300')."""
    # determine current Israel-time tz offset using a quick computation:
    # IDT (+3) runs from last Friday of March to last Sunday of October
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

    # IDT starts: Friday before the last Sunday of March, at 2am
    last_sun_march = last_dow_of_month(year, 3, 6)
    idt_start = last_sun_march - timedelta(days=2)  # Friday
    # IDT ends: last Sunday of October at 2am
    idt_end = last_dow_of_month(year, 10, 6)

    is_idt = idt_start <= now_utc < idt_end
    offset_hours = 3 if is_idt else 2
    offset_str = f"+{offset_hours:02d}00"
    local = now_utc + timedelta(hours=offset_hours)
    return local, offset_str, offset_hours


def fetch_weather():
    """Open-Meteo, no key needed. Returns dict with current temp, today's high/low, conditions."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={TEL_AVIV_LAT}&longitude={TEL_AVIV_LON}"
        "&current=temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"
        "&timezone=Asia/Jerusalem&forecast_days=1"
    )
    data = http_json(url)
    cur = data["current"]
    daily = data["daily"]
    return {
        "current_temp_c": round(cur["temperature_2m"]),
        "high_c": round(daily["temperature_2m_max"][0]),
        "low_c": round(daily["temperature_2m_min"][0]),
        "rain_prob_pct": daily["precipitation_probability_max"][0],
        "weather_code": cur["weather_code"],
        "wind_kmh": round(cur["wind_speed_10m"]),
        "humidity_pct": round(cur["relative_humidity_2m"]),
    }


# Open-Meteo weather codes → short spoken phrase
WEATHER_CODE = {
    0: "clear and sunny",
    1: "mostly sunny",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "foggy",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    80: "rain showers",
    81: "rain showers",
    82: "heavy showers",
    95: "thunderstorms",
    96: "thunderstorms with hail",
    99: "severe thunderstorms",
}


def describe_weather(w):
    cond = WEATHER_CODE.get(w["weather_code"], "mild")
    rain = w["rain_prob_pct"]
    rain_note = ""
    if rain >= 60:
        rain_note = f" Rain is likely today, with a {rain} percent chance."
    elif rain >= 30:
        rain_note = f" There's a {rain} percent chance of rain."
    else:
        rain_note = " No rain expected."
    return (
        f"Currently {w['current_temp_c']} degrees Celsius and {cond}. "
        f"Today's high near {w['high_c']}, low {w['low_c']}.{rain_note}"
    )


def fetch_yahoo_quote(symbol):
    """Returns dict with current price and previous close. symbol is URL-encoded."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
    data = http_json(url)
    result = data["chart"]["result"][0]
    meta = result["meta"]
    return {
        "price": meta.get("regularMarketPrice") or meta.get("previousClose"),
        "previous_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
        "currency": meta.get("currency", ""),
    }


def fetch_markets():
    sp = fetch_yahoo_quote("%5EGSPC")  # ^GSPC
    nd = fetch_yahoo_quote("%5ENDX")  # ^NDX
    vx = fetch_yahoo_quote("%5EVIX")  # ^VIX
    return {"sp500": sp, "nasdaq100": nd, "vix": vx}


def pct_change(price, prev):
    if not prev:
        return 0.0
    return (price - prev) / prev * 100


def fetch_fear_and_greed():
    """CNN's public dataviz endpoint. Returns dict with score and rating."""
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    try:
        data = http_json(url)
        fg = data.get("fear_and_greed", {})
        score = round(fg.get("score", 50))
        rating = fg.get("rating", "neutral").title()
        return {"score": score, "rating": rating}
    except Exception as e:
        print(f"Warning: F&G fetch failed ({e}). Using neutral fallback.", file=sys.stderr)
        return {"score": 50, "rating": "Neutral"}


# Curated quotes — varied sources, tones, depths
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
    ("Aldous Huxley", "It is a little embarrassing that, after forty-five years of research and study, the best advice I can give people is to be a little kinder to each other."),
    ("Henry David Thoreau", "It's not what you look at that matters, it's what you see."),
    ("Virginia Woolf", "Arrange whatever pieces come your way."),
    ("Albert Einstein", "Look deep into nature, and then you will understand everything better."),
    ("Hafiz", "The words you speak become the house you live in."),
    ("Etty Hillesum", "Sometimes the most important thing in a whole day is the rest we take between two deep breaths."),
    ("Lin-Manuel Miranda", "Look around, look around, at how lucky we are to be alive right now."),
    ("Confucius", "The man who moves a mountain begins by carrying away small stones."),
    ("Octavio Paz", "Wisdom lies neither in fixity nor in change, but in the dialectic between the two."),
]


def pick_quote_for_date(local_date):
    """Deterministic quote per date so re-runs are stable."""
    seed = int(local_date.strftime("%Y%m%d"))
    rnd = random.Random(seed)
    return rnd.choice(QUOTES)


def compose_brief(local, weather, markets, fg, quote):
    # spoken-number helpers
    def fmt_num(n):
        return f"{n:,.0f}"

    def fmt_pct(p):
        sign = "up" if p > 0 else "down" if p < 0 else "flat"
        if abs(p) < 0.05:
            return "essentially flat"
        return f"{sign} about {abs(p):.1f} percent".replace(".0 percent", " percent")

    # market-day label
    wd = local.weekday()  # Mon=0, Sun=6
    # Saturday=5, Sunday=6, Monday=0 → Friday's Close
    if wd in (5, 6, 0):
        market_label = "Friday's"
    else:
        market_label = "yesterday's"

    sp_price = markets["sp500"]["price"]
    sp_prev = markets["sp500"]["previous_close"]
    nd_price = markets["nasdaq100"]["price"]
    nd_prev = markets["nasdaq100"]["previous_close"]
    vx_price = markets["vix"]["price"]

    sp_pct = pct_change(sp_price, sp_prev)
    nd_pct = pct_change(nd_price, nd_prev)

    vix_label = "calm" if vx_price < 20 else "elevated" if vx_price < 30 else "high"

    weather_line = describe_weather(weather)

    date_words = local.strftime("%A, %B %d, %Y")
    # avoid awkward leading zero
    date_words = date_words.replace(" 0", " ")

    quote_author, quote_text = quote

    text = (
        f"Good morning, Sagee. It's {date_words}. "
        f"In Tel Aviv right now, {weather_line.lower()[0]}{weather_line[1:]} "
        f"Turning to markets — at {market_label} close, the S and P 500 finished at {fmt_num(sp_price)}, {fmt_pct(sp_pct)}. "
        f"The Nasdaq 100 closed at {fmt_num(nd_price)}, {fmt_pct(nd_pct)}. "
        f"The VIX is at {vx_price:.1f}, in {vix_label} territory, "
        f"and CNN's Fear and Greed Index reads {fg['score']} — {fg['rating'].lower()}. "
        f"Today's thought is from {quote_author}: {quote_text} "
        f"Have a wonderful {local.strftime('%A')}."
    )
    return text, market_label


def main():
    local, offset_str, offset_hours = israel_today()
    date_str = local.strftime("%Y-%m-%d")
    out = BRIEFS_DIR / f"{date_str}.json"
    if out.exists():
        print(f"Brief already exists for {date_str} ({out}); leaving it untouched.")
        return

    print(f"Building brief for {date_str} (Israel local time: {local.isoformat()})")

    weather = fetch_weather()
    print(f"Weather: {weather}")

    markets = fetch_markets()
    print(f"Markets: SP500={markets['sp500']['price']:.2f}  NDX={markets['nasdaq100']['price']:.2f}  VIX={markets['vix']['price']:.2f}")

    fg = fetch_fear_and_greed()
    print(f"F&G: {fg}")

    quote = pick_quote_for_date(local)
    print(f"Quote: {quote[0]} — {quote[1][:60]}...")

    spoken_text, market_label = compose_brief(local, weather, markets, fg, quote)

    # pub_date: 7am Israel time
    pub_dt = local.replace(hour=7, minute=0, second=0, microsecond=0)
    # rebuild as aware datetime with the right offset
    aware = pub_dt.replace(tzinfo=timezone(timedelta(hours=offset_hours)))
    pub_date = format_datetime(aware)

    title = local.strftime("%A, %B %d, %Y").replace(" 0", " ")
    description = f"Tel Aviv weather, {market_label} market close, and a thought to start your day."

    brief = {
        "date": date_str,
        "title": title,
        "description": description,
        "pub_date": pub_date,
        "spoken_text": spoken_text,
    }

    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(brief, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out.relative_to(REPO_ROOT)}")
  