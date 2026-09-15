#!/usr/bin/env bash
# paket.sh — das Chart so packen, wie eine Market Source es braucht
#
# Eine Olares-App kommt nicht dadurch in den Markt, dass sie hier im Repo
# liegt. Eine Market Source ist ein eigener Webdienst: sie listet die App
# unter /api/v1/appstore/info und liefert das Chart unter
# /api/v1/applications/<app>/chart?fileName=<app>-<version>.tgz. Das Chart
# steckt dort als base64 in einer Tabelle. Dieses Skript erzeugt genau die
# beiden Stuecke, die dort eingetragen werden -- nichts weiter.
#
# Erzeugt in dist/:
#   move-<version>.tgz          das gepackte Chart
#   move-<version>.tgz.base64   dasselbe als EINE Zeile, ohne Umbrueche
#   markteintrag.json           die Metadatenfelder, aus dem Manifest gelesen
#
# Drei Regeln, die hier eingebaut sind, weil jede fuer eine echte Ablehnung
# steht:
#
#   1. Nie ein Chart packen, das die Guards nicht besteht. check-chart.sh
#      laeuft zuerst; scheitert es, entsteht kein Paket.
#   2. Den GEPACKTEN Tarball linten, nicht den Ordner.
#   3. Nur EINMAL gzippen. `helm package` liefert schon .tgz -- das base64
#      entsteht direkt daraus, nie aus einem zweiten Komprimieren. Ein
#      doppelt gezipptes Chart meldet die Box als "invalid tar header".
#
# Der Dateiname ist keine Formalie: er muss dem Tabellenschluessel in der
# Market Source und dem Feld chartName entsprechen, sonst laedt die Box das
# Chart nicht (404) und zeigt weiter die alte Version.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP="move"
DIST="dist"

