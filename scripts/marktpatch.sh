#!/usr/bin/env bash
# marktpatch.sh — move in eine Checkout-Kopie der AImighty Market Source
# einsetzen und daraus einen anwendbaren Patch machen.
#
# Aufruf:
#   ./scripts/paket.sh                     # erst packen
#   ./scripts/marktpatch.sh [<pfad>]       # dann einsetzen
#
# Ohne <pfad> wird die Market Source nach dist/markt frisch geklont. Sie ist
# oeffentlich lesbar; geschrieben wird ausschliesslich in die lokale Kopie.
# Dieses Skript pusht nichts und oeffnet keinen Pull Request.
#
# Zwei Dateien werden geaendert, mehr braucht es nicht -- der Endpunkt
# functions/api/v1/applications/[app]/chart.ts liest allein aus dem
# CHARTS-Dict, nicht aus dem charts/-Ordner:
#
#   functions/_apps.ts   der App-Eintrag, zwischen zwei Markerzeilen
#   functions/_lib.ts    eine Zeile im CHARTS-Dict, Schluessel = Dateiname
#
# ZWEIMAL LAUFEN IST SICHER. Der Eintrag steht zwischen Markern und wird
# ersetzt, nicht angehaengt -- zwei Eintraege mit demselben Namen waeren zwei
# Kacheln im Katalog. Beim CHARTS-Dict bleiben aeltere Versionen stehen; das
# ist Absicht und im Markt ueblich, eine Bestandsinstallation kann ihr Chart
# noch anfordern.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP="move"
DIST="dist"
# Marcs Repo ist die Quelle der Wahrheit. Geschrieben wird aber in den FORK:
# Kai hat auf bayerhazard/aimighty-market nur Leserechte, der Weg ist Fork,
# Branch, Pull Request (so steht es in beacons docs/BETRIEB.md, und insilos
# Einreichung dort ist PR #1).
HERKUNFT="https://github.com/bayerhazard/aimighty-market"
FORK="https://github.com/ska1walker/aimighty-market"

red()    { printf "\033[31m%s\033[0m\n" "$*"; }
green()  { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }

VERSION="$(grep -E '^version:' "$APP/Chart.yaml" | head -1 | awk '{print $2}')"

for f in "$DIST/_apps-eintrag.ts" "$DIST/_lib-eintrag.txt" "$DIST/$APP-$VERSION.tgz"; do
  if [[ ! -f "$f" ]]; then
    red "$f fehlt. Erst ./scripts/paket.sh laufen lassen."
    exit 1
  fi
done

MARKT="${1:-$DIST/markt}"
if [[ ! -d "$MARKT/.git" ]]; then
  echo "-- Market Source klonen --"
  git clone --depth 1 "$HERKUNFT" "$MARKT"
else
  echo "-- vorhandene Kopie benutzen: $MARKT --"
fi

# Nie in ein fremdes Repo schreiben, das man fuer ein anderes haelt. Erlaubt
# sind beide: die Quelle (zum Lesen) und der Fork (dorthin wird gepusht).
URL="$(git -C "$MARKT" remote get-url origin)"
if [[ "${URL%.git}" != "$HERKUNFT" && "${URL%.git}" != "$FORK" ]]; then
  red "$MARKT zeigt auf $URL, erwartet war $HERKUNFT oder $FORK"
  exit 1
fi
green "  + $(git -C "$MARKT" rev-parse --short HEAD) auf $URL"

# Niemals in einen Checkout mit offenen Aenderungen schreiben. Wenn das hier
# jemandes Arbeitskopie ist, gehoert sein Stand ihm.
if ! git -C "$MARKT" diff --quiet || ! git -C "$MARKT" diff --cached --quiet; then
  red "$MARKT hat offene Aenderungen. Erst aufraeumen, dann hier noch einmal."
  git -C "$MARKT" status --short | sed 's/^/      /'
  exit 1
fi

BRANCH="$APP-$VERSION"
git -C "$MARKT" checkout -q -B "$BRANCH"

# Die Grundlinie VOR den Aenderungen messen. Der frueheren Fassung lag ein
# `git stash` zugrunde; beim zweiten Lauf gibt es nichts zu stashen, und das
# `stash pop` beendete das Skript mit "No stash entries found". Vorher messen
# braucht kein stash.
VORHER=""
if [[ -d "$MARKT/node_modules/typescript" ]]; then
  VORHER="$(cd "$MARKT" && ./node_modules/.bin/tsc --noEmit 2>&1 | grep -c "error TS" || true)"
fi

echo
echo "-- eintragen --"
python3 - "$APP" "$VERSION" "$ROOT/$DIST" "$MARKT" <<'PY'
import pathlib
import sys

app, version, dist, markt = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3]), pathlib.Path(sys.argv[4])

block = (dist / "_apps-eintrag.ts").read_text().rstrip("\n")
anfang = block.splitlines()[0]
ende = block.splitlines()[-1]

apps_datei = markt / "functions" / "_apps.ts"
s = apps_datei.read_text()

if anfang in s:
    # Ersetzen, nicht anhaengen: zwei Eintraege mit demselben Namen waeren
    # zwei Kacheln, und getChartByAppName nimmt den ersten Treffer.
    i = s.index(anfang)
    j = s.index(ende, i) + len(ende)
    s = s[:i] + block + s[j:]
    print("  + _apps.ts: vorhandenen move-Block ERSETZT")
else:
    # Vor die schliessende Klammer des apps-Arrays. rindex, weil weiter oben
    # im interface ebenfalls ein "];" stehen kann.
    marke = "\n];"
    i = s.rindex(marke)
    s = s[:i] + "\n" + block + s[i:]
    print("  + _apps.ts: move-Block eingefuegt")

apps_datei.write_text(s)

