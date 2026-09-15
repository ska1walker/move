#!/usr/bin/env bash
# check-chart.sh — Vorab-Guards fuer move
#
# Uebernommen aus dem Insilo-Original und auf move umgebaut. Jede Pruefung
# stand dort fuer eine echte Ablehnung. Laeuft in CI vor dem Image-Build und
# ist lokal schnell genug fuer jeden Commit.
#
# Angepasst gegenueber dem Original:
#   - Chart-Ordner heisst wie die App (move/), nicht olares/
#   - SQL-Drift-Guard auf db/migrations/ statt supabase/
#   - neu: apiTimeout, runAsUser-Typ, openMethod, Netzwerk-Kinds,
#          fuehrende Null in der Version, OLARES_USER_-Textpruefung
#
# Exit-Codes:
#   0  — alle Pruefungen bestanden
#   1  — mindestens eine Pruefung ist gescheitert

set -euo pipefail

# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP="move"
CHART_DIR="$APP"
CHART_FILE="$CHART_DIR/Chart.yaml"
MANIFEST_FILE="$CHART_DIR/OlaresManifest.yaml"
ROOT_MANIFEST_FILE="OlaresManifest.yaml"
VALUES_FILE="$CHART_DIR/values.yaml"
TEMPLATES_DIR="$CHART_DIR/templates"
VALUES_STUB="$CHART_DIR/values-olares-stub.yaml"
MIGRATIONS_SRC="db/migrations"
MIGRATIONS_DST="$CHART_DIR/files"

red()    { printf "\033[31m%s\033[0m\n" "$*"; }
green()  { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }

FAILED=0
fail()    { red    "  x $*"; FAILED=$((FAILED + 1)); }
ok()      { green  "  + $*"; }
skip()    { yellow "  - $*"; }
section() { printf "\n%s\n" "-- $* --"; }

extract() {
  # extract <datei> <yaml-pfad-praefix>
  # Kleiner grep-Leser. Vermeidet eine yq-Abhaengigkeit in CI.
  grep -E "^[[:space:]]*${2}:" "$1" | sed -n 1p \
    | sed -E "s/.*${2}:[[:space:]]*['\"]?([^'\"#]+)['\"]?.*/\1/" | xargs
}

# ---------------------------------------------------------------------------
# 1. Versionsgleichlauf (Chart.yaml <-> OlaresManifest)
# ---------------------------------------------------------------------------

section "Versionsgleichlauf"

CHART_VERSION="$(extract "$CHART_FILE" "version")"
CHART_APP_VERSION="$(extract "$CHART_FILE" "appVersion")"
MANIFEST_VERSION="$(extract "$MANIFEST_FILE" "  version")"
MANIFEST_VERSIONNAME="$(grep -E "^[[:space:]]*versionName:" "$MANIFEST_FILE" | sed -n 1p \
  | sed -E "s/.*versionName:[[:space:]]*['\"]?([^'\"#]+)['\"]?.*/\1/" | xargs)"

if [[ "$CHART_VERSION" == "$CHART_APP_VERSION" ]]; then
  ok "Chart.yaml: version == appVersion ($CHART_VERSION)"
else
  fail "Chart.yaml: version ($CHART_VERSION) != appVersion ($CHART_APP_VERSION)"
fi

if [[ "$CHART_VERSION" == "$MANIFEST_VERSION" ]]; then
  ok "Chart.yaml.version == OlaresManifest.metadata.version ($CHART_VERSION)"
else
  fail "Chart.yaml.version ($CHART_VERSION) != OlaresManifest.metadata.version ($MANIFEST_VERSION)"
fi

if [[ "$CHART_APP_VERSION" == "$MANIFEST_VERSIONNAME" ]]; then
  ok "Chart.yaml.appVersion == OlaresManifest.spec.versionName ($CHART_APP_VERSION)"
else
  fail "Chart.yaml.appVersion ($CHART_APP_VERSION) != OlaresManifest.spec.versionName ($MANIFEST_VERSIONNAME)"
fi

# ---------------------------------------------------------------------------
# 1a. Keine fuehrende Null im Versionsfeld
#     olares-cli validiert mit strict-semver (Masterminds) und lehnt '26.09.1'
#     ab — und zwar auf beiden Seiten: als Ziel UND als installierte Version.
#     Aus so einer Version kommt man nur per Deinstallation heraus. move
#     startet deshalb bei 26.9.1 und darf nie zurueckfallen.
# ---------------------------------------------------------------------------

