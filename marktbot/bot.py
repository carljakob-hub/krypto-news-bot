"""Telegram-Bot: postet Hoch/Tief von großen Krypto-Coins und Gold
sowie die Top 3 Gewinner und Verlierer in eine Telegram-Gruppe.

Daten: CoinGecko (kostenlos, kein API-Key nötig).
Gold: PAX Gold (PAXG), 1 Token = 1 Feinunze Gold, folgt dem Goldpreis.

Benötigte Umgebungsvariablen:
  TELEGRAM_BOT_TOKEN  Token von @BotFather
  TELEGRAM_CHAT_ID    ID der Gruppe (z. B. -1001234567890)
Optional:
  TELEGRAM_TOPIC_ID   Thema (Topic) in der Gruppe, in das gepostet wird
  WATCHLIST           CoinGecko-IDs, kommagetrennt
  TOP_N_UNIVERSE      Wie viele der größten Coins für Gewinner/Verlierer (Standard 100)
  DRY_RUN=1           Nachricht nur ausgeben, nicht senden
"""

import os
import sys
from datetime import datetime, timezone

import requests

COINGECKO = "https://api.coingecko.com/api/v3/coins/markets"

DEFAULT_WATCHLIST = "bitcoin,ethereum,solana,binancecoin,ripple,pax-gold"

# Stablecoins und Gold-Token werden bei Gewinnern/Verlierern ignoriert
EXCLUDE_FROM_MOVERS = {
    "tether", "usd-coin", "dai", "first-digital-usd", "ethena-usde",
    "usds", "paypal-usd", "true-usd", "frax", "usdd", "tether-gold",
    "pax-gold", "binance-usd", "susds", "usd1-wlfi", "ripple-usd",
    "wrapped-bitcoin", "staked-ether", "wrapped-steth", "weth",
}

NAMES = {"pax-gold": "Gold (PAXG)"}


def fetch_markets(params):
    base = {"vs_currency": "usd", "price_change_percentage": "24h"}
    base.update(params)
    resp = requests.get(COINGECKO, params=base, timeout=30)
    resp.raise_for_status()
    return resp.json()


def usd(value):
    if value is None:
        return "–"
    if value >= 1000:
        return f"${value:,.0f}".replace(",", ".")
    if value >= 1:
        return f"${value:,.2f}"
    return f"${value:.4f}"


def pct(value):
    if value is None:
        return "–"
    arrow = "🟢" if value >= 0 else "🔴"
    return f"{arrow} {value:+.2f}%"


def build_message():
    watchlist = os.environ.get("WATCHLIST", DEFAULT_WATCHLIST)
    universe_size = int(os.environ.get("TOP_N_UNIVERSE", "100"))

    watched = fetch_markets({"ids": watchlist})
    order = [w.strip() for w in watchlist.split(",")]
    watched.sort(key=lambda c: order.index(c["id"]) if c["id"] in order else 999)

    universe = fetch_markets({
        "order": "market_cap_desc",
        "per_page": min(universe_size, 250),
        "page": 1,
    })
    movers = [
        c for c in universe
        if c["id"] not in EXCLUDE_FROM_MOVERS
        and c.get("price_change_percentage_24h") is not None
    ]
    movers.sort(key=lambda c: c["price_change_percentage_24h"], reverse=True)
    gainers = movers[:3]
    losers = movers[-3:][::-1]

    now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    lines = [f"📊 <b>Markt-Update</b> · {now}", "", "<b>Hoch / Tief (24h)</b>"]
    for c in watched:
        name = NAMES.get(c["id"], c["name"])
        lines.append(
            f"<b>{name}</b>: {usd(c['current_price'])} {pct(c.get('price_change_percentage_24h'))}\n"
            f"   ⬆️ {usd(c.get('high_24h'))}  ⬇️ {usd(c.get('low_24h'))}"
        )

    lines += ["", f"🚀 <b>Top 3 Gewinner (24h, Top {universe_size})</b>"]
    for i, c in enumerate(gainers, 1):
        lines.append(f"{i}. {c['name']} ({c['symbol'].upper()}) {pct(c['price_change_percentage_24h'])}")

    lines += ["", f"📉 <b>Top 3 Verlierer (24h, Top {universe_size})</b>"]
    for i, c in enumerate(losers, 1):
        lines.append(f"{i}. {c['name']} ({c['symbol'].upper()}) {pct(c['price_change_percentage_24h'])}")

    lines += ["", "<i>Daten: CoinGecko · keine Anlageberatung</i>"]
    return "\n".join(lines)


def send(text):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    topic = os.environ.get("TELEGRAM_TOPIC_ID", "").strip()
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            **({"message_thread_id": int(topic)} if topic else {}),
        },
        timeout=30,
    )
    if not resp.ok:
        print(resp.text, file=sys.stderr)
    resp.raise_for_status()


def main():
    text = build_message()
    if os.environ.get("DRY_RUN") == "1":
        print(text)
    else:
        send(text)
        print("Gesendet.")


if __name__ == "__main__":
    main()