zeile = (dist / "_lib-eintrag.txt").read_text()
schluessel = f'"{app}-{version}.tgz"'
lib_datei = markt / "functions" / "_lib.ts"
t = lib_datei.read_text()

kopf = "const CHARTS: Record<string, string> = {\n"
i = t.index(kopf) + len(kopf)

if schluessel in t:
    # Gleicher Schluessel, moeglicherweise anderes base64 -- die Zeile
    # austauschen. Ein altes base64 stehen zu lassen waere der Fehler, den
    # AGENTS.md ausdruecklich nennt.
    start = t.index(schluessel)
    zeilenanfang = t.rindex("\n", 0, start) + 1
    zeilenende = t.index("\n", start) + 1
    if t[zeilenanfang:zeilenende] == zeile:
        print("  + _lib.ts: Chart stand schon drin, unveraendert")
    else:
        t = t[:zeilenanfang] + zeile + t[zeilenende:]
        print("  + _lib.ts: base64 fuer diesen Schluessel ERSETZT")
else:
    t = t[:i] + zeile + t[i:]
    print(f"  + _lib.ts: {schluessel} eingefuegt")

lib_datei.write_text(t)
PY

echo
echo "-- typecheck, wenn die Abhaengigkeiten da sind --"
if [[ -n "$VORHER" ]]; then
  # Gegen die Grundlinie messen, nicht gegen null: das Repo hat schon vor
  # diesem Eintrag Fehler, und die sind nicht unsere.
  NACHHER="$(cd "$MARKT" && ./node_modules/.bin/tsc --noEmit 2>&1 | grep -c "error TS" || true)"
  if [[ "$NACHHER" -le "$VORHER" ]]; then
    green "  + tsc: $VORHER Fehler vorher, $NACHHER nachher -- der move-Eintrag bringt keinen neuen"
  else
    red "  x tsc: $VORHER Fehler vorher, $NACHHER nachher -- der move-Eintrag bringt $((NACHHER - VORHER)) neue"
    (cd "$MARKT" && ./node_modules/.bin/tsc --noEmit 2>&1 | grep "_apps.ts\|_lib.ts" | head -10 | sed 's/^/      /')
    exit 1
  fi
else
  yellow "  - node_modules fehlt in $MARKT (npm ci dort) -- typecheck uebersprungen"
fi

echo
echo "-- festschreiben --"
# Leer, solange kein Patch entstanden ist. Ohne diese Zeile beendete `set -u`
# das Skript am Schlusstext, sobald ein zweiter Lauf nichts zu aendern fand.
PATCH=""
git -C "$MARKT" add functions/_apps.ts functions/_lib.ts
if git -C "$MARKT" diff --cached --quiet; then
  yellow "  - keine Aenderung: der Eintrag steht schon auf diesem Branch"
  PATCH="$(ls "$DIST"/0001-*.patch 2>/dev/null | head -1 || true)"
else
  git -C "$MARKT" -c user.name="move" -c user.email="noreply@kaivo.studio" \
    commit -q -m "$APP $VERSION in den Katalog

Neue App: $APP, Kategorie Utilities, ein Entrance auf Port 3000.

Schnitt-Templates als Zeitstempel. Ein Template ist kein Modell, sondern eine
Liste von Zeitpunkten; angewendet wird deterministisch mit ffmpeg. Die
Pipeline laeuft ohne einen einzigen Modellaufruf durch.

Zwei Dateien, mehr braucht es nicht:
  functions/_apps.ts   App-Eintrag, zwischen zwei Markerzeilen
  functions/_lib.ts    eine Zeile im CHARTS-Dict, Schluessel $APP-$VERSION.tgz

Der Eintrag ist aus dem OlaresManifest.yaml der App erzeugt, nicht
abgeschrieben (scripts/markteintrag.py dort). Die Markerzeilen sagen das, weil
_apps.ts in Zeile 1 'Generated by gen_market.py' traegt -- der Block gehoert
in diesen Generator, nicht in dauerhafte Handpflege.

Drei envs werden bei der Installation abgefragt, alle optional: FAL_KEY
(password), MOVE_FAL_MODEL, MOVE_FAL_MAX_CLIPS (int, Vorgabe 12). Ohne
Schluessel laeuft die App mit Platzhaltern und eigenen Uploads."
  green "  + $(git -C "$MARKT" rev-parse --short HEAD)"

  git -C "$MARKT" format-patch -1 -o "$ROOT/$DIST" >/dev/null
  PATCH="$(ls "$DIST"/0001-*.patch | head -1)"
  green "  + $PATCH"
fi

echo
green "fertig."
cat <<TEXT

Die Kopie unter $MARKT traegt den Eintrag auf dem Branch $BRANCH.
Dieses Skript hat NICHT gepusht.

Der Weg ist Fork, Branch, Pull Request -- auf $HERKUNFT
gibt es nur Leserechte. Gepusht wird also in den Fork, und der Pull Request
geht ueber die Fork-Grenze:

  git -C $MARKT push -u origin $BRANCH        # origin = der Fork
  gh pr create --repo bayerhazard/aimighty-market \\
    --base main --head ska1walker:$BRANCH \\
    --title "$APP $VERSION in den Katalog"

Zeigt origin hier auf $HERKUNFT, vorher umstellen:

  git -C $MARKT remote set-url origin $FORK

Oder ohne diese Kopie, direkt im eigenen Checkout:

  git am ${PATCH:-<kein Patch: nichts hat sich geaendert>}
  (git apply nimmt nur die Aenderung, git am bringt die Commit-Nachricht mit)

Danach: committen UND deployen (wrangler pages deploy functions/), dann den
Sync abwarten. Ein Commit ohne Deploy aendert am Katalog nichts.
TEXT
