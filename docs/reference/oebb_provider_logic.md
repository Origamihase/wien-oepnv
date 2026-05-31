# ÖBB Provider Logic (`src/providers/oebb.py`)

## Zweck des Providers
Der ÖBB Provider hat die Aufgabe, Störungsmeldungen des österreichischen Zugverkehrs (ÖBB) abzurufen und sie einer strengen Filterung zu unterziehen. Ziel ist es, ausschließlich Meldungen in den Feed aufzunehmen, die einen **strikten Wien-Bezug** aufweisen oder für Pendler in und um Wien relevant sind. Irrelevante Meldungen aus dem reinen Fern- oder Auslandsverkehr werden frühzeitig verworfen.

## Die Herausforderung der ÖBB-Rohdaten
Die von der ÖBB bereitgestellten RSS-Feeds weisen oft komplexe, mutierte Titel auf, die sowohl Kategorien, Liniencodes als auch Streckenabschnitte vermischen (z. B. `"REX 51: Störung: Wien Hbf ↔ Wr. Neustadt"`).

Ein simples Splitten (z. B. beim ersten oder letzten Doppelpunkt) ist unzureichend und fehleranfällig, da echte Stationsnamen selbst Doppelpunkte enthalten können (wie etwa `"Wien 10.: Favoriten"`). Ein naiver Split würde diese gültigen Stationsnamen zerstören.

Daher erfolgt das Parsing der Präfixe **iterativ**. In einer `while`-Schleife wird mittels regulärer Ausdrücke jeweils von vorne geprüft, ob der Anfang des Textes ein bekanntes Präfix (wie `"Störung:"` oder `"REX 51:"`) ist. Ist das der Fall, wird es abgeschnitten und die Prüfung wiederholt, bis kein bekanntes Präfix mehr gefunden wird. Dadurch bleiben reguläre Bestandteile des Titels, die Doppelpunkte enthalten, geschützt.

## Die Stationserkennung (Routing-Matrix)
Die Relevanz einer Strecke wird durch den Abgleich der erkannten Start- und Zielbahnhöfe ermittelt. Wir verwenden eine mehrstufige Logik, um zu entscheiden, welche Routen behalten und welche verworfen werden.

Die folgende Matrix veranschaulicht, wann eine Verbindung als "Wien-relevant" eingestuft wird:

| Endpunkt 1 (A) | Endpunkt 2 (B) | Ergebnis | Begründung / Bedingung |
| :--- | :--- | :--- | :--- |
| **Unbekannt** | **Unbekannt** | ❌ **Verworfen** | Komplett unbekannte Strecke, typischerweise reiner Fern-/Auslandsverkehr (z. B. Budapest ↔ Bratislava). |
| **Wien** | **Wien** | ✅ **Behalten** | Innerstädtische Verbindung. |
| **Wien** | **Pendlerbahnhof** | ✅ **Behalten** | Relevante Pendelstrecke. |
| **Wien** | **Unbekannt** | ❌ **Verworfen** | Ein unbekannter Endpunkt disqualifiziert die gesamte Strecke; behalten werden nur Strecken, deren bekannte Endpunkte allesamt Wien/Pendler sind und mindestens einen Wien-Bezug haben. |
| **Pendlerbahnhof** | **Pendlerbahnhof** | ❌ **Verworfen** | Strecke außerhalb Wiens ohne direkten Wien-Bezug. |
| **Pendlerbahnhof**| **Unbekannt** | ❌ **Verworfen** | Wenn mindestens eine Station bekannt ist, *muss* zwingend auch eine in Wien liegen (Asymmetrischer Pendler-Check). |

*Hinweis:* Wenn der strikte Modus über die Umgebungsvariable `OEBB_ONLY_VIENNA` aktiviert ist, werden Pendlerbahnhöfe ignoriert und **jeder** bekannte Endpunkt muss explizit in Wien liegen. Zwei zusätzliche Schärfungen gelten ausschließlich in diesem Modus:

* Das bloße Arealwort „Wien"/„Vienna" wird **nicht** mehr zu einer Flaggschiff-Station (z. B. „Wien Hauptbahnhof") kanonisiert. So sät eine generische „ab/bis Wien"-Meldung keine Phantom-Station mehr (Bug `b10`).
* Eine **allein** stehende Pendler-Erwähnung zählt nicht mehr als relevant: Ist die einzige erkannte Station ein Pendlerbahnhof, wird die Meldung verworfen, da ohne echten Wien-Bezug (Bug `b12`).

Im Standardmodus (Flag aus) bleibt beides unverändert.

## Warnung für zukünftige Entwickler (Tech-Debt)
Aktuell gibt es im Projekt zwei separate Stellen, an denen Schlüsselwörter für Kategorien und Liniencodes gepflegt werden — beide leben in `src/providers/oebb.py`:

1. Die Menge `NON_LOCATION_PREFIXES` (siehe Definition ab Zeile ~251) — wird von `_is_category` ausgewertet, um Titel-Tokens als Kategorien (vs. echte Ortsnamen) zu erkennen.
2. Der reguläre Ausdruck `base_pattern` innerhalb der Funktion `_strip_oebb_prefixes` (siehe Definition ab Zeile ~428) — entfernt führende Linien-/Störungspräfixe iterativ aus dem Titel.

**Achtung:** Wenn in Zukunft neue Kategorien, Störungsarten oder Liniencodes der ÖBB hinzugefügt werden müssen, muss sichergestellt werden, dass diese an **beiden** Stellen ergänzt werden. Eine Divergenz dieser Listen führt zu inkonsistentem Parsing und potenziellen Fehlern bei der Stationserkennung.

---

## Verwandte Dokumentation

* **S-Bahn-Stammstrecke-Monitor** — siehe
  [`stammstrecke_provider_logic.md`](stammstrecke_provider_logic.md).
  Das Stammstrecken-Verspätungs-Monitoring lief ursprünglich über
  `pyhafas` mit dem ÖBB-HAFAS-Endpunkt; seit der 2026-05-09 Migration
  läuft es über die VOR/VAO ReST API und seit der 2026-05-15 Migration
  über `/departureBoard` am Wien Hauptbahnhof. Es ist in einer eigenen
  Referenzdatei dokumentiert. Mit der 2026-05-09 Migration wurde auch
  das `source`-Feld der emittierten Events von `"ÖBB"` auf `"VOR/VAO"`
  umgestellt, weil die Datenquelle inzwischen technisch die VOR/VAO-API
  ist (`EVENT_SOURCE` in `src/feed/stammstrecke.py`).
