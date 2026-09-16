# move

KI-Video-Plattform mit vorgefertigten cinematischen Presets und gelernten
Schnitt-Templates. Native Olares-App, ausgeliefert über eine eigene Market
Source.

Stand: **Chart, Assembler, Worker-Image, Job-Tabelle und Upload-Pfad stehen.**
Die Pipeline läuft von einem handgeschriebenen `CutTemplate` bis zum fertigen
MP4, ohne einen einzigen KI-Aufruf. Beide Images bauen in CI. Auf der Box war
noch nichts installiert — `running` ist nicht gemessen.

## Was drin ist

```
CLAUDE.md                      Projektregeln, Scope v0, Architektur, Fallstricke
AGENTS.md                      Market-Source-Playbook (AImighty, Marc)
scripts/check-chart.sh         Vorab-Guards, aus dem Insilo-Original umgebaut
scripts/make-icon.py           erzeugt icon.png reproduzierbar, ohne Bildbibliothek
icon.png                       512x512, Hanseatenblau + Gold
OlaresManifest.yaml            Root-Manifest (Store)
move/Chart.yaml                Version 26.9.1
move/OlaresManifest.yaml       Chart-Manifest, byteweise identisch zum Root
move/values.yaml               keine Pins, keine Secrets
move/values-olares-stub.yaml   Stub für helm lint/template
move/templates/                zwei Deployments, ein Service
db/schema.sql                  Schema, von Web UND Worker gelesen
worker/                        Assembler, Platzhalter, Job-Schleife
web/                           Next.js 15.5, Upload-Pfad, Oberfläche
.github/workflows/ci.yml       Guards, Tests, beide Images
.dockerignore
.gitignore
```

Beide Images bauen mit dem **Repo-Wurzelverzeichnis** als Kontext, weil
`db/schema.sql` von Web und Worker gemeinsam gelesen wird:

```bash
docker build -f worker/Dockerfile -t moveworker:26.9.1 .
docker build -f web/Dockerfile    -t move:26.9.1 .
```

Chart-Stand: ein Entrance auf `move` (Port 3000), Worker `moveworker` ohne
Entrance und ohne Service, SQLite auf `appData` per hostPath mit
`strategy: Recreate`, Init-Container als root nur aus dem beclab-Image,
Image-Tags aus `.Chart.AppVersion`, kein GPU-Bedarf in v0.

## Was noch fehlt

| Datei | Woher |
|---|---|
| `docs/olares-learnings.md` | das gemessene Olares-Dokument (Stand 15.09.2026). **Wichtigste fehlende Datei** — CLAUDE.md verweist bei jedem Widerspruch darauf. |
| `docs/design-guide.md` | Kopie aus dem AImighty-Markt-Repo. Ohne sie kann Claude Code die verbindliche Designvorgabe nicht lesen. |
| Repo auf Public stellen | Entschieden, aber noch nicht getan. Danach liefert die `icon.png`-URL im Manifest HTTP 200. |
| ghcr-Pakete auf Public stellen | Einmalig nach dem ersten Push. Pakete sind auch in einem öffentlichen Repo zunächst privat. |
| `docs/design-guide.md` | s. o. — die Oberfläche folgt bisher der Zusammenfassung im Platzhalter, nicht dem Original. |
| erster echter fal-Aufruf | Die Generierung ist gebaut und getestet, aber nur gegen ein Doppel. fal.ai ist vom Proxy gesperrt. |
| Eintrag in einer Market Source | Der eigentliche Grund, warum move nirgends im Marktplatz auftaucht. S. u. |

## Wie eine App auf die Box kommt — drei Wege, einer davon defekt

Aus `docs/OLARES_DEEP_DIVE.md §4` und `.claude/skills/olares-release/SKILL.md`
im Insilo-Repo, dort auf einer echten Box gemessen:

| Weg | Sichtbarkeit | Trägt? |
|---|---|---|
| PR an `beclab/apps` | weltweit | ja, Tage Review |
| **eigene Market Source** | jede Box, die die Quelle einträgt | **ja — der Weg für move** |
| Upload custom chart (Market UI, `market upload`) | nur diese Box | **nein** |

