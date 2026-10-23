# Checkliste: vom Testbetrieb zum autonomen Produktivbetrieb

Der Produktivmodus lässt sich technisch erst starten, wenn `newsbot preflight` für **genau** die aktuelle Konfiguration und Zielgruppe bestanden wurde. Diese Liste ergänzt das um die menschlichen Prüfschritte.

## A. Einrichtung

- [ ] Server mit Docker eingerichtet, Uhrzeit per NTP synchron (wichtig für die Aktualitätsprüfung)
- [ ] `.env` aus `.env.example` erstellt, `chmod 600 .env`
- [ ] `data/` angelegt und dem Container-Benutzer gegeben (`sudo chown 10001:10001 data`)
- [ ] Bot bei BotFather erstellt, `/setjoingroups` deaktiviert, `/setprivacy` aktiviert
- [ ] Private **Testgruppe** und **Produktionsgruppe** angelegt, Bot jeweils als Admin nur mit „Nachrichten senden“
- [ ] `TELEGRAM_BOT_TOKEN`, `TELEGRAM_TEST_CHAT_ID`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ADMIN_CHAT_ID` eingetragen
- [ ] Optional: `ANTHROPIC_API_KEY` eingetragen, `llm.daily_budget_usd` festgelegt

## B. Technische Prüfungen

- [ ] `docker compose run --rm newsbot pytest -q -p no:cacheprovider` → alle Tests grün
- [ ] `docker compose run --rm newsbot newsbot check-config` → keine Probleme
- [ ] `docker compose run --rm newsbot newsbot check-sources` → defekte Quellen korrigiert oder deaktiviert
- [ ] `docker compose run --rm newsbot newsbot check-telegram --send-test` → alle Chats ✓, Testnachricht kam an

## C. Testbetrieb (mindestens 3–7 Tage, `NEWSBOT_MODE=test`)

- [ ] `docker compose up -d`; Beiträge erscheinen in der Testgruppe und in `data/posts.log`
- [ ] Täglich `newsbot status` geprüft: Abrufe stabil, keine dauerhaften Fehler
- [ ] **Stichprobe von mindestens 20 Beiträgen** gegen die verlinkten Quellen geprüft: Fakten, Zahlen, Datum, Quellenlinks korrekt
- [ ] Mit `newsbot explain <id>` mehrere **verworfene** Ereignisse angesehen: Wurde zu Recht nicht veröffentlicht? Wurde Wichtiges übersehen?
- [ ] Frequenz passt (nicht zu viele / zu wenige Beiträge) – sonst `relevance` und `publishing` in `config.yaml` anpassen
- [ ] Bei aktivem Sprachmodell: Texte sachlich, keine Prognosen; `generated_by` im Protokoll zeigt, wie oft auf Vorlagen zurückgefallen wurde
- [ ] Warnungen getestet: z. B. Token kurz falsch setzen → Warnung im Admin-Chat, danach Entwarnung

## D. Umschalten

- [ ] In `.env`: `NEWSBOT_MODE=production`
- [ ] `docker compose run --rm newsbot newsbot preflight` → „BESTANDEN – Produktivbetrieb freigegeben“
- [ ] `docker compose up -d` und Logs beobachten: „Preflight vom … gültig“, „Starte im Modus 'production'“
- [ ] Ersten echten Beitrag in der Zielgruppe kontrolliert
- [ ] Externe Überwachung des Healthchecks eingerichtet

## E. Laufender Betrieb

- [ ] Wöchentlich `newsbot status --hours 168` und eine Stichprobe von Beiträgen prüfen
- [ ] Nach jeder Änderung an `config/` oder der Zielgruppe: erneut `newsbot preflight` (sonst verweigert der Dienst den Produktivstart)
- [ ] Regelmäßige Datenbank-Backups
- [ ] Token bei Verdacht auf Abfluss sofort bei BotFather widerrufen (`/revoke`)
