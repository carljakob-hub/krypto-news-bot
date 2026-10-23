# Krypto-News-Bot – verifizierte Trading-News für Telegram

Ein autonomer Dienst, der rund um die Uhr Krypto- und Makro-Nachrichten aus offiziellen Primärquellen und etablierten Medien abruft, ihre Echtheit nach festen, nachvollziehbaren Regeln prüft, die Trading-Relevanz bewertet und **nur verifizierte, relevante Meldungen** automatisch in eine festgelegte Telegram-Gruppe postet.

> **Grundsatz:** Lieber keine Nachricht als eine falsche. Das Sprachmodell formuliert nur Texte – über den Prüfstatus entscheiden ausschließlich deterministische Regeln auf Basis tatsächlich abgerufener Quellen. Jede Entscheidung wird mit ihren Belegen in der Datenbank gespeichert (`newsbot explain <id>`).

---

## 1. Was sofort funktioniert – und was Einrichtung braucht

| Teil | Status | Was du tun musst |
|---|---|---|
| Recherche über RSS (SEC, Fed, EZB, CFTC, BLS, ESMA, Ethereum Foundation, CoinDesk, The Block, Decrypt, Cointelegraph, Bitcoin Magazine) | ✅ Code fertig und getestet | Feed-URLs einmal mit `newsbot check-sources` auf deinem Server prüfen (in der Entwicklungsumgebung war kein Internetzugriff möglich) |
| Verifizierung (7 Stufen), Relevanzbewertung, Duplikaterkennung, Datenbank, Frequenzbegrenzung, Monitoring, Warnungen | ✅ fertig, durch 110 automatisierte Tests abgedeckt | nichts |
| Testmodus (Beiträge nur in `data/posts.log`) | ✅ Standard, läuft ohne jede Zugangsdaten | nichts |
| Telegram-Versand | ✅ fertig | **Bot-Token** und **Gruppen-ID** anlegen → [docs/TELEGRAM_SETUP.md](docs/TELEGRAM_SETUP.md) |
| Deutsche Zusammenfassungen per Sprachmodell (Claude) | ✅ fertig, optional | **`ANTHROPIC_API_KEY`** setzen. Ohne Schlüssel nutzt der Bot Textvorlagen; „Was ist passiert?“ ist dann ein Auszug aus der Originalquelle (meist Englisch) |
| Docker-Betrieb | ✅ Dockerfile + docker-compose | Server mit Docker; Image wurde hier nicht gebaut (kein Docker-Daemon in der Entwicklungsumgebung) |
| On-Chain-Daten, Token-Unlock-Kalender, exakte ETF-Flusszahlen, X/Twitter | ⚠️ **nicht integriert** | Erfordern kostenpflichtige APIs (z. B. Glassnode, Arkham, Token Unlocks, CoinGlass, X API). Bis dahin werden diese Themen nur erkannt, wenn die konfigurierten Quellen darüber berichten. Siehe Abschnitt 8 |

---

## 2. Architektur (kurz)

```
 RSS-Feeds / offizielle Seiten (nur lesend, HTTPS)
        │  research/   – Abruf mit Retry, ETag/Last-Modified, Größenlimit, Parsing
        ▼
 Meldungen (items) ──► verification/clustering – gleiche Ereignisse quellenübergreifend gruppieren
        ▼
 verification/verifier – 7-stufige Prüfung → VERIFIZIERT / TEILWEISE / UNBESTÄTIGT / WIDERLEGT (+ Belege)
        ▼ (nur VERIFIZIERT, TEILWEISE nur bei HOHER Relevanz)
 analysis/relevance – Themen, Assets, Neuheit, Zeithorizont → HOCH / MITTEL / NIEDRIG
        ▼
 analysis/summarizer – Textvorlage oder Claude (strukturiertes JSON) → analysis/guard prüft jede Zahl, verbietet Prognosen/Links
        ▼
 publishing/formatter → publishing/publisher (Warteschlange, Priorität, Limits, Duplikatschutz) → Telegram / Protokoll
        ▼
 monitoring/ – Kennzahlen, Healthcheck, Warnungen an Admin-Chat
```

Details zu jeder Prüfregel: [docs/ARCHITEKTUR.md](docs/ARCHITEKTUR.md).

## 3. Ordnerstruktur

