# Audit: Force-Push-Race löscht gemergten PR aus `main`

**Datum:** 2026-09-12
**Schwerpunkt:** CI/CD-Integrität (`.github/workflows/*`), nicht Feed-Inhalt
**Schweregrad:** hoch — stiller, unbemerkter Verlust gemergter Arbeit
**Status:** Ursache belegt, Fix in diesem PR

---

## 1. Executive Summary

Der Merge-Commit von [PR #1783](https://github.com/Origamihase/wien-oepnv/pull/1783)
(`a62fa59`, fügte `docs/archive/audits/audit-2026-09-11.md` hinzu) ist **zwei
Sekunden nach dem Merge** aus `main` verschwunden. Kein Mensch hat ihn entfernt:
Der `SEO Verify`-Workflow hat ihn mit einem Force-Push überschrieben.

Das `--force-with-lease`, das genau das verhindern sollte, ist an dieser Stelle
**wirkungslos** — die verwendete `git-auto-commit-action` führt unmittelbar vor
dem Push einen eigenen `git fetch` aus und schärft damit das Lease auf genau den
Commit nach, den es schützen soll.

Der Verlust blieb unbemerkt: Alle Workflows waren grün, der Feed war korrekt, und
GitHub zeigt den PR weiterhin als „merged" an. Aufgefallen ist es nur, weil beim
Nacharbeiten der 09-2026-Audits eine referenzierte Datei fehlte.

---

## 2. Nachweis (Job-Log, Run 34644037452, Job 103410399275)

Schritt `Commit sitemap changes`, gekürzt auf die vier entscheidenden Zeilen:

```text
20:24:56.948   + f576d7cc6...a62fa59c1 main -> origin/main  (forced update)
20:24:57.082   Your branch and 'origin/main' have diverged,
20:24:57.082   and have 28039 and 1 different commits each, respectively.
20:24:57.096   INPUT_PUSH_OPTIONS: --force-with-lease
20:24:58.772   + a62fa59c1...82c421f01 main -> main (forced update)
```

Zeile 1 ist der actioneigene Fetch: `refs/remotes/origin/main` wandert auf
`a62fa59` (den frisch gemergten PR). Zeile 2–3 ist die Warnung der Action, dass
der lokale Branch **divergiert** ist — sie pusht trotzdem. Zeile 5 ist der
Verlust: `a62fa59` wird durch `82c421f` ersetzt, dessen Parent noch der Stand
*vor* dem Merge ist.

## 3. Zeitleiste

| UTC | Ereignis |
| --- | --- |
| 20:24:19 | PR #1782 gemergt (`f576d7c`) → triggert `SEO Verify` Run 1015 |
| 20:24:27 | Run 1015 checkt `f576d7c` aus |
| 20:24:53 | Sitemap/llms.txt regeneriert |
| 20:24:55 | Schritt „Pull concurrent remote changes" (`git pull --rebase`) — sieht noch `f576d7c` |
| **20:24:55** | **PR #1783 wird gemergt → `main` = `a62fa59`** |
| 20:24:56 | Action fetcht → Lease zeigt jetzt auf `a62fa59` |
| 20:24:57 | Action committet `82c421f` auf Parent `f576d7c` |
| 20:24:58 | Force-Push: `a62fa59` → `82c421f`, **PR #1783 ist aus `main` verschwunden** |
| 20:25:35 | Run 1016 (vom #1783-Merge getriggert) rebased auf die bereits beschädigte Historie und pusht erneut force |
| 20:26:08 | Revert-PR #1784 wird geöffnet und 36 s später ungemergt geschlossen |

## 4. Root Cause

`--force-with-lease` ohne expliziten Wert prüft gegen den **Remote-Tracking-Ref**
(`refs/remotes/origin/main`). Die Schutzwirkung setzt voraus, dass dieser Ref den
Stand widerspiegelt, den der Job kennt und auf dem er aufbaut. Ein `git fetch`
unmittelbar vor dem Push zerstört diese Voraussetzung: Das Lease beschreibt
danach nicht mehr „was ich kenne", sondern „was gerade im Remote steht" — und
damit ist jede Prüfung tautologisch erfüllt.

`stefanzweifel/git-auto-commit-action` fetcht in ihrem Push-Pfad. In Kombination
mit `push_options: --force-with-lease` degradiert der Push damit faktisch zu
`--force`, inklusive der ausdrücklichen Divergenz-Warnung im Log, die die Action
ignoriert.

Der vorgeschaltete Schritt „Pull concurrent remote changes" schließt die Lücke
nicht: Er läuft *vor* dem Commit und kann eine Sekunde später gelandete Commits
nicht kennen.

## 5. Reichweite

Drei Workflows waren betroffen — alle drei mit demselben Muster
(`git-auto-commit-action` + `push_options: --force-with-lease`):

| Workflow | Trigger | Kollisionsrisiko |
| --- | --- | --- |
| `seo-guard.yml` | jeder Push auf `docs/**` + Cron | hoch (läuft nach *jedem* Merge) |
| `update-stations.yml` | wöchentlicher Cron | niedrig |
| `manual-full-refresh.yml` | manuell | niedrig |

`update-cycle.yml` ist **nicht** betroffen, obwohl es ebenfalls
`--force-with-lease` verwendet: Sein Push läuft in einer eigenen Retry-Schleife,
die vor jedem erneuten Versuch `git pull --rebase` ausführt. Nach einem
erfolgreichen Rebase enthält der lokale Branch die Remote-Commits, ein Force
verliert dort also nichts; der erste Versuch trägt ein Lease aus dem
Checkout-Fetch am Job-Anfang und wird bei einem Rennen korrekt abgelehnt.

## 6. Fix

`push_options: '--force-with-lease'` wurde in den drei betroffenen Workflows
ersatzlos entfernt. Der Push ist damit ein gewöhnlicher Push:

* **Normalfall:** Nach dem Rebase im vorgelagerten Schritt ist er ein
  Fast-Forward — Verhalten unverändert.
* **Rennen:** Der Push wird abgelehnt. Der Run scheitert sichtbar, der Remote
  bleibt **unangetastet**, und der nächste Trigger erzeugt dieselbe Ausgabe neu
  (`seo-guard` läuft bei jedem `docs/**`-Push, also spätestens beim nächsten
  Update-Cycle-Tick).

Bewusste Abwägung: Ein seltener roter Run (grob geschätzt ~1× pro Monat, bei
einem Kollisionsfenster von ~2 s) ist einem stillen Historienverlust vorzuziehen.
Wer auch den roten Run vermeiden will, kann später die Retry-Schleife aus
`update-cycle.yml` übernehmen — der Fix hier ist bewusst minimal gehalten,
weil Workflow-Änderungen nicht lokal testbar sind.

## 7. Wiederherstellung

`docs/archive/audits/audit-2026-09-11.md` wurde aus dem verwaisten Commit
`a62fa59` wiederhergestellt (`git show a62fa59:<pfad>`). Der Commit ist weiterhin
über die GitHub-API erreichbar, solange er nicht der Garbage Collection zum Opfer
fällt — bei einem späteren Fund wäre die Datei möglicherweise unrettbar gewesen.

## 8. Empfehlungen über diesen PR hinaus

1. **Branch-Protection für `main`:** „Allow force pushes" deaktivieren. Das hätte
   den Verlust unabhängig von jeder Workflow-Konfiguration verhindert und ist die
   einzige Maßnahme, die auch künftige Automatisierungen abdeckt.
2. **Kein `--force-with-lease` in Kombination mit Tools, die selbst fetchen.**
   Wo ein Force unvermeidlich ist, gehört das Lease explizit gepinnt
   (`--force-with-lease=<ref>:<sha>`) statt implizit über den Tracking-Ref.
3. **Merge-Commits stichprobenartig prüfen:** Ein wöchentlicher Job, der für die
   zuletzt gemergten PRs `git merge-base --is-ancestor <merge-sha> origin/main`
   verifiziert, hätte den Verlust am selben Tag gemeldet.

## 9. Prüfung auf weitere Verluste

Die Merge-Commits der PRs #1780 bis #1785 wurden gegen `origin/main` geprüft.
`b9633d1` (#1781) und `5e72c73` (#1780) sind ebenfalls keine Vorfahren von `main`
— ihre **Inhalte** sind jedoch vorhanden (eingesammelt von einem späteren
`chore: update cycle`-Commit), hier wurde also nur die Historie umgeschrieben,
nichts verloren. Einzig #1783 hat Inhalt verloren.
