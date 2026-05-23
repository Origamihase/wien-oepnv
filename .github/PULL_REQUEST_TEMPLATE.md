<!--
Vorlage für Pull Requests im Wien-ÖPNV-Feed-Repository. Die Checkliste
bündelt die Mindestanforderungen aus den fortlaufenden Audits. Nicht
zutreffende Punkte bitte in der Beschreibung kurz begründen.
-->

## Beschreibung der Änderung
<!-- Was wurde geändert? Warum ist diese Änderung notwendig? -->

## Ticket-Referenz
<!-- Falls vorhanden, bitte Issue-Nummer verlinken (z. B. #123) -->

## Bewusst ausgeklammert
<!--
Optional. Was wurde bei der Arbeit auffällig, aber bewusst nicht
mitbehoben? Hilft Reviewer:innen, bekannte Baustellen nicht erneut
aufzudecken. Beispiele:
- „C901 für _foo weiterhin 51 — Refactor in eigenem PR"
- „wl_fetch-Tests mocken noch auf falscher Ebene — separater Cleanup"
-->

---

## Review-Checkliste

Diese Checkliste fasst die wiederkehrenden Audit-Schwerpunkte des
Projekts zusammen. Bitte jeden Punkt abhaken **oder** in der
Beschreibung begründen, warum er auf diesen PR nicht zutrifft.

### Sicherheit
- [ ] Kein neuer HTTP-Aufruf umgeht `request_safe` (`src/utils/http.py`).
- [ ] Keine neue `# nosec`-Markierung verdeckt eine echte Schwachstelle
      (Markierungen ausschließlich an Call-Sites mit vertrauenswürdigem
      Input – siehe `docs/architecture.md` §2).
- [ ] Keine neue SSRF-, Redirect- oder DNS-Rebinding-Angriffsfläche.
- [ ] Wenn ein neuer externer Host angesprochen wird: Allowlist
      aktualisiert und Response-`Content-Type` validiert.

### Performance
- [ ] Keine `copy.deepcopy(items)` oder unnötigen Defensiv-Kopien auf
      Hot-Paths der Datenpipeline.
- [ ] Kein neuer O(n²)-Regex-Reparse in paarweisen Schleifen; bei
      mehrfach gelesener Projektion: cachen.
- [ ] Keine `wait()`-gemockten Test-Loops ohne kleinen
      `feed_config.PROVIDER_TIMEOUT`-Patch.

### Statische Analyse
- [ ] `ruff check src/ tests/` ist sauber.
- [ ] `python3 -m mypy --no-pretty src tests` (CI-pinnung 1.10.1) ist sauber.
- [ ] Keine neuen `# type: ignore` / `# noqa`, die eine strukturelle
      Schwäche verbergen.
- [ ] Keine neuen Mypy-Allowlist-Einträge, sofern nicht dokumentiert.

### Komplexität
- [ ] Keine neue Funktion übersteigt **C901 = 15** (durch
      `scripts/check_complexity.py` durchgesetzt).
- [ ] Falls ein Refactor eine Baseline-Funktion unter den Threshold
      bringt, wurde `.c901-baseline.txt` regeneriert
      (`bash scripts/regen_c901_baseline.sh`).
- [ ] Extrahierte Helfer sind nach Möglichkeit reine Funktionen mit
      klar benennbarer Einzelverantwortung.

### `request_safe`-Berührungen
- [ ] Wenn `request_safe` oder einer seiner Helfer angepasst wurde:
      jede betroffene Security-Gate hat eigene Test-Abdeckung (keine
      implizite „der bestehende Test fängt das" -Annahme).
- [ ] Neue Security-Helfer beginnen den Docstring mit
      `Mitigates: <Angriffsvektor>`.

### Resilienz
- [ ] Pfade für hostile Payloads liefern `{}` / `[]` / `None`
      (fail-closed) und brechen den Cron-Job nicht ab.
- [ ] Für neue Payload-Parser werden `RecursionError`,
      `JSONDecodeError` und `ET.ParseError` gemeinsam abgefangen.
- [ ] Neue Provider nutzen `CircuitBreaker` (`src/utils/circuit_breaker.py`)
      oder begründen explizit, warum nicht.
- [ ] Provider-Bulkhead bleibt erhalten: eine Provider-Exception nimmt
      die Items der anderen Provider nicht mit in den Abgrund.

### Dokumentation
- [ ] Neue öffentliche Funktionen / Klassen tragen PEP-257-Docstrings
      mit `Args`, `Returns`, `Raises`.
- [ ] Architektur-relevante Änderungen aktualisieren
      `docs/architecture.md` (samt zugehörigem Mermaid-Diagramm,
      sofern betroffen).

---

## Lokale Verifikation

Vor dem Push bitte ausführen — spiegelt die CI exakt:

```bash
pre-commit run --all-files            # ruff + mypy + bandit + scan_secrets
python scripts/run_static_checks.py   # vollständiger CI-Parity-Check
python scripts/check_complexity.py    # C901-Gate gegen Baseline
python -m pytest --timeout=120        # vollständige Test-Suite
```