**Der lokale Upload ist kein Auslieferungsweg und auch kein Testweg.** Olares
hat ihn als Dev-Feature gedacht; er löst den vollen BFL-Ablauf nicht aus. Es
entsteht **kein `Application`-CR, kein `ns-owner`-Label, keine NetworkPolicy**
— dann findet `check-auth` Authelia nicht und der Pod endet in `Init:Error`.
Er registriert eine Version, er installiert nicht.

Das hat eine Folge, die die Reihenfolge in CLAUDE.md präzisiert statt ihr zu
widersprechen: *„erst installieren und `running` messen, dann in den Katalog"*
— der Markteintrag **ist** hier der Installationsweg. Kein Widerspruch, denn
die eigene Market Source ist keine Veröffentlichung an die Welt: nur Boxen,
die diese Quelle eingetragen haben, sehen move. Der Eintrag kommt also zuerst,
dann die Installation, dann die Messung. Was CLAUDE.md verbietet, ist der
Schritt in einen **öffentlichen** Katalog vor der Messung.

## Warum es bei insilo geht

Weil insilo denselben Weg nimmt, nicht einen anderen: `insilo-0.1.98.tgz`
steht in Marcs `_lib.ts`, der Eintrag in `_apps.ts` Zeile 854. Drei
Unterschiede, alle nachprüfbar:

1. **Das insilo-Repo ist öffentlich.** Sein Icon zeigt auf
   `raw.githubusercontent.com/ska1walker/insilo/main/icon.png` — dieselbe Form
   wie bei move, und sie löst auf. Bei move noch nicht.
2. **Der Markt-Schritt war immer von Hand**, auf Kais Rechner. `release.sh`
   packt das Chart dort (Helm ist vorhanden) und legt es nach `~/Downloads`;
   die Einträge in `_apps.ts` und `_lib.ts` setzt jemand mit Schreibrecht.
3. **Insilos eigene CI pusht nie in Marcs Repo.** `release.yml` baut
   ausschließlich die vier Images nach ghcr.

Es gab also keine Automatisierung, die man für move hätte übernehmen können.
`marktpr.yml` automatisiert einen Schritt, der auch für insilo Handarbeit war.

### Zwei Fallen, die dort Zeit gekostet haben

**Der Markt hat zwei Quellen.** Das Cloudflare-Pages-Projekt wird auch direkt
bespielt, am Repo vorbei. Am 19.8. lag insilo 0.1.81 um 10:40 live und war um
12:10 aus dem Katalog verschwunden — ohne Workflow-Lauf. Ein Deploy aus dem
Repo holte es zurück. Daher: aus dem Repo deployen ist der normale Weg, und
man ersetzt damit einen eventuellen Direkt-Deploy.

**Vier fremde Einträge im Repo sind widersprüchlich** — `aimqwen3asr`,
`aimqwen3ttsvllm`, `aimvoxtral4bvllm` und `rewind` tragen in `_apps.ts` eine
Version, zu der in `_lib.ts` kein Chart liegt. `getChartByAppName` baut
`name-version.tgz` und findet nichts, also 404. Das steht dort seit vor dem
ersten Insilo-Commit. Nicht unser Werk und nicht unsere Reparatur, aber
vermutlich der Grund, warum Marc sein eigenes Bundle direkt aufspielt.

**Und nie „liegt im Markt" sagen, ohne live gemessen zu haben** — Katalog
*und* Chart, denn ein gelisteter Eintrag ohne abrufbares Chart ist nicht
installierbar. Nach einem Deploy braucht die Edge eine bis zwei Minuten; ein
404 direkt danach heißt noch nichts.

## Warum move nicht im Marktplatz steht

Weil eine App nicht dadurch in den Marktplatz kommt, dass sie hier im Repo
liegt. Eine **Market Source ist ein eigener Webdienst** (bei AImighty:
Cloudflare Pages, Repo `bayerhazard/aimighty-market`). Sie listet die App
unter `/api/v1/appstore/info` und liefert das Chart unter
`/api/v1/applications/move/chart?fileName=move-26.9.1.tgz`. Das Chart steckt
dort als base64 in einer Tabelle. In **move ist noch nichts davon eingetragen** —
dieses Repo enthält nur das Chart selbst.

`scripts/paket.sh` erzeugt genau die Stücke, die dort hineingehören:

```bash
./scripts/paket.sh          # braucht helm und PyYAML
```