red()   { printf "\033[31m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }

if ! command -v helm >/dev/null 2>&1; then
  red "helm fehlt. Ohne helm kein Paket -- das Packformat wird nicht nachgebaut."
  exit 1
fi

# check-chart.sh vermeidet PyYAML bewusst, damit die Guards ueberall laufen.
# Hier ist es anders: der Markteintrag liest das Manifest strukturiert, und
# ein grep-Leser wuerde bei den mehrzeiligen Beschreibungen falsch raten.
if ! python3 -c "import yaml" 2>/dev/null; then
  red "PyYAML fehlt: python3 -m pip install pyyaml"
  exit 1
fi

VERSION="$(grep -E '^version:' "$APP/Chart.yaml" | head -1 | awk '{print $2}')"
if [[ -z "$VERSION" ]]; then
  red "keine version in $APP/Chart.yaml gefunden"
  exit 1
fi

TGZ="$DIST/$APP-$VERSION.tgz"

echo "-- Guards vor dem Packen --"
./scripts/check-chart.sh >/dev/null
green "  + check-chart.sh bestanden"

rm -rf "$DIST"
mkdir -p "$DIST"

echo
echo "-- packen --"
helm package "$APP/" -d "$DIST" >/dev/null
if [[ ! -f "$TGZ" ]]; then
  red "erwartet wurde $TGZ, entstanden ist: $(ls "$DIST")"
  red "Chart.yaml.name und .version muessen den Dateinamen ergeben."
  exit 1
fi
green "  + $TGZ ($(stat -c%s "$TGZ") Byte)"

echo
echo "-- Inhalt pruefen --"
# Was eine Market Source im Tarball erwartet. Fehlt eines davon, laedt die
# Box das Chart, lehnt es aber ab -- und der Grund steht nur im Log von
# app-service.
INHALT="$(tar -tzf "$TGZ")"
fehlt=0
for pfad in \
  "$APP/Chart.yaml" \
  "$APP/values.yaml" \
  "$APP/OlaresManifest.yaml" \
  "$APP/templates/deployment-move.yaml" \
  "$APP/templates/deployment-moveworker.yaml" \
  "$APP/templates/service-move.yaml"; do
  if echo "$INHALT" | grep -qx "$pfad"; then
    green "  + $pfad"
  else
    red "  x $pfad fehlt im Tarball"
    fehlt=1
  fi
done

# Der Stub ist ein Werkzeug fuer helm lint ausserhalb der Box und hat im
# ausgelieferten Chart nichts zu suchen: Olares mischt seine eigenen Werte
# ein, und ein mitgeliefertes values-Stub koennte sie ueberdecken.
if echo "$INHALT" | grep -q "values-olares-stub.yaml"; then
  red "  x values-olares-stub.yaml ist mitgepackt -- gehoert in .helmignore"
  fehlt=1
else
  green "  + kein values-olares-stub.yaml im Paket"
fi

# Genau einmal gzippen: der Tarball muss sich in EINEM Schritt entpacken
# lassen. Ein zweites gzip macht daraus "invalid tar header" auf der Box.
if ! tar -tzf "$TGZ" >/dev/null 2>&1; then
  red "  x $TGZ ist kein einfach gezippter Tarball"
  fehlt=1
fi

[[ "$fehlt" -eq 0 ]] || exit 1

echo
echo "-- offizieller Validator auf dem GEPACKTEN Chart --"
if command -v olares-cli >/dev/null 2>&1; then
  olares-cli chart lint "$TGZ" --with-rbac --with-security-context
  green "  + olares-cli chart lint: OK"
else
  printf "\033[33m%s\033[0m\n" "  - olares-cli nicht im PATH. Das Paket ist ungeprueft durch den offiziellen Linter."
fi

echo
echo "-- base64, eine Zeile, direkt aus dem Tarball --"
base64 -w0 "$TGZ" > "$TGZ.base64"
# Gegenprobe: dekodiert muss byteweise dasselbe herauskommen. Ein Umbruch
# oder ein abgeschnittenes Ende faellt sonst erst auf der Box auf.
base64 -d "$TGZ.base64" > "$DIST/probe.tgz"
cmp "$TGZ" "$DIST/probe.tgz"
rm "$DIST/probe.tgz"
green "  + $TGZ.base64 ($(stat -c%s "$TGZ.base64") Zeichen), Rueckprobe byteweise gleich"

echo
echo "-- Metadaten aus dem Manifest --"
python3 - "$APP" "$VERSION" "$TGZ" <<'PY'
import hashlib, json, sys
import yaml

app, version, tgz = sys.argv[1], sys.argv[2], sys.argv[3]
m = yaml.safe_load(open("OlaresManifest.yaml"))
meta, spec = m["metadata"], m["spec"]

# Aus dem Manifest gelesen, nicht abgeschrieben: was hier steht, kann nicht
# gegen das ausgelieferte Chart driften.
eintrag = {
    "chartName": f"{app}-{version}.tgz",
    "metadata": {
        "name": meta["name"],
        "appid": meta["appid"],
        "version": str(meta["version"]),
        "icon": meta["icon"],
        "title": meta["title"],
        "description": meta["description"],
        "categories": meta["categories"],
        "fullDescription": spec["fullDescription"],
        "upgradeDescription": spec["upgradeDescription"],
        "developer": spec["developer"],
        "website": spec["website"],
        "sourceCode": spec["sourceCode"],
        "versionName": str(spec["versionName"]),
        "resources": {
            k: spec[k]
            for k in (
                "requiredCpu", "limitedCpu",
                "requiredMemory", "limitedMemory",
                "requiredDisk",
            )
        },
    },
    "spec": {"entrance": m["entrances"][0]},
    # Die Adresse der App leitet sich hieraus ab, nicht aus dem Namen.
    "appidHinweis": hashlib.md5(app.encode()).hexdigest()[:8],
}

json.dump(eintrag, open("dist/markteintrag.json", "w"), indent=2, ensure_ascii=False)
print(f"  + dist/markteintrag.json")
print(f"  + chartName        {eintrag['chartName']}")
print(f"  + Tabellenschluessel  \"{eintrag['chartName']}\"")
print(f"  + Adresse beginnt mit {eintrag['appidHinweis']}")
PY

echo
green "Paket fertig. Version $VERSION."
cat <<TEXT

Was damit zu tun ist, in der Market Source (eigenes Repo, nicht dieses):

  1. Den Inhalt von $TGZ.base64 als Wert unter dem
     Schluessel "$APP-$VERSION.tgz" in die Chart-Tabelle eintragen.
     Nie ein altes base64 wiederverwenden.
  2. Die Felder aus $DIST/markteintrag.json in den App-Eintrag uebernehmen.
  3. Market Source committen UND deployen, dann den Sync abwarten.

Danach, und erst danach, installieren und 'running' auf der Box MESSEN.
Eine App, die nie 'running' erreicht hat, gehoert in keinen Markt.
TEXT
