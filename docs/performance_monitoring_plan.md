# Performance- & Monitoring-Konzept für den Wien ÖPNV Feed

Dieses Dokument beschreibt die Ergänzungen, mit denen das Projekt um
regelmäßige Performance-Analysen und Monitoring-Dashboards erweitert
wurde. Ziel ist es, Auffälligkeiten bei Latenzen oder Feed-Generierung
frühzeitig zu erkennen und datenbasiert zu optimieren.

## 1. Metrik-Erfassung

- **Feed-Laufzeiten**: Jeder Aufruf von `build_feed.py` protokolliert die
  Gesamtdauer sowie Zeiten der Provider-Abschnitte in einer neuen
  Metrik-Datei `log/performance-metrics.jsonl`. Die Datei verwendet
  JSON-Lines, damit sie leicht ingestiert werden kann.
- **Provider-Sicht**: Zusätzlich zu den bisherigen Warn-Logs werden die
  Wartezeiten einzelner API-Anfragen erfasst. Daraus lässt sich erkennen,
  ob Timeouts oder Retries zunehmen.
- **Cache-Trefferquote**: Die `cache`-Utilities loggen, ob Datensätze aus
  dem Cache oder von der Quelle stammen. Dadurch bleibt sichtbar, wie gut
  die konfigurierten Cache-Laufzeiten greifen.

## 2. Alarmierung

- **Warnschwellen**: Überschreiten die Feed-Laufzeiten einen konfigurier-
  baren Schwellenwert (`FEED_RUN_WARN_THRESHOLD`), wird ein dedizierter
  Log-Eintrag unter `log/performance-warnings.log` erzeugt. Dieser kann
  von gängigen Log-Monitoring-Lösungen (z. B. Loki, ELK) verarbeitet
  werden.
- **Hook-System**: Für kritische Abweichungen lässt sich ein eigener
  Callback registrieren (`PERFORMANCE_ALERT_HOOK`). Sobald die Laufzeit
  den definierten Höchstwert überschreitet, ruft die Anwendung den Hook
  auf (z. B. zur Versendung eines Webhooks oder einer ChatOps-Nachricht).

## 3. Dashboards

- **Zeitreihen**: Aus den JSON-Lines lassen sich Dashboards in Grafana
  oder einer ähnlichen Lösung aufbauen. Empfohlene Panels: Feed-Gesamt-
  dauer, Provider-Dauern, Anzahl der Cache-Treffer, Anzahl der Warnungen.
- **Fehlerverfolgung**: In `log/errors.log` erfasste Fehler lassen sich
  mit den Performance-Kurven kombinieren, um Korrelationen zwischen
  Ausfällen und Latenzspitzen zu finden.

## 4. Profiling-Workflows

- **Lokale Analysen**: Für tiefergehende Profiling-Sessions steht ein
  Makefile-Target `make profile-feed` zur Verfügung, das `python -m
  cProfile` mit aussagekräftigen Parametern ausführt und die Auswertung
  in `log/profile/latest.pstat` ablegt.
- **Vergleichsläufe**: Die Ergebnisse können mit `snakeviz` oder `pyprof2calltree`
  visualisiert werden. Eine Anleitung zum Aufruf befindet sich in
  `docs/how-to/profiling.md`.

## 5. Betriebliches Vorgehen

1. **Regelmäßige Überprüfung**: Performance-Dashboards täglich prüfen und
   Warnungen automatisieren.
2. **Incident-Response**: Bei Alarmen zunächst die betroffenen Provider
   prüfen. Erhöhte Latenzen werden in der JSONL-Datei mit konkretem
   Zeitstempel dokumentiert.
3. **Kontinuierliche Anpassung**: Schwellenwerte und Cache-Strategie
   mindestens vierteljährlich evaluieren und bei Bedarf anpassen.

Mit diesen Ergänzungen existiert nun ein belastbares Fundament, um die
Feed-Erzeugung nicht nur funktional, sondern auch hinsichtlich
Performance und Zuverlässigkeit kontinuierlich zu überwachen.
