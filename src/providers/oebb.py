from typing import List, Dict, Any
import os

def fetch_events() -> List[Dict[str, Any]]:
    """
    Offizielle ÖBB-Echtzeit-/Störungsdaten erfordern Zugang (z.B. InfoHub/SFIT).
    Dieser Provider ist bewusst „still“, bis ENV-Variablen gesetzt und die
    Implementierung hinterlegt ist. So bleibt der Feed valid und erweiterbar.
    """
    # Beispiel für spätere Aktivierung:
    # base = os.getenv("OEBB_API_BASE_URL")
    # token = os.getenv("OEBB_API_TOKEN")
    # if not (base and token):
    #     return []
    #
    # 1) Relevante Stationen/Linien für Großraum Wien whitelisten
    # 2) Offizielle Endpunkte abfragen
    # 3) Auf aktives Zeitfenster filtern
    # 4) Events ins einheitliche Format mappen (wie WL)
    return []
