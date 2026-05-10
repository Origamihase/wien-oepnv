# Performance- & Monitoring-Konzept (Vorschlag)

> ⚠️ **Status: Konzeptpapier / Roadmap.** Dieses Dokument skizziert
> einen vorgeschlagenen Ausbau der Performance-Telemetrie für den
> Wien ÖPNV Feed. Die hier genannten Artefakte
> (`log/performance-metrics.jsonl`, `log/performance-warnings.log`,
> ein `make profile-feed`-Target, ein eigenes `docs/how-to/profiling.md`)
> sind **noch nicht implementiert** und dürfen nicht als beschriebene
> Features verwechselt werden. Der aktuelle Beobachtbarkeits-Stack
> beschränkt sich auf:
>
> * `log/errors.log` und `log/diagnostics.log` (rotierende Textlogs;
>   konfigurierbar über `LOG_DIR`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`,
>   `LOG_FORMAT`),
> * `docs/feed-health.md` (Markdown-Bericht nach jedem Feed-Build,
>   lokal, **nicht** committed) und
> * die statistischen CSV-Ledger unter `data/stats/`, aus denen
>   `scripts/generate_markdown_stats.py` täglich `docs/statistik.md`
>   regeneriert.
>
> Bevor weitere Performance-Features implementiert werden, sollte
> dieses Dokument vom Hauptprojekt aktualisiert oder durch eine
> realisierte Variante ersetzt werden.

Ziel des Konzepts: Auffälligkeiten bei Latenzen oder Feed-
Generierung frühzeitig erkennen und datenbasiert optimieren.

## 1. Geplante Metrik-Erfassung

- **Feed-Laufzeiten**: Jeder Aufruf von `python -m src.cli feed build`
  protokolliert die Gesamtdauer sowie Zeiten der Provider-Abschnitte
  in einer geplanten Metrik-Datei `log/performance-metrics.jsonl`. Die
  Datei verwendet JSON-Lines, damit sie leicht ingestiert werden kann.
- **Provider-Sicht**: Zusätzlich zu den bisherigen Warn-Logs sollen
  die Wartezeiten einzelner API-Anfragen erfasst werden. Daraus
  ließe sich erkennen, ob Timeouts oder Retries zunehmen.
- **Cache-Trefferquote**: Die `cache`-Utilities sollen loggen, ob
  Datensätze aus dem Cache oder von der Quelle stammen. Dadurch
  bliebe sichtbar, wie gut die konfigurierten Cache-Laufzeiten
  greifen.

## 2. Geplante Alarmierung

- **Warnschwellen**: Überschreiten die Feed-Laufzeiten einen
  konfigurierbaren Schwellenwert (`FEED_RUN_WARN_THRESHOLD`), würde
  ein dedizierter Log-Eintrag unter `log/performance-warnings.log`
  erzeugt. Dieser könnte von gängigen Log-Monitoring-Lösungen
  (z. B. Loki, ELK) verarbeitet werden.
- **Hook-System**: Für kritische Abweichungen sollte sich ein eigener
  Callback registrieren lassen (`PERFORMANCE_ALERT_HOOK`). Sobald die
  Laufzeit den definierten Höchstwert überschritte, ruft die
  Anwendung den Hook auf (z. B. zur Versendung eines Webhooks oder
  einer ChatOps-Nachricht).

## 3. Geplante Dashboards

- **Zeitreihen**: Aus den JSON-Lines ließen sich Dashboards in
  Grafana oder einer ähnlichen Lösung aufbauen. Empfohlene Panels:
  Feed-Gesamt­dauer, Provider-Dauern, Anzahl der Cache-Treffer,
  Anzahl der Warnungen.
- **Fehlerverfolgung**: In `log/errors.log` erfasste Fehler ließen
  sich mit den Performance-Kurven kombinieren, um Korrelationen
  zwischen Ausfällen und Latenzspitzen zu finden.

## 4. Geplante Profiling-Workflows

- **Lokale Analysen**: Für tiefergehende Profiling-Sessions soll
  ein Makefile-Target `make profile-feed` zur Verfügung stehen,
  das `python -m cProfile` mit aussagekräftigen Parametern ausführt
  und die Auswertung in `log/profile/latest.pstat` ablegt.
- **Vergleichsläufe**: Die Ergebnisse sollen mit `snakeviz` oder
  `pyprof2calltree` visualisiert werden. Eine separate Anleitung
  (geplant: `docs/how-to/profiling.md`) würde die Aufrufe
  beschreiben.

## 5. Betriebliches Vorgehen (Zielzustand)

1. **Regelmäßige Überprüfung**: Performance-Dashboards täglich
   prüfen und Warnungen automatisieren.
2. **Incident-Response**: Bei Alarmen zunächst die betroffenen
   Provider prüfen. Erhöhte Latenzen würden in der JSONL-Datei mit
   konkretem Zeitstempel dokumentiert.
3. **Kontinuierliche Anpassung**: Schwellenwerte und Cache-Strategie
   mindestens vierteljährlich evaluieren und bei Bedarf anpassen.

Mit diesem Ausbau entstünde ein belastbares Fundament, um die
Feed-Erzeugung nicht nur funktional, sondern auch hinsichtlich
Performance und Zuverlässigkeit kontinuierlich zu überwachen.
Bis zur Umsetzung sind die unter `log/`, `docs/feed-health.md`
und `data/stats/` verfügbaren Artefakte die Basis für
Performance-Untersuchungen.