```
krypto-news-bot/
├── config/
│   ├── config.yaml          # Intervalle, Prüfschwellen, Veröffentlichungsregeln, Kostenbudget
│   └── sources.yaml         # Quellen mit Vertrauensstufe, Eigentümergruppe, offiziellen Domains
├── src/newsbot/
│   ├── cli.py               # Befehle (run, preflight, status, explain …)
│   ├── app.py               # Verdrahtung der Komponenten
│   ├── config.py            # Konfiguration + Geheimnisse aus Umgebungsvariablen
│   ├── models.py            # Datenmodelle
│   ├── textutils.py         # Bereinigung, Ähnlichkeit, Zahlen, Signalwörter
│   ├── pipeline.py          # Ein kompletter Durchlauf
│   ├── scheduler.py         # Dauerbetrieb mit Zeitlimit und Backoff
│   ├── logging_setup.py     # Logging mit Maskierung von Tokens/Schlüsseln
│   ├── db/                  # SQLite + Migrationen (migrations/001_initial.sql)
│   ├── research/            # HTTP-Abruf, RSS-Parser, Collector
│   ├── verification/        # Clustering, Link-Prüfung, Verifier
│   ├── analysis/            # Relevanz, Zusammenfassung, Ausgabeprüfung
│   ├── publishing/          # Formatierung, Telegram-Client, Warteschlange
│   └── monitoring/          # Kennzahlen, Warnungen
├── tests/                   # 110 automatisierte Tests (pytest)
├── docs/                    # Architektur, Telegram-Setup, Betrieb & Sicherheit, Produktions-Checkliste
├── .env.example
├── Dockerfile
└── docker-compose.yml
```

## 4. Installation und Start

### Lokal (zum Ausprobieren)

Voraussetzung: Python 3.11 oder neuer.

```bash
cd krypto-news-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -e .

cp .env.example .env          # zunächst unverändert lassen = Testmodus ohne Telegram
set -a; source .env; set +a   # Umgebungsvariablen laden

pytest                        # alle Tests ausführen
newsbot init-db               # Datenbank anlegen
newsbot check-config          # Konfiguration prüfen
newsbot check-sources         # Erreichbarkeit aller Feeds prüfen
newsbot run-once              # ein Durchlauf; Beiträge landen in data/posts.log
newsbot status                # Monitoring-Bericht
```

### Dauerbetrieb mit Docker (empfohlen)

```bash
cp .env.example .env && nano .env      # Werte eintragen (siehe docs/TELEGRAM_SETUP.md)
chmod 600 .env
mkdir -p data && sudo chown 10001:10001 data   # Container läuft als unprivilegierter Benutzer 10001

docker compose build
docker compose run --rm newsbot newsbot check-sources
docker compose run --rm newsbot newsbot check-telegram --send-test
docker compose up -d                    # startet im Testmodus (NEWSBOT_MODE=test)
docker compose logs -f
docker compose exec newsbot newsbot status
```

Umstellung auf Produktion: [docs/CHECKLISTE_PRODUKTION.md](docs/CHECKLISTE_PRODUKTION.md).

## 5. Befehle

| Befehl | Zweck |
|---|---|
| `newsbot init-db` | Datenbank anlegen bzw. migrieren |
| `newsbot check-config` | Konfiguration und Modus prüfen |
| `newsbot check-sources` | Jede aktivierte Quelle einmal abrufen |
| `newsbot check-telegram [--send-test]` | Token, Gruppen-IDs, Chat-Typ und Bot-Rechte prüfen |
| `newsbot preflight` | Vollprüfung (Konfiguration, Telegram-Rechte der Produktionsgruppe, alle Tests). Nur danach startet der Produktivmodus |
| `newsbot run-once` | Einen Durchlauf ausführen |
| `newsbot run` | Dauerbetrieb |
| `newsbot status [--hours 24]` | Kennzahlen: Meldungen, Prüfstatus, Veröffentlichungen, Quellen, Fehler, Kosten |
| `newsbot health` | Healthcheck (Exit-Code 0 = gesund), wird von Docker genutzt |
| `newsbot explain <id>` | Alle Belege und Gründe zu einem Ereignis anzeigen |

## 6. Beitragsformat

```
🚨 KRYPTONEWS | HOHE RELEVANZ

Thema: SEC genehmigt Optionen auf Spot-Bitcoin-ETFs

Was ist passiert?
…bestätigte Fakten…

Verifizierung: ✅ VERIFIZIERT – bestätigt durch Primärquelle (sec.gov) und 2 unabhängige Bestätigung(en)
Betroffene Assets: BTC
Trading-Relevanz: Hoch
Mögliche Marktauswirkung: …sachliche Einordnung…
Mögliche Reaktionen (Szenarien, keine Prognose):
• Positiv: …
• Negativ: …
• Worauf achten: …
Zeithorizont: Kurz- bis mittelfristig
Risiken und Unsicherheiten: …

Quellen:
• Primärquelle: U.S. SEC – Press Releases
• Unabhängige Bestätigung: CoinDesk
• Unabhängige Bestätigung: The Block

Zeitpunkt: 23.10.2026, 14:00 CEST (Europe/Berlin)
Keine Anlageberatung. …
```

Im Testmodus steht zusätzlich `🧪 [TESTMODUS]` darüber.

### Wann wird gepostet?

