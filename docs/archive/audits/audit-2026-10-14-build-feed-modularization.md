# Architektur- und Modularisierungs-Audit: src/build_feed.py
Datum: 2026-10-14

## 1. Anatomie & Zeilen-Vermessung von `src/build_feed.py`

Die Datei `src/build_feed.py` ist historisch als monolithisches "God-Modul" gewachsen.

* **Exakte Gesamtzeilenzahl:** 11.454 Zeilen

**Logische Funktionsblöcke:**
- **Imports and Setup:** L1–L1856 (1856 Zeilen, 16.2%)
- **Statische Datenstrukturen, Wörterbücher und Glossare:** L1857–L3771 (1915 Zeilen, 16.7%)
  - Enthält Übersetzungs-Mappings (`_GLOSSARY_BASE`, `_GLOSSARY_BY_SOURCE`), Regex-Listen für Entitäten und Masking.
- **Übersetzungs-, Masking- und Lokalisierungs-Logik:** L3772–L6126 (2355 Zeilen, 20.6%)
  - Enthält die Anwendung von Neural Machine Translation (MarianMT), Schutz von Platzhaltern (`_apply_domain_glossary`, `_mask_entities`, `_unmask_entities`) und Lokalisierungs-Feinschliff (Artikelanpassung, `_translate_text_attempt`).
- **Event-Aggregation, State-/Cache-Handling (Haupt-Orchestrierung):** L6127–L10197 (4071 Zeilen, 35.5%)
  - Umfasst den Abruf der Feeds (`_collect_items`), Caching-Mechanismen (`_load_state`, `_save_state`), Entprellung/Deduplizierung (`_dedupe_items`, `_summarize_duplicates`, Ticker-Merges).
- **Textaufbereitung, Bereinigung, Kürzung:** L10198–L10506 (309 Zeilen, 2.7%)
  - Beinhaltet String-Kürzungen (`_truncate_summary_180`), Zusammensetzung von Texten (`_compose_description`, `_lead_with_fragment`).
- **XML-Generierung, CDATA-Handling und Datei-I/O:** L10507–L11454 (948 Zeilen, 8.3%)
  - Beinhaltet das Schreiben von RSS/Atom Feeds (`_format_item_content`, `_emit_item`, `_make_rss`).

## 2. Kopplungs- und Test-Abhängigkeitsanalyse (Breaking-Change-Risiko)

Das Verzeichnis `tests/` und `src/` weist über 147 Dateien auf, die `src.build_feed` importieren. Zahlreiche Tests greifen direkt auf private Funktionen (`_...`) zu, um das komplexe Verhalten isoliert zu prüfen.

**Zwingend beizubehaltende Re-Exports in `src/build_feed.py` (Auszug relevanter Symbole):**
Damit bestehende Tests und Workflows ohne Anpassungen in einer ersten Stufe der Modularisierung weiter funktionieren, müssen nach der Auslagerung sämtliche privat und öffentlich deklarierte Funktionen im Monolithen re-exportiert werden (z. B. `from src.feed.translation import _apply_domain_glossary`).
Dazu zählen unter anderem:
- Konstanten: `_TRANSLATION_CACHE_EPOCH`, `_GLOSSARY_PLACEHOLDER_FORMAT`, `_ENTITY_PLACEHOLDER_FORMAT`, `_STREET_SUFFIX_RE`
- Logik-Funktionen: `_truncate_summary_180`, `_apply_domain_glossary`, `_mask_entities`, `_unmask_entities`, `_compose_description`, `_lead_with_fragment`, `_drop_passed_expected_end`, `_reason_only_summary`, `_is_test_message`
- State/I-O: `_load_state`, `_save_state`, `_dedupe_items`, `_format_item_content`, `_make_rss`

**Potenzielle zirkuläre Importe:**
Bisher gibt es keine gravierenden Zirkelbezüge auf Ebene der Provider-Klassen (`src/providers/*`). Eine Auslagerung nach `src/feed/*` und `src/utils/*` ist sicher, solange die extrahierten Module keine Objekte aus `src/build_feed.py` zurück-importieren. `src/cli.py` importiert `build_feed.py`, dies stellt jedoch eine saubere top-down Abhängigkeit dar.