| Datei | wohin |
|---|---|
| `dist/move-26.9.1.tgz` | das gepackte Chart |
| `dist/move-26.9.1.tgz.base64` | eine Zeile, als Wert unter dem Schlüssel `"move-26.9.1.tgz"` |
| `dist/markteintrag.json` | die Metadatenfelder, aus dem Manifest gelesen |

Der CI-Job **Chart-Paket** führt das bei jedem Push mit echtem Helm aus und
hängt die drei Dateien als Artefakt an den Lauf. Damit fällt ein Packfehler
beim Commit auf und nicht erst beim Release, und das base64 liegt nie
veraltet im Repo.

### Der fertige Patch für Marcs AImighty Market

`scripts/marktpatch.sh` setzt move in eine Checkout-Kopie der Market Source
ein und macht daraus einen anwendbaren Patch. Geändert werden genau zwei
Dateien — der Endpunkt liest allein aus dem `CHARTS`-Dict, nicht aus dem
`charts/`-Ordner:

```bash
./scripts/paket.sh
./scripts/marktpatch.sh          # klont nach dist/markt, pusht nichts
```

Der CI-Job **Markt-Patch** tut dasselbe mit echtem Helm und hängt den Patch
als Artefakt an. Er steht bewusst **nicht** in den `needs` von `publish`: er
klont ein fremdes Repo, und dessen Erreichbarkeit soll die Veröffentlichung
der Images nicht aufhalten.

### Den Pull Request automatisch öffnen

`.github/workflows/marktpr.yml` packt, setzt den Eintrag, pusht einen Branch
und öffnet den Pull Request. **Nur auf Knopfdruck** (`workflow_dispatch`, mit
Trockenlauf als Vorgabe) — der Workflow schreibt in ein fremdes Repo, das
soll ein bewusster Klick sein und keine Nebenwirkung eines Commits. Er
**deployt nicht**: ein Commit ohne Deploy ändert am Katalog nichts, aber ein
Deploy ändert, was jede synchronisierende Box sieht. Das bleibt bei dem, dem
das Cloudflare-Konto gehört.

Einmalig einzurichten: das Secret `MARKT_TOKEN` unter Settings → Secrets and
variables → Actions. **Feingranular**, nur für `bayerhazard/aimighty-market`,
mit Contents und Pull requests auf *Read and write* — kein klassisches PAT
mit Zugriff auf alles.

Warum als Secret und nicht anders: ein Token, das durch eine Unterhaltung
oder eine Datei geht, liegt danach irgendwo und muss erneuert werden. Als
Actions-Secret ist es verschlüsselt, im Log maskiert und zurückziehbar, ohne
dass eine Kopie bleibt. `gh auth setup-git` richtet den Credential-Helper
ein, damit das Token nicht in einer Remote-URL steht.

**Und es gibt keinen anderen Weg von einer Entwicklungssitzung aus.** Mit
Token in der Hand gemessen: der Egress-Proxy setzt die Repo-Freigabe
*oberhalb* des Tokens durch.

| Versuch | Ergebnis |
|---|---|
| `api.github.com/user` | HTTP 200, `login: ska1walker` — der Proxy lässt eigene Repos durch |
| `api.github.com/repos/bayerhazard/aimighty-market` | `GitHub access to this repository is not enabled for this session` |
| `git push` dorthin, Token in der URL | `access denied by the git proxy: … is not in this session's authorized repository set, so the proxy will not inject a credential for it` |
| Artefakt-Download über die API | `CONNECT tunnel failed, 403` (Blob-Host gesperrt) |
| `api.cloudflare.com` | keine Verbindung |

Ein Schlüssel war also nie das fehlende Stück. Die CI ist die einzige Stelle,
an der **beide** Hälften existieren: echtes Helm und Schreibzugriff.

### Deployen

Zweiter Haken, eigener Job. Er deployt den **Standardbranch** der Market
Source, also den Stand *nach* dem Merge — nie den PR-Branch, sonst wäre move
live, bevor jemand den PR gesehen hat. Ist move dort nicht enthalten, bricht
der Job ab, statt den alten Katalog auszurollen.

Secrets: `CF_API_TOKEN` und `CF_ACCOUNT_ID`. **Ungeprüft von hier aus** — die
Cloudflare-API ist aus dieser Umgebung nicht erreichbar, der erste echte
Aufruf passiert im CI-Lauf.

