"""Gemini decides the disruptions the rules leave unclear: sudden or planned.

Owner's decision (Michael, 2026-11-12, card "Soll ich die Gemini-Entscheidung
für unklare Fälle so bauen?": "Ja, so bauen"; his order of the same day:
"Wenn bei einer Meldung nicht klar ist, ob sie eine Störung oder ein
Hinweis ist, soll Gemini dies über ein neues Modul entscheiden können.
Plötzliche Störungen haben Priorität. Geplante Hinweise kommen danach."):
his second explicit exception to "an AI only watches and reports, never
rewrites" (2026-10-31), after the yearly Baustellen review (2026-11-10).

What the exception covers, and what it does not:

* Only the disruptions of three measured classes are asked about
  (:mod:`.candidates`), about three a week; every other message is decided
  by the rules alone, as are the owner's own decisions (a moved stop,
  works, a demonstration, an event are notices).
* The model chooses, it writes nothing: its answer is one of
  ``ploetzlich``, ``geplant``, ``unklar`` (:mod:`.ask`). The feed's words
  stay the sources' and the rules'. The answer sets ``_ki_art`` on the
  item, and ``feed_build.time_line._incident_since`` reads it where it
  would otherwise read the clock or a works word: a sudden disruption
  shows "[Seit hh:mm]" and stands in the first tier for its first day, a
  planned one stands in the normal order.
* The answers are stored per message (:mod:`.store`) and hold for the
  event they were given for: the next event under the same WL key is asked
  about anew. Without an answer (no key, a busy model, a reached limit,
  ``unklar``) the rules decide as before. The build sends no new request
  later than fifteen seconds after the first and waits about twenty
  seconds for the models, thirty at the latest when a connection is slow
  to open (:mod:`.ask`, :mod:`.run`).
"""

from .run import Decider, apply_stored

__all__ = ["Decider", "apply_stored"]
