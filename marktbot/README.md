# Markt-Update

Postet alle 4 Stunden in die Telegram-Gruppe: Preis, 24h-Hoch und 24h-Tief für Bitcoin, Ethereum,
Solana, BNB, XRP und Gold (über PAX Gold, 1 Token = 1 Feinunze) sowie die Top 3 Gewinner und Verlierer
der 100 größten Coins (24h, ohne Stablecoins). Daten: CoinGecko, kostenlos, kein API-Key.

Nutzt dieselben Secrets wie der Newsbot (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). Optional:
- `MARKT_BOT_TOKEN`: Token eines eigenen Bots (z. B. „Market Update“), sonst postet der Newsbot-Bot
- `MARKT_TOPIC_ID`: Thema in der Gruppe, in das die Updates gehen (sonst „Allgemein“)
- `MARKT_CHAT_ID`: andere Gruppe als der Newsbot

Testen: Actions → marktupdate → Run workflow. Lokal: `DRY_RUN=1 python marktbot/bot.py`.
Intervall: `cron`-Zeile in `.github/workflows/marktupdate.yml`.