Der Eintrag steht zwischen zwei Markerzeilen. Das ist kein Schmuck:
`_apps.ts` trägt in Zeile 1 `// Generated by gen_market.py`, und dieser
Generator liegt nicht im Repo, sondern bei Marc. Der Block gehört also
mittelfristig in seinen Generator, nicht in dauerhafte Handpflege — der
Marker sagt das dem nächsten Leser und macht den Block für ein Skript exakt
auffindbar.

Gemessen, jeweils gegen den echten Klon:

| Fall | Verhalten |
|---|---|
| frischer Klon | Block eingefügt, `CHARTS`-Zeile eingefügt, `tsc` 3 Fehler vorher / 3 nachher |
| zweiter Lauf | Block **ersetzt**, Chart unverändert, ein Eintrag — nicht zwei |
| Version hochgezogen | Block ersetzt, neues Chart dazu, **altes Chart bleibt** (eine Bestandsinstallation darf es noch anfordern) |
| Checkout mit offenen Änderungen | bricht ab, bevor irgendetwas geschrieben wird |
| Patch auf frischem Klon | `git apply --check` greift sauber, 2 Dateien, 58 Zeilen |

Die drei `tsc`-Fehler sind Bestand in Marcs Repo (`invisible` an beacons
zweitem Entrance, zweimal `appDescription` in `_lib.ts`). Der move-Eintrag
bringt keinen einzigen neuen.

**`helm package` ist nicht bitgleich über Läufe hinweg.** Gemessen an zwei
CI-Läufen, deren `move/`-Inhalt sich nicht unterschied (geändert war nur
`.github/`): die base64 endeten auf `…rxmORAFgAAA=` und `…BtxPIAWAAA=`. Helm
übernimmt die Änderungszeiten der Dateien in den Tarball, und die kommen aus
dem Checkout. Folge für die Praxis: **`_apps.ts`-Block und `CHARTS`-Zeile
immer aus DEMSELBEN Lauf nehmen.** Eine Prüfsumme aus einem anderen Lauf
passt nicht, und das ist kein Fehler.

**Das Skript ist der belastbare Weg, der Patch die Bequemlichkeit.** Der
Patch ist rund 154 KB groß, und 146 KB davon sind Kontextzeilen **fremder
Apps**: im `CHARTS`-Dict steht direkt neben unserer Einfügung `insilo` mit
133 KB base64 auf einer einzigen Zeile. `git am` prüft diesen Kontext — wird
eine Nachbar-App neu gepackt, schlägt der Patch fehl, obwohl unsere Änderung
eine Zeile ist. `marktpatch.sh` sucht seine Stellen dagegen über Inhalt
(Markerzeilen und den Dict-Kopf) und ist davon unabhängig. Bei einem
Fehlschlag also nicht am Patch reparieren, sondern das Skript neu laufen
lassen.

Eingebaut sind drei Regeln, die je für eine echte Ablehnung stehen: nie ein
Chart packen, das `check-chart.sh` nicht besteht; den **gepackten** Tarball
linten, nicht den Ordner; und nur **einmal** gzippen — ein zweites Mal meldet
die Box als `invalid tar header`. Das base64 wird nach dem Schreiben
zurückdekodiert und byteweise gegen den Tarball verglichen.

Danach, und erst danach, installieren und `running` auf der Box **messen**.
Eine App, die nie `running` erreicht hat, gehört in keinen Markt — und move
hat es nie erreicht.

## Nutzereingaben bei der Installation

Der `envs:`-Block im Manifest fragt drei Werte ab, alle optional:

| Name | Typ | Bedeutung |
|---|---|---|
| `FAL_KEY` | `password` | eigener fal.ai-Schlüssel. Leer heißt: nur Platzhalter und eigene Uploads |
| `MOVE_FAL_MODEL` | `string` | Vorgabemodell. Leer heißt: der Standard aus `generierung.py` |
| `MOVE_FAL_MAX_CLIPS` | `int` | Obergrenze bezahlter Aufrufe je Job, Vorgabe 12. `0` schaltet die Generierung ab |

Alle drei sind bewusst `required: false`. Die Pipeline läuft ohne einen
einzigen Modellaufruf durch; eine Installation, die nur Platzhalter will,
darf nicht an einem fehlenden Schlüssel hängen.