section "Version ist strikte SemVer ohne fuehrende Null"

if [[ "$CHART_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  IFS=. read -r _VMAJ VMIN VPAT <<< "$CHART_VERSION"
  if [[ "$VMIN" =~ ^0[0-9]+$ || "$VPAT" =~ ^0[0-9]+$ ]]; then
    fail "Version '$CHART_VERSION' hat eine fuehrende Null — market upgrade wird sie dauerhaft ablehnen"
  else
    ok "Version $CHART_VERSION"
  fi
else
  fail "Version '$CHART_VERSION' ist keine strikte SemVer"
fi

# ---------------------------------------------------------------------------
# 1b. Root-Manifest <-> Chart-Manifest
#     Der Markt verlangt ZWEI OlaresManifest.yaml (Store-Metadaten und
#     Installation) mit identischer Version. Die Kopie driftet, wenn ein
#     Release-Skript nur die chart-interne Datei hebt.
# ---------------------------------------------------------------------------

section "Root-Manifest <-> Chart-Manifest"

if [[ ! -f "$ROOT_MANIFEST_FILE" ]]; then
  fail "$ROOT_MANIFEST_FILE fehlt — der Store liest das Root-Manifest"
else
  ROOT_MANIFEST_VERSION="$(extract "$ROOT_MANIFEST_FILE" "  version")"
  ROOT_MANIFEST_VERSIONNAME="$(grep -E "^[[:space:]]*versionName:" "$ROOT_MANIFEST_FILE" | sed -n 1p \
    | sed -E "s/.*versionName:[[:space:]]*['\"]?([^'\"#]+)['\"]?.*/\1/" | xargs)"

  if [[ "$ROOT_MANIFEST_VERSION" == "$MANIFEST_VERSION" ]]; then
    ok "root vs chart: metadata.version stimmt ($ROOT_MANIFEST_VERSION)"
  else
    fail "root metadata.version ($ROOT_MANIFEST_VERSION) != chart ($MANIFEST_VERSION)"
  fi

  if [[ "$ROOT_MANIFEST_VERSIONNAME" == "$MANIFEST_VERSIONNAME" ]]; then
    ok "root vs chart: spec.versionName stimmt ($ROOT_MANIFEST_VERSIONNAME)"
  else
    fail "root spec.versionName ($ROOT_MANIFEST_VERSIONNAME) != chart ($MANIFEST_VERSIONNAME)"
  fi
fi

# ---------------------------------------------------------------------------
# 1c. upgradeDescription nennt die ausgelieferte Version
#     Ein Release-Skript hebt die Felder, nicht den handgeschriebenen Text.
# ---------------------------------------------------------------------------

section "upgradeDescription nennt die aktuelle Version"

for mf in "$MANIFEST_FILE" "$ROOT_MANIFEST_FILE"; do
  [[ -f "$mf" ]] || continue
  HEADLINE="$(awk '/^[[:space:]]*upgradeDescription:/{f=1;next} f&&NF{print;exit}' "$mf")"
  if [[ -z "$HEADLINE" ]]; then
    fail "$mf: upgradeDescription ist leer"
  elif [[ "$HEADLINE" == *"$CHART_VERSION"* ]]; then
    ok "$mf: erste Zeile nennt $CHART_VERSION"
  else
    fail "$mf: erste Zeile nennt nicht $CHART_VERSION — veraltete Release-Notiz?"
    echo "      $HEADLINE" | sed 's/^/    /'
  fi
done

# ---------------------------------------------------------------------------
# 1d0. Der offizielle Validator, wenn er da ist.
#      `olares-cli chart lint` faehrt dieselbe Pipeline, mit der der Store ein
#      Chart einliest — er kennt die Regeln besser als alles, was wir hier
#      nachbauen. Fehlt der Befehl, wird uebersprungen statt zu scheitern.
#      Immer das gepackte Chart pruefen, nicht den Ordner.
# ---------------------------------------------------------------------------

section "olares-cli chart lint (offizieller Validator)"

if command -v olares-cli >/dev/null 2>&1; then
  LINT_TMP="$(mktemp -d)"
  trap 'rm -rf "$LINT_TMP"' EXIT
  # Packen und Pruefen getrennt melden. Zusammengefasst verschluckte ein
  # Packfehler seine eigene Meldung und lief unter `set -u` in eine
  # ungesetzte Variable — die Ursache stand dann nirgends.
  if ! PACK_OUT="$(olares-cli chart package "$CHART_DIR/" -o "$LINT_TMP" 2>&1)"; then
    fail "olares-cli chart package: ${PACK_OUT}"
  elif ! LINT_OUT="$(olares-cli chart lint "$LINT_TMP"/*.tgz --with-rbac --with-security-context 2>&1)"; then
    fail "olares-cli chart lint: ${LINT_OUT}"
  else
    ok "olares-cli chart lint: OK"
  fi
else
  skip "olares-cli nicht im PATH — offizieller Validator uebersprungen"
fi

# ---------------------------------------------------------------------------
# 1d1. Neue Wertschluessel duerfen nicht direkt dereferenziert werden.
#
#      Ein Upgrade — per `helm --reuse-values` oder ueber den Markt — spielt
#      die beim Installieren gespeicherten Werte zurueck und mischt die
#      Vorgaben des neuen Charts NICHT ein. Ein Schluessel, den es in der
#      Vorversion nicht gab, fehlt dann zur Laufzeit. Steht im Template
#      `.Values.neu.feld`, stirbt das Rendern mit "nil pointer" und das
#      Upgrade scheitert, bevor irgendetwas passiert.
#
#      Nachstellen laesst sich das nicht durch Rendern: `helm template -f`
#      MISCHT die uebergebene Datei mit den Chart-Vorgaben, der Schluessel ist
#      also immer da. Deshalb statisch gegen den letzten Tag.
#
#      Sichere Schreibweise: (default (dict) .Values.neu).feld | default ""
#
#      Jede Pipeline hier braucht `|| true`. Unter `set -euo pipefail` beendet
#      ein grep ohne Treffer das ganze Skript — und "kein Treffer" ist hier
#      der Normalfall (frisches Repo ohne Tags).
# ---------------------------------------------------------------------------

section "neue Wertschluessel sind upgrade-sicher dereferenziert"

PREV_TAG="$(git tag --list 'v*' --sort=-v:refname 2>/dev/null | grep -v "^v${CHART_VERSION}$" | sed -n 1p || true)"

if [[ -z "$PREV_TAG" ]]; then
  skip "kein vorheriger Tag gefunden"
elif ! git cat-file -e "$PREV_TAG:$VALUES_FILE" 2>/dev/null; then
  skip "$PREV_TAG kennt $VALUES_FILE nicht"
else
  toplevel() { grep -E '^[a-zA-Z_][a-zA-Z0-9_]*:' | sed 's/:.*//' || true; }
  ALT_KEYS="$(git show "$PREV_TAG:$VALUES_FILE" 2>/dev/null | toplevel | sort -u || true)"
  NEU_KEYS="$(toplevel < "$VALUES_FILE" | sort -u || true)"
  ZUGEWACHSEN="$(comm -13 <(echo "$ALT_KEYS") <(echo "$NEU_KEYS") || true)"

  if [[ -z "$ZUGEWACHSEN" ]]; then
    ok "keine neuen Wertschluessel gegenueber $PREV_TAG"
  else
    unsicher=0
    for k in $ZUGEWACHSEN; do
      # Kommentarzeilen ausnehmen — sonst schlaegt der Guard bei der
      # Erklaerung an, die genau vor dieser Schreibweise warnt.
      TREFFER="$(grep -rn "\.Values\.${k}\." "$TEMPLATES_DIR" 2>/dev/null \
        | grep -vE ':[[:space:]]*#' | grep -v 'default (dict)' || true)"
      if [[ -n "$TREFFER" ]]; then
        fail "neuer Schluessel '$k' (seit $PREV_TAG) wird direkt dereferenziert — Upgrade wuerde scheitern:"
        echo "$TREFFER" | sed 's/^/      /'
        unsicher=1
      fi
    done
    if [[ $unsicher -eq 0 ]]; then
      ok "neue Schluessel gegenueber $PREV_TAG ($(echo "$ZUGEWACHSEN" | tr '\n' ' ')) sicher dereferenziert"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 1d2. Olares-Pin passend zur Manifest-Generation
#      v3 verlangt genau '>=1.12.6-0' — offen nach oben, mit '-0', damit
#      Tages- und RC-Builds matchen. Die aeltere Regel (geschlossenes
#      Intervall) galt fuer v1 und wuerde hier die Version aussperren, auf
#      der die Box laeuft.
# ---------------------------------------------------------------------------

section "olares-Dependency passt zur Manifest-Generation"

for mf in "$MANIFEST_FILE" "$ROOT_MANIFEST_FILE"; do
  [[ -f "$mf" ]] || continue
  DEP_VERSION="$(awk '/^[[:space:]]*-[[:space:]]*name:[[:space:]]*olares[[:space:]]*$/{f=1} f&&/^[[:space:]]*version:/{gsub(/.*version:[[:space:]]*/,""); gsub(/['"'"'"]/,""); print; exit}' "$mf")"
  API_VERSION="$(awk '/^apiVersion:/{gsub(/.*apiVersion:[[:space:]]*/,""); gsub(/['"'"'"]/,""); print; exit}' "$mf")"

  if [[ -z "$DEP_VERSION" ]]; then
    fail "$mf: keine options.dependencies[name=olares].version gefunden"
  elif [[ "$API_VERSION" == "v3" ]]; then
    if [[ "$DEP_VERSION" == ">=1.12.6-0" ]]; then
      ok "$mf: olares-Dependency '>=1.12.6-0' (v3)"
    else
      fail "$mf: apiVersion=v3 verlangt exakt '>=1.12.6-0', gefunden '$DEP_VERSION'"
    fi
  elif [[ "$DEP_VERSION" == *"<"* ]]; then
    ok "$mf: olares-Dependency begrenzt ($DEP_VERSION, vor v3)"
  else
    fail "$mf: olares-Dependency '$DEP_VERSION' ohne obere Grenze — Upload wird 400 liefern"
  fi
done

# ---------------------------------------------------------------------------
# 1d3. Manifest-Optionen, die move braucht
#      apiTimeout 0: sonst kappt der Envoy-Sidecar jede Antwort nach 15 s.
#      runAsUser als String: als Bool/Zahl scheitert der Parser
#      ("invalid character 'i' in literal false").
#      openMethod window: sonst zeigt die Kachel "running" und oeffnet nichts.
# ---------------------------------------------------------------------------

section "Manifest-Optionen"

TIMEOUT="$(grep -E "^[[:space:]]*apiTimeout:" "$MANIFEST_FILE" | sed -n 1p \
  | sed -E "s/.*apiTimeout:[[:space:]]*([^[:space:]#]+).*/\1/" || true)"
if [[ "$TIMEOUT" == "0" ]]; then
  ok "options.apiTimeout: 0"
else
  fail "options.apiTimeout = '${TIMEOUT:-fehlt}' — Envoy kappt sonst nach 15 s"
fi

if grep -E "^[[:space:]]*runAsUser:" "$MANIFEST_FILE" >/dev/null 2>&1; then
  if grep -E "^[[:space:]]*runAsUser:[[:space:]]*[\"'][0-9]+[\"']" "$MANIFEST_FILE" >/dev/null 2>&1; then
    ok "options.runAsUser ist ein String"
  else
    fail "options.runAsUser muss ein String sein (\"1000\"), nicht Zahl oder Bool"
  fi
fi

if grep -E "^[[:space:]]*openMethod:[[:space:]]*window" "$MANIFEST_FILE" >/dev/null 2>&1; then
  ok "entrance openMethod: window"
else
  fail "entrance ohne 'openMethod: window' — Kachel zeigt 'running' und oeffnet nichts"
fi

# ---------------------------------------------------------------------------
# 1e. hostPath-Deployments brauchen strategy: Recreate
#     RollingUpdate liesse zwei Pods auf dasselbe Host-Verzeichnis laufen.
#     Der Markt lehnt die Kombination beim Upload mit HTTP 400 ab. k8s
#     faellt ohne strategy-Block auf RollingUpdate zurueck — "kein Block"
#     ist also ein Fehler, kein Bestehen.
# ---------------------------------------------------------------------------

section "hostPath-Deployments nutzen strategy: Recreate"

for f in "$TEMPLATES_DIR"/deployment-*.yaml; do
  [[ -e "$f" ]] || continue
  grep -q "hostPath" "$f" || continue
  name="$(basename "$f")"
  if grep -qE "^[[:space:]]*type:[[:space:]]*Recreate[[:space:]]*$" "$f"; then
    ok "$name: hostPath + Recreate"
  else
    fail "$name: hostPath ohne 'strategy: type: Recreate' — Upload wird 400 liefern"
  fi
done

# ---------------------------------------------------------------------------
# 1f. Container-Ressourcen muessen ins Manifest-Budget passen
#     Der Markt addiert jeden Container — Init-Container eingeschlossen — und
#     lehnt mit HTTP 400 ab, wenn eine Summe required*/limited* uebersteigt.
#     Reines awk, damit CI kein PyYAML braucht.
# ---------------------------------------------------------------------------

section "Container-Ressourcen passen ins Manifest-Budget"

if command -v helm >/dev/null 2>&1 && [[ -f "$VALUES_STUB" ]]; then
  SUMS="$(helm template "$APP" "$CHART_DIR/" -f "$VALUES_STUB" 2>/dev/null | awk '
    function tocpu(v) { if (v ~ /m$/) { sub(/m$/,"",v); return v+0 } else { return v*1000 } }
    function tomem(v) {
      if (v ~ /Gi$/) { sub(/Gi$/,"",v); return v*1024 }
      if (v ~ /Mi$/) { sub(/Mi$/,"",v); return v }
      if (v ~ /Ki$/) { sub(/Ki$/,"",v); return v/1024 }
      return v/1048576
    }
    /^[[:space:]]*resources:[[:space:]]*$/ { inres=1; mode=""; next }
    inres && /^[[:space:]]*requests:[[:space:]]*$/ { mode="req"; next }
    inres && /^[[:space:]]*limits:[[:space:]]*$/   { mode="lim"; next }
    inres && /^[[:space:]]*cpu:/ {
      v=$2; gsub(/["'"'"']/,"",v)
      if (mode=="req") rc+=tocpu(v); else if (mode=="lim") lc+=tocpu(v)
      next
    }
    inres && /^[[:space:]]*memory:/ {
      v=$2; gsub(/["'"'"']/,"",v)
      if (mode=="req") rm+=tomem(v); else if (mode=="lim") lm+=tomem(v)
      next
    }
    inres && !/^[[:space:]]*(requests|limits|cpu|memory):/ { inres=0; mode="" }
    END { printf "%d %d %d %d", rc, lc, rm, lm }
  ')"
  read -r SUM_RC SUM_LC SUM_RM SUM_LM <<< "$SUMS"

  budget() {
    local key="$1" raw
    raw="$(grep -E "^[[:space:]]*${key}:" "$MANIFEST_FILE" | sed -n 1p \
      | sed -E "s/.*${key}:[[:space:]]*([^[:space:]#]+).*/\1/")"
    case "$raw" in
      *m)  echo "${raw%m}" ;;
      *Gi) echo $(( ${raw%Gi} * 1024 )) ;;
      *Mi) echo "${raw%Mi}" ;;
      *)   echo $(( raw * 1000 )) ;;
    esac
  }

  check_sum() {
    if [[ "$2" -le "$3" ]]; then
      ok "$1: $2$4 <= $3$4"
    else
      fail "$1: $2$4 uebersteigt $3$4 — Upload wird 400 liefern"
    fi
  }

  check_sum "requests.cpu vs requiredCpu"       "$SUM_RC" "$(budget requiredCpu)"    "m"
  check_sum "limits.cpu vs limitedCpu"          "$SUM_LC" "$(budget limitedCpu)"     "m"
  check_sum "requests.memory vs requiredMemory" "$SUM_RM" "$(budget requiredMemory)" "Mi"
  check_sum "limits.memory vs limitedMemory"    "$SUM_LM" "$(budget limitedMemory)"  "Mi"
else
  skip "helm oder Stub fehlt — Ressourcen-Guard uebersprungen"
fi

# ---------------------------------------------------------------------------
# 1g. Root-Container nur aus beclab-Images
#     Der Markt lehnt "non-beclab image ... runs with root-equivalent
#     securityContext" ab. Der init-chown darf root sein, aber nur aus
#     docker.io/beclab/aboveos-busybox. Setzt voraus, dass `image:` innerhalb
#     eines Container-Blocks vor `securityContext:` steht.
# ---------------------------------------------------------------------------

section "Root-Container nutzen ein beclab-Image"

if command -v helm >/dev/null 2>&1 && [[ -f "$VALUES_STUB" ]]; then
  ROOT_VIOLATIONS="$(helm template "$APP" "$CHART_DIR/" -f "$VALUES_STUB" 2>/dev/null | awk '
    /^[[:space:]]*-[[:space:]]*name:/ { img=""; cname=$3 }
    /^[[:space:]]*image:/ { img=$2; gsub(/["'"'"']/,"",img) }
    /^[[:space:]]*runAsUser:[[:space:]]*0[[:space:]]*$/ {
      if (img != "" && img !~ /beclab/) print cname " -> " img
    }
  ' || true)"
  if [[ -z "$ROOT_VIOLATIONS" ]]; then
    ok "kein Root-Container auf einem Nicht-beclab-Image"
  else
    fail "Root-Container auf Nicht-beclab-Image — Upload wird 400 liefern:"
    echo "$ROOT_VIOLATIONS" | sed 's/^/      /'
  fi
else
  skip "helm oder Stub fehlt — Root-Image-Guard uebersprungen"
fi

# ---------------------------------------------------------------------------
# 2. Kein .Files.Get — Olares' Chart-Renderer kennt .Files nicht
#    Nur echte Template-Nutzung melden: muss innerhalb von {{ }} stehen.
#    Kommentare, die .Files erwaehnen, sind in Ordnung.
# ---------------------------------------------------------------------------

section "kein .Files.* in den Templates"

if grep -rEn "\{\{[^}]*\.Files\.(Get|Glob|AsConfig|AsSecrets)" "$TEMPLATES_DIR" >/dev/null 2>&1; then
  fail ".Files.* innerhalb von {{ }} — der Renderer lehnt das Chart ab"
  grep -rEn "\{\{[^}]*\.Files\.(Get|Glob|AsConfig|AsSecrets)" "$TEMPLATES_DIR" | sed 's/^/    /'
else
  ok "keine .Files.*-Aufrufe"
fi

# ---------------------------------------------------------------------------
# 3. Keine Helm-Hooks
#    Das Namespace-Label ns-owner kommt erst NACH erfolgreichem helm install;
#    bis dahin sperrt die NetworkPolicy die Middleware. Ein Hook, der die DB
#    braucht, kann sie nie erreichen.
# ---------------------------------------------------------------------------

section "keine Helm-Hooks"

if grep -rn "helm\.sh/hook:" "$TEMPLATES_DIR" >/dev/null 2>&1; then
  fail "helm.sh/hook gefunden — Migrationen gehoeren in einen Init-Container mit Wiederholschleife"
  grep -rn "helm\.sh/hook" "$TEMPLATES_DIR" | sed 's/^/    /'
else
  ok "keine helm.sh/hook-Annotationen"
fi

# ---------------------------------------------------------------------------
# 4. Kein runAsInternal: true — Studio-Merkmal, bricht den check-auth-Init
# ---------------------------------------------------------------------------

section "kein runAsInternal: true"

if grep -E "^[[:space:]]*runAsInternal:[[:space:]]*true" "$MANIFEST_FILE" >/dev/null 2>&1; then
  fail "runAsInternal: true im Manifest — bricht Envoy (Init:CrashLoopBackOff)"
  grep -nE "runAsInternal:" "$MANIFEST_FILE" | sed 's/^/    /'
else
  ok "kein runAsInternal: true"
fi

# ---------------------------------------------------------------------------
# 4a. Verbotene Kinds und Zeichenketten
#     Nur ClusterIP ist erlaubt. Und der Manifest-Pruefer durchsucht den
#     rohen Dateitext nach OLARES_USER_, auch in Kommentaren.
# ---------------------------------------------------------------------------

section "verbotene Kinds und Zeichenketten"

for bad in hostNetwork NodePort LoadBalancer ClusterRoleBinding; do
  if grep -rn "$bad" "$TEMPLATES_DIR" >/dev/null 2>&1; then
    fail "$bad im Template — nur ClusterIP ist erlaubt"
    grep -rn "$bad" "$TEMPLATES_DIR" | sed 's/^/    /'
  fi
done

if grep -rn "OLARES_USER_" "$CHART_DIR" "$ROOT_MANIFEST_FILE" >/dev/null 2>&1; then
  fail "Zeichenkette OLARES_USER_ gefunden — Weg ist envs: mit valueFrom + .Values.olaresEnv.<X>"
  grep -rn "OLARES_USER_" "$CHART_DIR" "$ROOT_MANIFEST_FILE" | sed 's/^/    /'
else
  ok "kein OLARES_USER_ im Chart-Text"
fi

if grep -rEn "\{\{[[:space:]]*\.Release\.Name" "$TEMPLATES_DIR" >/dev/null 2>&1; then
  fail "metadata.name aus .Release.Name — muss literal '$APP' sein"
else
  ok "keine .Release.Name-Verwendung"
fi

# ---------------------------------------------------------------------------
# 5. Image-Tags folgen .Chart.AppVersion
#
#    Ein Markt-Upgrade spielt die bei der Installation aufgezeichneten Werte
#    zurueck und uebernimmt die Vorgaben des neuen Charts NICHT. Ein in der
#    values.yaml stehender Tag friert damit ein: die Box meldete die neue
#    Chart-Version und lief mit den alten Images, bei gruenen Health-Checks.
#    Chart-Metadaten kommen dagegen frisch an — der Tag haengt deshalb an
#    .Chart.AppVersion, und values.yaml traegt einen Tag nur, um Images in
#    einem reinen Chart-Release absichtlich zurueckzuhalten.
# ---------------------------------------------------------------------------

section "Image-Tags folgen .Chart.AppVersion"

bad_form=0
while IFS= read -r line; do
  [[ -n "$line" ]] || continue
  file="${line%%:*}"
  if [[ "$line" != *"default .Chart.AppVersion"* ]]; then
    fail "$(basename "$file"): Image-Tag nicht aus .Chart.AppVersion abgeleitet"
    echo "    ${line#*:}" | sed 's/^/  /'
    bad_form=1
  fi
done < <(grep -n "image:.*\.Values\.images\." "$TEMPLATES_DIR"/*.yaml 2>/dev/null || true)
[[ "$bad_form" -eq 0 ]] && ok "alle Workload-Images nutzen '| default .Chart.AppVersion'"

PINS="$(grep -E "^[[:space:]]+tag:" "$VALUES_FILE" \
       | grep -vE 'tag:[[:space:]]*("")|('"''"')[[:space:]]*$' || true)"
if [[ -z "$PINS" ]]; then
  ok "values.yaml pinnt nichts — Images folgen der Chart-Version"
else
  PIN_VALUES="$(echo "$PINS" | sed -E "s/.*tag:[[:space:]]*[\"']?([^[:space:]\"'#]+).*/\1/" | sort -u)"
  if echo "$PIN_VALUES" | grep -qx "$CHART_VERSION"; then
    fail "values.yaml pinnt den Tag auf die Chart-Version ($CHART_VERSION) — redundant, und friert beim naechsten Upgrade ein. Leer lassen."
  else
    skip "values.yaml pinnt Image-Tags auf $(echo "$PIN_VALUES" | tr '\n' ' ') — nur bei einem reinen Chart-Release korrekt"
  fi
fi

# Image als ein gequoteter String, nicht als Mapping.
if grep -rEn "^[[:space:]]*image:[[:space:]]*$" "$TEMPLATES_DIR" >/dev/null 2>&1; then
  fail "image: als Mapping — muss ein gequoteter String sein"
else
  ok "image: ueberall als String"
fi

# ---------------------------------------------------------------------------
# 6. SQL-Drift: db/migrations/ == move/files/
#    Einzige Quelle der Wahrheit. Der Generator inlined die Dateien in eine
#    ConfigMap, weil .Files.Get nicht verfuegbar ist.
# ---------------------------------------------------------------------------

section "SQL-Drift ($MIGRATIONS_SRC vs $MIGRATIONS_DST)"

if [[ ! -d "$MIGRATIONS_SRC" ]]; then
  skip "$MIGRATIONS_SRC existiert noch nicht"
else
  drift=0
  for src in "$MIGRATIONS_SRC"/*.sql; do
    [[ -e "$src" ]] || continue
    name="$(basename "$src")"
    dst="$MIGRATIONS_DST/$name"
    if [[ ! -f "$dst" ]]; then
      fail "$dst fehlt (python3 scripts/regen-migrations.py)"
      drift=$((drift + 1))
    elif ! diff -q "$src" "$dst" >/dev/null 2>&1; then
      fail "$dst weicht von $src ab (python3 scripts/regen-migrations.py)"
      drift=$((drift + 1))
    fi
  done
  [[ "$drift" -eq 0 ]] && ok "$MIGRATIONS_SRC und $MIGRATIONS_DST sind synchron"
fi

# ---------------------------------------------------------------------------
# 7. ConfigMap ist regenerierbar
# ---------------------------------------------------------------------------

section "configmap-migrations.yaml ist regenerierbar"

CM="$TEMPLATES_DIR/configmap-migrations.yaml"
if [[ ! -f "$CM" ]]; then
  skip "$CM existiert noch nicht"
elif ! command -v python3 >/dev/null 2>&1; then
  skip "python3 nicht verfuegbar"
elif [[ ! -f scripts/regen-migrations.py ]]; then
  skip "scripts/regen-migrations.py fehlt"
else
  cp "$CM" /tmp/configmap-migrations.backup
  python3 scripts/regen-migrations.py >/dev/null
  if diff -q /tmp/configmap-migrations.backup "$CM" >/dev/null 2>&1; then
    ok "configmap-migrations.yaml entspricht der Generator-Ausgabe"
    rm /tmp/configmap-migrations.backup
  else
    fail "configmap-migrations.yaml ist veraltet — regenerierte Fassung committen"
    cp /tmp/configmap-migrations.backup "$CM"
    rm /tmp/configmap-migrations.backup
  fi
fi

# ---------------------------------------------------------------------------
# 8. helm lint + template, inklusive Render ohne neue userspace-Schluessel
#
#    Olares setzt `userspace.<x>` nur fuer die Berechtigungen, die eine App
#    bei der INSTALLATION angefordert hat, und ein Upgrade spielt genau diese
#    Werte zurueck. Kommt spaeter eine Berechtigung dazu, rendert das Chart
#    auf jeder Bestandsbox `hostPath.path:` leer — die API lehnt ab mit
#    "volumes[N].hostPath.path: Required value". Der Stub traegt alle Pfade
#    und wuerde es nie zeigen.
# ---------------------------------------------------------------------------

section "helm lint + template"

if ! command -v helm >/dev/null 2>&1; then
  skip "helm nicht installiert"
elif [[ ! -f "$VALUES_STUB" ]]; then
  skip "$VALUES_STUB fehlt"
else
  if helm lint "$CHART_DIR/" -f "$VALUES_STUB" >/tmp/helm-lint.log 2>&1; then
    ok "helm lint besteht"
  else
    fail "helm lint gescheitert:"
    sed 's/^/    /' /tmp/helm-lint.log
  fi

  if helm template "$APP" "$CHART_DIR/" -f "$VALUES_STUB" >/tmp/helm-template.log 2>&1; then
    ok "helm template rendert"
  else
    fail "helm template gescheitert:"
    sed 's/^/    /' /tmp/helm-template.log
  fi

  ALT_WERTE="$(mktemp)"
  awk '!/^[[:space:]]+appCommon:/' "$VALUES_STUB" > "$ALT_WERTE"
  if helm template "$APP" "$CHART_DIR/" -f "$ALT_WERTE" >/tmp/helm-template-alt.log 2>&1; then
    LEERE_PFADE="$(grep -cE '^[[:space:]]+path:[[:space:]]*$' /tmp/helm-template-alt.log || true)"
    if [[ "$LEERE_PFADE" == "0" ]]; then
      ok "ohne userspace.appCommon: kein hostPath ohne Pfad"
    else
      fail "ohne userspace.appCommon rendern $LEERE_PFADE hostPath mit leerem Pfad — die API lehnt das Deployment ab"
    fi
  else
    fail "helm template ohne userspace.appCommon gescheitert:"
    sed 's/^/    /' /tmp/helm-template-alt.log
  fi
  rm -f "$ALT_WERTE"
fi

# ---------------------------------------------------------------------------
# 9. Pflichtfelder und Namensregex
# ---------------------------------------------------------------------------

section "OlaresManifest Pflichtfelder"

for field in "name:" "appid:" "title:" "version:" "icon:" "requiredDisk:" "supportArch:"; do
  if grep -E "^[[:space:]]*${field}" "$MANIFEST_FILE" >/dev/null 2>&1; then
    ok "$field vorhanden"
  else
    fail "$field fehlt im OlaresManifest"
  fi
done

NAME="$(extract "$MANIFEST_FILE" "  name")"
if [[ "$NAME" =~ ^[a-z0-9]{1,30}$ ]]; then
  ok "metadata.name '$NAME' passt auf ^[a-z0-9]{1,30}\$"
else
  fail "metadata.name '$NAME' verletzt ^[a-z0-9]{1,30}\$ (keine Bindestriche)"
fi

if [[ "$NAME" == "$APP" ]]; then
  ok "metadata.name == '$APP'"
else
  fail "metadata.name '$NAME' != '$APP' — Ordner, Chart.yaml.name, metadata.name und appid muessen gleich sein"
fi

# ---------------------------------------------------------------------------
# Ende
# ---------------------------------------------------------------------------

printf "\n"
if [[ "$FAILED" -gt 0 ]]; then
  red "$FAILED Pruefung(en) gescheitert"
  exit 1
else
  green "alle Pruefungen bestanden (Version $CHART_VERSION)"
fi
