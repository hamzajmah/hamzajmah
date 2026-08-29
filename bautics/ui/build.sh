#!/usr/bin/env bash
# Bautics - Stylesheet bauen.
#
#   ./ui/build.sh            einmalig uebersetzen (minimiert)
#   ./ui/build.sh --watch    beim Entwickeln mitlaufen lassen
#
# Gebaut wird mit der Tailwind-Standalone-CLI: eine einzelne Binaerdatei,
# kein Node und kein npm noetig. Ergebnis ist app/static/css/bautics.css -
# diese Datei wird eingecheckt und vom eigenen Server ausgeliefert, damit im
# Produktivpfad kein fremder CDN aufgerufen wird.
set -euo pipefail

VERSION="v3.4.17"
PRUEFSUMME="7d24f7fa191d2193b78cd5f5a42a6093e14409521908529f42d80b11fde1f1d4"

UI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WURZEL="$(dirname "$UI")"
BIN="$UI/.bin/tailwindcss"   # nicht im Repo, siehe .gitignore

if [ ! -x "$BIN" ]; then
  echo "Tailwind-CLI $VERSION wird geholt ..."
  mkdir -p "$UI/.bin"
  URL="https://github.com/tailwindlabs/tailwindcss/releases/download/$VERSION/tailwindcss-linux-x64"
  curl -sSL --fail -o "$BIN.teil" "$URL"
  # Ohne Pruefsumme laedt der Build blind eine fremde Binaerdatei nach.
  echo "$PRUEFSUMME  $BIN.teil" | sha256sum -c - >/dev/null
  mv "$BIN.teil" "$BIN"
  chmod +x "$BIN"
fi

cd "$UI"
if [ "${1:-}" = "--watch" ]; then
  exec "$BIN" -c tailwind.config.js -i input.css -o "$WURZEL/app/static/css/bautics.css" --watch
fi
"$BIN" -c tailwind.config.js -i input.css -o "$WURZEL/app/static/css/bautics.css" --minify
echo "Fertig: app/static/css/bautics.css ($(wc -c <"$WURZEL/app/static/css/bautics.css") Bytes)"