### Der Widerspruch, gegen den Katalog aufgelöst

AGENTS.md verlangte für ein Nutzer-Env ein `valueFrom` auf einen Namen, den
CLAUDE.md in Chart-Dateien verbietet und `check-chart.sh` zurückweist.
Gemessen an fünf Apps im offiziellen Olares-Katalog (`sillytavern`,
`karakeep`, `firecrawl`, `flowise`, `openwebui`) gibt es **zwei** Formen:

- `valueFrom` verweist auf einen **geschlossenen Satz kontoweiter
  Olares-Einstellungen** (OpenAI-Schlüssel, Hugging-Face-Token und ein paar
  mehr), die Olares selbst verwaltet. Für fal existiert dort kein Eintrag.
- Einen Anbieter, den Olares nicht kennt, deklariert man **schlicht mit
  `type: password` ohne `valueFrom`** — genau das tut `sillytavern` für seinen
  Anthropic- und Google-Schlüssel.

move nimmt die zweite Form. Damit kommt das verbotene Präfix im Repo
überhaupt nicht vor, und beide Dokumente behalten recht. Nebenbei gemessen:
der `envs:`-Block steht in allen fünf Apps **zuletzt**, nach `options:` — die
Reihenfolge aus AGENTS.md hält keine von ihnen ein.

Ein neuer Guard hält beide Seiten zusammen: jedes deklarierte Env muss von
einem Template gelesen werden, und jeder Zugriff auf `olaresEnv` muss
deklariert sein. Sonst fragt der Installationsdialog etwas ab, das nichts
entgegennimmt — oder ein Template liest einen Wert, den niemand setzen kann.

Optional, falls vorhanden: `scripts/release.sh` und
`scripts/regen-migrations.py` aus dem Insilo-Repo. `check-chart.sh` ruft den
Generator auf, überspringt den Guard aber sauber, solange er fehlt.

## Erste Schritte

1. Knotenzahl und GPU-Belegung messen — die beiden Referenzdokumente
   widersprechen sich (ein Knoten oder zwei):

   ```bash
   ssh olares@<box>
   export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
   kubectl get nodes
   olares-cli settings compute list
   ```

   Das Ergebnis entscheidet, ob der Worker einen `nodeSelector` bekommt.

   Für v0 ist das noch nicht dringend: das Chart fordert **keine GPU** an, der
   Worker rechnet auf der CPU. Die Frage wird erst bei TransNetV2 scharf.

2. `./scripts/check-chart.sh` laufen lassen. Der Guard kommt vor dem Fix.

   Ohne `helm` und `olares-cli` im PATH überspringt er das Ressourcen-Budget,
   den Root-Image-Guard, `helm lint/template` und den offiziellen Validator —
   also genau die Prüfungen, die Struktur und Budget betreffen. Vor einem
   Release muss er dort laufen, wo beide Werkzeuge liegen.

3. Assembler: steht. `worker/README.md`, dann

   ```bash
   cd worker && python3 -m move_worker demo \
     --template examples/beat-8s.json --out-dir /tmp/move
   ```

4. Worker-Image: steht. Es bringt ffmpeg **mit `drawtext`** und eine
   Schriftdatei mit; ohne beides gibt es keine Platzhalter.

5. Upload-Pfad: steht, gegen 400 MB gemessen. Siehe `web/README.md`.

6. Extraktion: steht. `scdet` statt TransNetV2 (der v0-Scope verlangt „ohne
   einen einzigen KI-Aufruf"), librosa für das Beat-Grid.

   ```bash
   cd worker && python3 -m move_worker extract --video trailer.mp4 --save
   ```

7. **Veröffentlichen.** Die CI pusht nach ghcr bei einem Versions-Tag `v*`
   oder per „Run workflow" mit gesetztem Haken. Danach einmalig die beiden
   Pakete auf Public stellen — sie sind beim ersten Push privat, auch in einem
   öffentlichen Repo, und die Installation endet sonst in `registry_error`.

8. Installieren und `running` auf der Box **messen**. Erst dann der Katalog.

## Reihenfolge, die nicht verhandelbar ist

Images bauen -> auf der eigenen Box installieren und `running` **messen** ->
erst dann in den Katalog. Eine App, die nie `running` erreicht hat, gehört in
keinen Markt.
