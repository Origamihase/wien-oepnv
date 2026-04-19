# ÖBB Provider Logic (`src/providers/oebb.py`)

## Zweck des Providers
Der ÖBB Provider hat die Aufgabe, Störungsmeldungen des österreichischen Zugverkehrs (ÖBB) abzurufen und sie einer strengen Filterung zu unterziehen. Ziel ist es, ausschließlich Meldungen in den Feed aufzunehmen, die einen **strikten Wien-Bezug** aufweisen oder für Pendler in und um Wien relevant sind. Irrelevante Meldungen aus dem reinen Fern- oder Auslandsverkehr werden frühzeitig verworfen.

## Die Herausforderung der ÖBB-Rohdaten
Die von der ÖBB bereitgestellten RSS-Feeds weisen oft komplexe, mutierte Titel auf, die sowohl Kategorien, Liniencodes als auch Streckenabschnitte vermischen (z.B. `"REX 51: Störung: Wien Hbf ↔ Wr. Neustadt"`).

Ein simples Splitten (z.B. beim ersten oder letzten Doppelpunkt) ist unzureichend und fehleranfällig, da echte Stationsnamen selbst Doppelpunkte enthalten können (wie etwa `"Wien 10.: Favoriten"`). Ein naiver Split würde diese gültigen Stationsnamen zerstören.

Daher erfolgt das Parsing der Präfixe **iterativ**. In einer `while`-Schleife wird mittels regulärer Ausdrücke jeweils von vorne geprüft, ob der Anfang des Textes ein bekanntes Präfix (wie `"Störung:"` oder `"REX 51:"`) ist. Ist das der Fall, wird es abgeschnitten und die Prüfung wiederholt, bis kein bekanntes Präfix mehr gefunden wird. Dadurch bleiben reguläre Bestandteile des Titels, die Doppelpunkte enthalten, geschützt.

## Die Stationserkennung (Routing-Matrix)
Die Relevanz einer Strecke wird durch den Abgleich der erkannten Start- und Zielbahnhöfe ermittelt. Wir verwenden eine mehrstufige Logik, um zu entscheiden, welche Routen behalten und welche verworfen werden.

Die folgende Matrix veranschaulicht, wann eine Verbindung als "Wien-relevant" eingestuft wird:

| Endpunkt 1 (A) | Endpunkt 2 (B) | Ergebnis | Begründung / Bedingung |
| :--- | :--- | :--- | :--- |
| **Unbekannt** | **Unbekannt** | ❌ **Verworfen** | Komplett unbekannte Strecke, typischerweise reiner Fern-/Auslandsverkehr (z.B. Budapest ↔ Bratislava). |
| **Wien** | **Wien** | ✅ **Behalten** | Innerstädtische Verbindung. |
| **Wien** | **Pendlerbahnhof** | ✅ **Behalten** | Relevante Pendelstrecke. |
| **Wien** | **Unbekannt** | ✅ **Behalten** | Mindestens ein Endpunkt ist bekannt und liegt in Wien. |
| **Pendlerbahnhof** | **Pendlerbahnhof** | ❌ **Verworfen** | Strecke außerhalb Wiens ohne direkten Wien-Bezug. |
| **Pendlerbahnhof**| **Unbekannt** | ❌ **Verworfen** | Wenn mindestens eine Station bekannt ist, *muss* zwingend auch eine in Wien liegen (Asymmetrischer Pendler-Check). |

*Hinweis:* Wenn der strikte Modus über die Umgebungsvariable `OEBB_ONLY_VIENNA` aktiviert ist, werden Pendlerbahnhöfe ignoriert und **jeder** bekannte Endpunkt muss explizit in Wien liegen.

## Warnung für zukünftige Entwickler (Tech-Debt)
Aktuell gibt es im Projekt zwei separate Stellen, an denen Schlüsselwörter für Kategorien und Liniencodes gepflegt werden:

1. Die Menge `NON_LOCATION_PREFIXES` in der Datei `src/utils/stations.py`.
2. Der reguläre Ausdruck `base_pattern` innerhalb der Funktion `_strip_oebb_prefixes` in der Datei `src/providers/oebb.py`.

**Achtung:** Wenn in Zukunft neue Kategorien, Störungsarten oder Liniencodes der ÖBB hinzugefügt werden müssen, muss sichergestellt werden, dass diese an **beiden** Stellen ergänzt werden. Eine Divergenz dieser Listen führt zu inkonsistentem Parsing und potenziellen Fehlern bei der Stationserkennung.
