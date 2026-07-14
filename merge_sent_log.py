"""
Combina sent_alerts.json (local, recién escrito por esta corrida) con la
versión más reciente del mismo archivo en origin/main (guardada por otra
corrida que pudo haber terminado casi al mismo tiempo, dado que el bot
corre cada 5 minutos). Para cada ticker, se queda con la fecha más reciente.

Usado por .github/workflows/scan.yml antes de hacer commit/push, para
evitar conflictos de git cuando dos corridas se superponen.
"""

import json
import sys
from datetime import datetime


def load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def main():
    local_path = "sent_alerts.json"
    remote_path = "/tmp/remote_sent_alerts.json"

    local = load(local_path)
    remote = load(remote_path)

    merged = dict(remote)
    for symbol, ts in local.items():
        actual = merged.get(symbol)
        if actual is None:
            merged[symbol] = ts
            continue
        try:
            if datetime.fromisoformat(ts) > datetime.fromisoformat(actual):
                merged[symbol] = ts
        except Exception:
            merged[symbol] = ts

    with open(local_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
    sys.exit(0)