## 3. Ziel-Architektur (Vorschlag für neue Module)

Wir schlagen die Zerlegung von `src/build_feed.py` in folgende klar abgegrenzte Module vor, wobei `src/build_feed.py` am Ende als schlanker Orchestrator (ca. 4.000 Zeilen) verbleibt.

**Modul 1: `src/feed/translation_glossary.py` (Statische Daten)**
- **Zweck:** Auslagerung von Glossar-Strukturen und statischen Regexes für Masking.
- **Inhalt (aus `src/build_feed.py`):** `_GLOSSARY_BASE`, `_GLOSSARY_BY_SOURCE`, Konstanten wie `_STREET_SUFFIX_CORE` (L1857-L3771).
- **Zeilen (neu):** ~1.900 Zeilen.

**Modul 2: `src/feed/translator.py` (ML/NMT Logik)**
- **Zweck:** Kapselung der MarianMT Pipeline, Maskierungs-Algorithmen und Lokalisierung.
- **Inhalt (aus `src/build_feed.py`):** `_apply_domain_glossary`, `_mask_entities`, `_unmask_entities`, `_translate_text`, `_get_translation_pipeline` (L3772-L6126).
- **Zeilen (neu):** ~2.350 Zeilen.

**Modul 3: `src/utils/text_formatting.py` (Text-Utilities)**
- **Zweck:** Zusammenführung allgemeiner Textbereinigungs- und Kürzungslogiken.
- **Inhalt (aus `src/build_feed.py`):** `_truncate_summary_180`, `_compose_description`, `_lead_with_fragment`, `_trim_truncation_tail` (L10198-L10506).
- **Zeilen (neu):** ~300 Zeilen.

**Modul 4: `src/feed/xml_writer.py` (Generierung & I/O)**
- **Zweck:** Feed-Generierung, HTML/CDATA-Escaping und Datei-I/O.
- **Inhalt (aus `src/build_feed.py`):** `_format_item_content`, `_emit_item`, `_make_rss`, `_escape_description_markup` (L10507-L11454).
- **Zeilen (neu):** ~950 Zeilen.

*Zukünftiger `src/build_feed.py` Monolith:* ca. 5.950 Zeilen (inkl. Setup, State-Handling, Event-Aggregation L6127–L10197, und Re-Exports).

## 4. Empfohlene Reihenfolge der Umsetzung (Migrations-Roadmap)

Um Breaking Changes zu vermeiden und die fortlaufende Stabilität zu garantieren, wird folgender Stufenplan empfohlen:

- **Stufe 0 (Aktuell):** Umsetzung der bereits identifizierten Bugfixes aus `audit-2026-10-14.md` am bestehenden Monolithen. Die dort referenzierten Zeilennummern müssen gültig bleiben.
- **Stufe 1:** Extraktion der statischen Daten. Erstellung von `src/feed/translation_glossary.py` (L1857–L3771). In `src/build_feed.py` werden die Symbole importiert (`from src.feed.translation_glossary import *` oder explizit). Dies ist risikofrei.
- **Stufe 2:** Extraktion der Text-Utilities. Erstellung von `src/utils/text_formatting.py` (L10198–L10506). Da diese pure Funktionen sind, bergen sie wenig Risiko.
- **Stufe 3:** Auslagerung des XML-Writers. Erstellung von `src/feed/xml_writer.py` (L10507–L11454).
- **Stufe 4:** Auslagerung der Übersetzungslogik. Erstellung von `src/feed/translator.py` (L3772–L6126).
- **Verifikationsschritt (jeweils nach Stufe 1-4):**
  - Alle Tests in `tests/` müssen zu 100 % grün bleiben, da Symbole für bestehende Tests in `src/build_feed.py` re-exportiert werden (z. B. `from src.utils.text_formatting import _truncate_summary_180`).
  - Linter (`python -m src.cli checks`) muss fehlerfrei passieren.
