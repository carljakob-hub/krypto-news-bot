"""Wartet bis zur nächsten vollen Stunde, damit das Markt-Update pünktlich kommt.

GitHub startet geplante Läufe oft 10–30 Minuten zu spät, besonders zur vollen Stunde.
Darum startet der Workflow jede Stunde um :35 und dieses Skript wartet bis hh:00.
Gesendet wird nur zu den Berliner Uhrzeiten in POST_HOURS (sommer- und winterzeitfest).

Exit-Code 0 = jetzt senden, 78 = in dieser Stunde nicht senden.
"""

import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")
POST_HOURS = {int(h) for h in os.environ.get("POST_HOURS", "2,6,10,14,18,22").split(",")}
MAX_WAIT = timedelta(minutes=28)   # früher gestartet: höchstens so lange warten
MAX_LATE = timedelta(minutes=20)   # zu spät gestartet: trotzdem noch senden
SKIP = 78


def main() -> int:
    now = datetime.now(TZ)
    hour = now.replace(minute=0, second=0, microsecond=0)
    target = hour + timedelta(hours=1) if now.minute >= 30 else hour
    if target.hour not in POST_HOURS:
        print(f"{target:%H:%M} ist keine Sendezeit – nichts zu tun")
        return SKIP
    wait = target - now
    if wait > MAX_WAIT or -wait > MAX_LATE:
        print(f"Start {now:%H:%M} passt nicht zu {target:%H:%M} – übersprungen (ein anderer Lauf übernimmt)")
        return SKIP
    if wait.total_seconds() > 0:
        print(f"Warte {int(wait.total_seconds())} s bis {target:%H:%M} Uhr")
        time.sleep(wait.total_seconds() + 5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
