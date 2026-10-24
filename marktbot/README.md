# Markt-Update

Postet alle 4 Stunden in die Telegram-Gruppe: Preis, 24h-Hoch und 24h-Tief für Bitcoin, Ethereum,
Solana, BNB, XRP und Gold (über PAX Gold, 1 Token = 1 Feinunze) sowie die Top 3 Gewinner und Verlierer
der 100 größten Coins (24h, ohne Stablecoins). Daten: CoinGecko, kostenlos, kein API-Key.

Nutzt dieselben Secrets wie der Newsbot (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). Optional:
- `MARKT_BOT_TOKEN`: Token eines eigenen Bots (z. B. „Market Update“), sonst postet der Newsbot-Bot
- `MARKT_TOPIC_ID`: Thema in der Gruppe, in das die Updates gehen (Standard: 54 = Thema „Market Update“ in CJ Trades)
- `MARKT_CHAT_ID`: andere Gruppe als der Newsbot

Testen: Actions → marktupdate → Run workflow. Lokal: `DRY_RUN=1 python marktbot/bot.py`.
Sendezeiten: pünktlich zur vollen Stunde um 2, 6, 10, 14, 18 und 22 Uhr (Berliner Zeit), einstellbar über `POST_HOURS` in `.github/workflows/marktupdate.yml`. Der Workflow startet zweimal pro Stunde (:10 und :35) und `marktbot/wait_for_hour.py` wartet bis zur vollen Stunde, weil GitHub geplante Läufe oft 20–30 Min. verspätet startet. Ein Cache-Eintrag je Stunde verhindert doppelte Sendungen.
