# Sicherheits-Richtlinie

## Unterstützte Versionen

Aktuell werden ausschließlich folgende Versionen mit Sicherheits-Updates versorgt:

| Version | Unterstützt        |
| ------- | ------------------ |
| Main    | :white_check_mark: |

## Schwachstellen melden

Wir nehmen die Sicherheit dieses Projekts ernst. Wer eine Schwachstelle entdeckt, möge sie bitte so zeitnah wie möglich melden.

### Meldewege

Bitte **keine** öffentlichen GitHub-Issues für Sicherheitsmeldungen verwenden. Stattdessen einen der folgenden Kanäle nutzen:

1. **GitHub Security Advisories**: Falls für dieses Repository aktiviert, die Schaltfläche „Report a vulnerability" im Reiter *Security* benutzen.
2. **E-Mail**: Falls GitHub Security Advisories nicht infrage kommen, eine E-Mail an die Repository-Inhaber:innen senden (Kontaktdaten typischerweise im GitHub-Profil).

### Was die Meldung enthalten sollte

Bitte so viele Informationen wie möglich beilegen, damit das Problem reproduzierbar und behebbar wird:

- Beschreibung der Schwachstelle.
- Schritte zur Reproduktion.
- Betroffene Versionen.
- Mögliche Auswirkung oder Proof of Concept (PoC).

### Reaktionszeit

Wir bestätigen den Eingang in der Regel innerhalb von 48 Stunden und liefern eine grobe Einschätzung des Zeitfensters für die Behebung. Vielen Dank für die kooperative, verantwortungsvolle Offenlegung.

### Sicherheitsmodell im Überblick

Eine Übersicht über die im Projekt etablierten Schutzmechanismen (SSRF-Schutz via `request_safe`, atomare Schreiboperationen, Path-Guard auf `docs/`/`data/`/`log/`, Secret-Scanner, Quota-Schichten der VOR/VAO-Anbindung) steht in [`AGENTS.md`](AGENTS.md) und in [`docs/architecture.md`](docs/architecture.md) §§2–4.