Es gibt keinen festen Zeitplan. Der Bot prüft jede Minute, welche Quellen fällig sind (Primärquellen alle 3–5 Min., Medien alle 3–10 Min.), und postet, **sobald** eine Meldung verifiziert und mindestens MITTEL relevant ist. NIEDRIG wird nie veröffentlicht. Ist nichts Wichtiges passiert, bleibt die Gruppe still. Obergrenzen: 4 Beiträge pro Stunde, 24 pro Tag, mind. 4 Min. Abstand (HOCH darf den Abstand überspringen). Alles in `config/config.yaml` einstellbar (`relevance.min_level_to_publish: HOCH` = nur wichtige).

## 7. Kosten (Schätzung, bitte selbst prüfen)

| Posten | Kosten |
|---|---|
| Telegram Bot API | kostenlos |
| RSS-Quellen | kostenlos (Nutzungsbedingungen beachten) |
| Server (1 vCPU, 1 GB RAM reichen) | typischerweise ca. 4–10 € / Monat |
| Sprachmodell | Wird nur für Beiträge aufgerufen, die Prüfung **und** Relevanzfilter bestanden haben. Pro Beitrag ca. 2.000–3.000 Eingabe- und 300–500 Ausgabe-Tokens. Mit `claude-opus-5-5` (4 $/20 $ pro Mio. Tokens) ≈ 0,02 $ pro Beitrag, bei maximal 24 Beiträgen/Tag ≈ 0,50 $/Tag. Mit `claude-haiku-5-5` (0,10 $/0,50 $) ein Bruchteil davon. Harte Obergrenze über `llm.daily_budget_usd`, danach automatisch Textvorlagen |

`newsbot status` zeigt die tatsächlich gemessenen Token-Kosten (aus den API-Antworten × konfigurierte Preise). Hosting-Kosten erscheinen nur, wenn du `monitoring.hosting_cost_usd_per_month` einträgst – sonst steht dort „nicht konfiguriert“.

## 8. Grenzen und offene Integrationsschritte

Ehrlich markiert, damit nichts als fertig gilt, was es nicht ist:

1. **Feed-URLs ungetestet gegen das echte Internet.** Die Entwicklungsumgebung hatte keinen Zugriff auf die Quellen. `newsbot check-sources` zeigt, welche funktionieren; fehlerhafte in `config/sources.yaml` korrigieren oder mit `enabled: false` abschalten.
2. **Keine kostenpflichtigen Datenquellen.** On-Chain-Bewegungen, Token-Unlock-Termine und ETF-Flusszahlen werden derzeit nur über Nachrichtenmeldungen erkannt. Eine neue Quelle wird als weiterer Collector-Typ ergänzt (Schnittstelle: liefert `RawItem`s, siehe `research/collector.py`).
3. **Keine sozialen Medien.** Die Stufe `social` ist vorbereitet, zählt aber bewusst nie als Bestätigung. Bilder/Screenshots werden nicht analysiert; Meldungen, die sich nur darauf stützen, bleiben UNBESTÄTIGT.
4. **Ereignisbasierte Verarbeitung** erfolgt über häufiges, günstiges Polling (pro Quelle eigenes Intervall, bedingte HTTP-Abrufe). Push-Mechanismen wie WebSub sind nicht eingebaut.
5. **Duplikaterkennung ist lexikalisch** (normalisierte Schlüsselwörter, Synonyme, Wortstämme), nicht semantisch. Sehr unterschiedlich formulierte Meldungen können als zwei Ereignisse gelten – der Duplikatschutz beim Veröffentlichen fängt einen Teil davon zusätzlich ab.
6. **Börsen-Ankündigungen** (Binance, Coinbase usw.) sind nicht als Quelle eingetragen, da es keine stabilen offiziellen RSS-Feeds gibt. Ihre Domains gelten aber als Primärdomains, wenn Medien darauf verlinken (dann wird das verlinkte Dokument abgerufen und geprüft).
7. **Docker-Image** wurde nicht gebaut (kein Docker-Daemon verfügbar); Python-Paket, CLI, Preflight und alle Tests wurden lokal ausgeführt.

## 9. Weitere Anleitungen

- [docs/SCHNELLSTART.md](docs/SCHNELLSTART.md) – **Schritt für Schritt zum laufenden Bot (hier anfangen)**
- [docs/ARCHITEKTUR.md](docs/ARCHITEKTUR.md) – Prüfregeln, Relevanzbewertung, Datenbank
- [docs/TELEGRAM_SETUP.md](docs/TELEGRAM_SETUP.md) – Bot erstellen, zur Gruppe hinzufügen, IDs ermitteln
- [docs/BETRIEB_UND_SICHERHEIT.md](docs/BETRIEB_UND_SICHERHEIT.md) – sichere Konfiguration, Dauerbetrieb, Updates, Backups
- [docs/CHECKLISTE_PRODUKTION.md](docs/CHECKLISTE_PRODUKTION.md) – Übergang vom Test- in den Produktivbetrieb

*Keine Anlageberatung. Das System führt keine Trades aus und hat keinen Zugriff auf Börsenkonten.*
