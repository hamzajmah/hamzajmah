"""Die Produkt-Oberflaeche (serverseitig gerenderte Seiten).

Bewusst kein SPA: zwei Bildschirme (Echo, Mind) rechtfertigen kein
Frontend-Framework. Die Seiten rufen dieselbe Fachlogik auf wie die
JSON-Schnittstelle - sie sprechen nicht ueber HTTP mit dem eigenen Server.
"""

from .routen import STATISCH_VERZEICHNIS, router

__all__ = ["STATISCH_VERZEICHNIS", "router"]
