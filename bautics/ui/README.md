# Oberfläche: Stylesheet und Schriften bauen

Die Anwendung liefert **eine** fertige CSS-Datei aus:
`app/static/css/bautics.css`. Sie ist eingecheckt. Im Produktivpfad wird
**kein CDN** aufgerufen – weder für CSS noch für Schriften.

Warum: Auf der Baustelle ist das Netz schlecht, und bei deutschen
Konzernkunden ist ein Aufruf an einen fremden CDN ein unnötiger
Datenschutz-Diskussionspunkt.

## Stylesheet neu bauen

```bash
./ui/build.sh            # einmalig, minimiert
./ui/build.sh --watch    # beim Entwickeln mitlaufen lassen
```

Gebaut wird mit der **Tailwind-Standalone-CLI** – eine einzelne Binärdatei,
kein Node und kein npm nötig. `build.sh` lädt sie beim ersten Aufruf nach
`ui/.bin/tailwindcss` (Version und SHA-256 stehen im Skript, die Prüfsumme
wird kontrolliert) und ignoriert sie danach. `ui/.bin/` ist bewusst nicht
eingecheckt – 43 MB gehören nicht ins Repo.

**Nach jeder Änderung an einem Template oder an `app/web/ansicht.py` muss
`./ui/build.sh` laufen und das Ergebnis mit eingecheckt werden.** Tailwind
nimmt nur die Klassen ins CSS auf, die es in den Quellen findet
(`content` in `tailwind.config.js`).

### Woher die Werte kommen

`ui/tailwind.config.js` bildet `DESIGN.md` ab: `paper`, `ink`, `accent`,
`night`, `ok`, `warn`, `crit` und die drei Schriftfamilien. Im Markup stehen
deshalb nur Token-Namen (`bg-paper`, `text-ink-muted`, `border-l-crit`),
keine Hex-Werte. Wer eine Farbe ändern will, ändert sie dort – an einer
Stelle.

`ui/input.css` enthält zusätzlich ein paar wiederkehrende Bausteine
(`.karte`, `.knopf`, `.plakette`, `.etikett`, `.kennung`). `.kennung` setzt
`tabular-nums` und gehört an jede Stationierung, Positionsnummer und
Berichtsnummer (DESIGN.md).

## Schriften erneuern

Fraunces, IBM Plex Sans und IBM Plex Mono liegen als `.woff2` unter
`app/static/fonts/` (nur Latin und Latin-Ext, rund 560 kB insgesamt). Neu
holen – nur nötig, wenn sich Schnitte ändern sollen:

```bash
python ui/fonts_holen.py
```

Das Skript lädt die Dateien einmalig von Google, legt sie lokal ab und
schreibt `app/static/fonts/schriften.css` mit den `@font-face`-Regeln. Diese
Datei wird von `ui/input.css` eingebunden. Danach `./ui/build.sh` laufen
lassen.
