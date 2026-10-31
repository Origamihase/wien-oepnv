# KI-Wächter

Die tägliche Prüfung der Feed-Anzeige im Projekt selbst: Claude liest die zehn
Meldungen des Feeds neben ihren Quelltexten und meldet, was der TV-Zuschauer
falsch lesen würde. Der Wächter ändert den Feed nie.

Entscheidung des Betreibers vom 2026-10-31: Die Regeln bleiben die Feed-Kette,
eine KI meldet nur. Die Gründe und die Messung dazu stehen in
[`architecture.md`](../architecture.md), Abschnitt „KI-Wächter“.

## Ablauf

| Schritt | Wer | Was |
| --- | --- | --- |
| Auslöser | `.github/workflows/ki-waechter.yml`, Job `gate` | nach jedem Update-Lauf; weiter nur, wenn er erfolgreich war, es 15:00 Wiener Zeit oder später ist und heute noch kein Lauf in `data/side_steps/ki-waechter.json` steht |
| Eingabe | `scripts/ki_waechter.py prepare` | `docs/feed.xml`, `docs/feed.en.xml`, `cache/*/events.json`; je Meldung DE, EN, das eigene Cache-Ereignis und bis zu sechs weitere Ereignisse derselben Linien; schreibt `.ki-waechter/eingabe.md` und `eingabe.json` |
| Lesen | Claude Code (`claude -p`, Modell `KI_WAECHTER_MODEL`) | Prompt `scripts/ki_waechter_prompt.md`; Werkzeuge nur Read, Grep, Glob; Antwort ein JSON-Objekt |
| Prüfen | `scripts/ki_waechter.py verify` | jedes Zitat wörtlich gegen Feed und Quellen; schreibt den Bericht und `ergebnis.json` |
| Ablage | `scripts/publish_to_main.sh` | `docs/archive/audits/ki-waechter-YYYY-MM-DD.md` und `data/side_steps/ki-waechter.json` nach `main` |
| Meldung | `gh issue create` | nur bei belegten Funden ein Issue, Label `ki-waechter` |

Von Hand: Actions → „KI-Wächter“ → „Run workflow“. Ohne Eingabe prüft er den
aktuellen Stand und veröffentlicht; mit einem Commit-Hash prüft er dessen Feed
und veröffentlicht nichts (Bericht in der Zusammenfassung des Laufs und als
Artefakt).

## Fundklassen

Geschlossene Liste, gleich wie im letzten Jules-Prompt vom 2026-10-30:

| Klasse | Inhalt | Feed |
| --- | --- | --- |
| F1 | dieselbe Sache zweimal | DE, zwei Meldungen mit je einem Zitat |
| F2 | Kernaussage abgeschnitten | DE oder EN |
| F3 | Fakt weicht von der Quelle ab | DE, mit wörtlichem Quellzitat |
| F4 | Titel widerspricht dem Text | DE oder EN |
| F5 | kaputter Text (geklebte Wörter, HTML-, Platzhalterreste, Doppelungen) | DE oder EN |
| F6 | Englisch unübersetzt, Eigenname übersetzt, Sinn verändert | EN |

Was entschieden ist (Kürzung auf 180 Zeichen, Zeitzeile, Tippfehler der
Betreiber, feste EN-Formen), steht im Prompt als Nicht-Fund. Stil und
Verbesserungsideen meldet der Wächter nie.

## Warum ein Fund belegt sein muss

Michael, 2026-10-30: „Falsche Funde stören mich am meisten.“ Deshalb prüft das
Skript jeden Fund, bevor er zählt:

- Das Feed-Zitat steht wörtlich (nach Leerraum-Angleich) in genau dieser
  Meldung im genannten Feed, mindestens drei Zeichen.
- F3: Das Quellzitat steht wörtlich in den Quellen dieser Meldung.
- F1: Die zweite Meldung ist eine andere und enthält ihr Zitat.
- Die Klasse passt zum Feed (F6 nur EN, F1 und F3 nur DE), ein erwarteter Text
  ist angegeben.

Ein Fund, der eine Prüfung nicht besteht, steht unter „Nicht belegt“ im
Bericht und öffnet kein Issue. Kopf, Zahlen, GUIDs und die Tabelle der
geprüften Meldungen schreibt das Skript, nicht das Modell.

## Sicherheit

- Das Modell hat nur Lese-Werkzeuge. Es kann nichts schreiben, nichts
  ausführen, nichts aus dem Netz holen und nichts veröffentlichen; das tun
  die Schritte danach, mit festem Inhalt.
- Nur der Schritt „Claude reads and compares“ bekommt
  `CLAUDE_CODE_OAUTH_TOKEN`.
- Die Texte der Betreiber sind Daten, keine Anweisungen; der Prompt sagt das,
  und die Eingabe rahmt sie mit `<<<DATEN … DATEN>>>`.
- Freitext des Modells (Erwartet, Begründung, Vermutungen) wird vor dem
  Bericht auf eine Zeile gekürzt; was wie ein Zugangsschlüssel aussieht, wird
  entfernt, Markup unschädlich gemacht.

## Ausfall

Kann der Wächter nicht prüfen (Token leer oder abgelaufen, Modell bricht ab,
Antwort ohne JSON), ist der Lauf rot, und die Prüfung `Nebenschritte` im
Health check wird rot. Rot wird sie auch, wenn 36 Stunden kein Lauf
aufgezeichnet wurde. Am selben Tag startet er nicht noch einmal von selbst;
von Hand geht es jederzeit.

## Probelauf im Pull Request

Ändert ein Pull Request `scripts/ki_waechter.py`, den Prompt oder den
Workflow, läuft der Wächter ohne Veröffentlichung an drei Feed-Ständen:

| Commit | Stand | Erwartet |
| --- | --- | --- |
| `001e47c` | 27.10.2026 16:00 | F6 „algae path“ (Algenweg, behoben in #2075); F1 40A „Busse halten Liechtensteinstraße 39, Liechtensteinstraße 38“ neben der Verlegung Bauernfeldplatz |
| `9e60b8d` | 30.10.2026 15:30 | F6 „All Saints' Worship“ (Allerheiligenverkehr, behoben in #2091) |
| `HEAD` | aktueller Feed | keine falschen Funde |

Der Prompt nennt keinen dieser Fälle (`tests/test_ki_waechter.py`).

## Kosten

Der Wächter nutzt das vorhandene Secret `CLAUDE_CODE_OAUTH_TOKEN` (wie
Code-Review und `@claude`), kein eigenes Abrechnungskonto. Ein Lauf liest
etwa 15 bis 30 KB Eingabe und dauert wenige Minuten.
