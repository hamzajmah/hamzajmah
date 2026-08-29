"""Bau-Glossar fuer Trassen- und Leitungsbau.

Wird der Spracherkennung als Kontext mitgegeben, damit Fachbegriffe und
Stationierungen korrekt ankommen ("zwoelf-vier" -> 12+400, nicht 12:40 Uhr).
"""

GLOSSAR_BEGRIFFE = [
    "Stationierung",
    "Rohrgraben",
    "Kabelzug",
    "Kabelschutzrohr",
    "Wasserhaltung",
    "Grundwasserabsenkung",
    "Bodenaustausch",
    "Planum",
    "HDD-Bohrung",
    "Spuelbohrung",
    "Duekerung",
    "Bewehrung",
    "Betonage",
    "Kettenbagger",
    "Raupe",
    "Verbau",
    "Aufmass",
    "Nachtrag",
    "Behinderungsanzeige",
    "Baulos",
    "Leistungsverzeichnis",
    "Muffenbauwerk",
    "Cross-Bonding",
    "Oberbodenabtrag",
    "Rekultivierung",
]

# Kontext-Hinweis fuer die Spracherkennung (Whisper/Deepgram "prompt"/"keywords")
STT_KONTEXT = (
    "Baustellenbericht Trassenbau, deutsche Fachsprache. "
    "Stationierungen wie 12+400 oder 12+700. Begriffe: "
    + ", ".join(GLOSSAR_BEGRIFFE)
    + "."
)
