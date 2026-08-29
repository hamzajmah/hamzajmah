/* Bautics - Tailwind-Theme.
 *
 * Farben und Schriften stammen unveraendert aus DESIGN.md. Sie stehen nur
 * hier, damit im Markup keine Hex-Werte auftauchen und ein Wert an genau
 * einer Stelle geaendert wird.
 *
 * Gebaut wird mit der Tailwind-Standalone-CLI (siehe ui/build.sh) zu einer
 * statischen Datei - im Produktivpfad wird kein CDN aufgerufen.
 */
module.exports = {
  // Nur unsere Templates und die Anzeigeregeln in app/web - was hier nicht
  // vorkommt, landet nicht im CSS. Ohne app/web/*.py fehlen die
  // Zustandsklassen aus ansicht.py im fertigen Stylesheet.
  content: ["../app/templates/**/*.html", "../app/web/*.py"],
  theme: {
    extend: {
      colors: {
        // Grundflaechen und Text
        paper: "#F4F1EB",
        card: "#FFFFFF",
        ink: { DEFAULT: "#1B1914", muted: "#6C675C" },
        hair: { DEFAULT: "#E2DCCF", strong: "#CFC8B8" },
        // Marke - sparsam einsetzen, die Wirkung lebt von Seltenheit
        accent: { DEFAULT: "#D14E0A", soft: "#F8E4D6" },
        // Dunkle Flaechen: Sidebar, Kontrastbaender
        night: {
          DEFAULT: "#171511",
          raised: "#221F19",
          ink: "#F1EDE4",
          muted: "#9C9585",
          hair: "#35312A",
        },
        // Zustandsfarben - reserviert, nie dekorativ
        ok: { DEFAULT: "#2E7048", soft: "#DDEBE2" },
        warn: { DEFAULT: "#9A6B00", soft: "#F6EBD2" },
        crit: { DEFAULT: "#B3261E", soft: "#F6E0DE" },
      },
      fontFamily: {
        // Ersatzfamilien sind Pflicht (DESIGN.md), falls eine Schrift fehlt.
        serif: ['"Fraunces"', "Georgia", "serif"],
        sans: ['"IBM Plex Sans"', "system-ui", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "monospace"],
      },
      maxWidth: { lesbar: "68ch" },
    },
  },
  plugins: [],
};
