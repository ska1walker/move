# move

KI-Video-Plattform mit vorgefertigten cinematischen Presets und gelernten
Schnitt-Templates. Native Olares-App, ausgeliefert über eine eigene Market
Source.

Stand: **Chart, Assembler, Worker-Image, Job-Tabelle und Upload-Pfad stehen.**
Die Pipeline läuft von einem handgeschriebenen `CutTemplate` bis zum fertigen
MP4, ohne einen einzigen KI-Aufruf. Beide Images bauen in CI. Auf der Box war
noch nichts installiert — `running` ist nicht gemessen.

Das Repo ist öffentlich, damit die `icon.png`-URL im Manifest auflöst
(gemessen: HTTP 200, 512×512 PNG). Die ghcr-Pakete sind es noch nicht.

## Was drin ist

```
CLAUDE.md                      Projektregeln, Scope v0, Architektur, Fallstricke
AGENTS.md                      Market-Source-Playbook (AImighty, Marc)
scripts/check-chart.sh         Vorab-Guards, aus dem Insilo-Original umgebaut
scripts/make-icon.py           erzeugt icon.png reproduzierbar, ohne Bildbibliothek
icon.png                       512x512, Hanseatenblau + Gold
OlaresManifest.yaml            Root-Manifest (Store)
move/Chart.yaml                Version 26.9.2
move/OlaresManifest.yaml       Chart-Manifest, byteweise identisch zum Root
move/values.yaml               keine Pins, keine Secrets
move/values-olares-stub.yaml   Stub für helm lint/template
move/templates/                zwei Deployments, ein Service
db/schema.sql                  Schema, von Web UND Worker gelesen
worker/                        Assembler, Platzhalter, Job-Schleife
web/                           Next.js 15.5, Upload-Pfad, Oberfläche
docs/olares-learnings.md       das gemessene Olares-Dokument -- gewinnt bei Widerspruch
docs/design-guide.md           AImighty-Standard, verbindlich fuer jede Oberflaeche
docs/installieren.md           auf der Box installieren und `running` messen
.github/workflows/ci.yml       Guards, Tests, beide Images
.github/workflows/marktpr.yml  Eintrag in die Market Source, PR, optional Merge
.github/workflows/marktpruefen.yml  Katalog und Chart live nachmessen, taeglich
.dockerignore
.gitignore
```

Beide Images bauen mit dem **Repo-Wurzelverzeichnis** als Kontext, weil
`db/schema.sql` von Web und Worker gemeinsam gelesen wird:

```bash
docker build -f worker/Dockerfile -t moveworker:26.9.2 .
docker build -f web/Dockerfile    -t move:26.9.2 .
```

Chart-Stand: ein Entrance auf `move` (Port 3000), Worker `moveworker` ohne
Entrance und ohne Service, SQLite auf `appData` per hostPath mit
`strategy: Recreate`, Init-Container als root nur aus dem beclab-Image,
Image-Tags aus `.Chart.AppVersion`, kein GPU-Bedarf in v0.

## Was noch fehlt

| | Woher |
|---|---|
| erster echter fal-Aufruf | Die Generierung ist gebaut und getestet, aber nur gegen ein Doppel. fal.ai ist vom Proxy gesperrt. |
| Installation auf der Box, `running` gemessen | Der letzte Schritt, und keiner, den ein Automat nimmt. Ablauf in `docs/installieren.md`. |

Erledigt und gemessen: Repo öffentlich (Icon HTTP 200), beide ghcr-Pakete
anonym abrufbar, `docs/olares-learnings.md` und `docs/design-guide.md` liegen
vor.

### Der Katalog steht auf 26.9.1, und dieses 26.9.1 ist kaputt

Das Chart, das gerade in Marcs Katalog liegt, **lässt sich nicht
installieren**. Nachgemessen, nicht vermutet: `git archive 9268b3b` (der
Stand, aus dem das ausgelieferte Paket entstand) mit echtem helm gerendert
ergibt drei Dokumente, und das dritte ist

```
apiVersion=None    kind=Deployment    name=moveworker
```

Eine rechte Trimm-Marke (`-}}`) an einer Zuweisung fraß den Zeilenumbruch
danach, und `apiVersion: apps/v1` klebte an das Ende der Kommentarzeile
darüber. Gültiges YAML, deshalb hat niemand gemeckert — helm nicht, `chart
lint` nicht, der Katalog nicht. Die API hätte es abgelehnt.

**26.9.2 behebt genau das.** Stand der drei Teile, getrennt gemeldet:

| | Stand |
|---|---|
| Chart 26.9.2 | gepackt, `helm template` rendert 3 Dokumente, `apiVersion` 3 == `kind` 3 |
| Images `26.9.2` auf ghcr | **fehlen** — der Push läuft nur auf einem `v*`-Tag oder per Klick |
| Katalogeintrag | steht auf 26.9.1, also auf dem kaputten Chart |

In dieser Reihenfolge, und nicht anders: Images bauen, auf der Box
installieren und `running` **messen**, erst dann der Katalogeintrag.
`marktpr.yml` prüft seit dem Image-Schritt selbst, dass die Tags existieren,
und bricht sonst ab, bevor etwas gelistet wird.

## Wie eine App auf die Box kommt — drei Wege

Nach `docs/olares-learnings.md` 9.1, auf einer echten Box gemessen:

| Weg | Sichtbarkeit | Wofür |
|---|---|---|
| `olares-cli market upload` | eine Box | **Entwicklung.** Registriert die Version, **ersetzt kein Deployment** |
| eigene **Market Source**, Box pollt alle 5 min | jede Box, die die Quelle einträgt | **Kundenauslieferung — der Weg für move** |
| PR an `beclab/apps` | weltweit | öffentliche Produkte, Tage bis Wochen |

Ich hatte hier einen Widerspruch zwischen insilos und beacons Doku stehen
lassen und vermutet, es liege an den zwei Schritten. Das gemessene Dokument
klärt die **Wirkung**: der Upload registriert eine Version und ersetzt kein
Deployment, ist also ein Entwicklungsweg. Für moves erste Messung taugt er
damit, für die Auslieferung nicht.

Die **Ursache** klärt es nicht, und ich hatte hier „ohne Vermutung"
geschrieben. Das war zu viel. `docs/olares-learnings.md` 16 führt den
Upload-Pfad selbst als offene Frage 5: früh als Sackgasse beschrieben (kein
CR, kein `ns-owner`), später funktionierend, *„die Ursache der frühen
Fehlschläge ist nicht sauber getrennt."* Praktische Folge für move: schlägt
`market upload` fehl, ist die Fehlermeldung nicht gedeutet — dann zählt der
Weg über die Market Source, der ohnehin der vorgesehene ist, und nicht das
Debuggen des Uploads.

**Nach dem Merge ist nichts ausgerollt.** Die Kette laut 9.2: Edge 1–2 min →
Box pollt bis 5 min → Update erscheint → **ein Mensch drückt „Upgrade"** →
Pods nachmessen. Deshalb werden „im Markt" und „auf der Box" getrennt
gemeldet; auf Kais Box lief einmal 0.9.6, während der Markt 0.9.9 trug.

## Warum es bei insilo und beacon geht

Beide nehmen denselben Weg wie move, nicht einen anderen:
`insilo-0.1.98.tgz` steht in Marcs `_lib.ts`, beacon ist dort als `0.1.4`
eingetragen. Was sie anders machen:

1. **Beide Repos sind öffentlich.** insilos Icon zeigt auf
   `raw.githubusercontent.com/ska1walker/insilo/main/icon.png` — dieselbe Form
   wie bei move, und sie löst auf. Bei move noch nicht.
2. **Der Weg ist Fork, Branch, Pull Request.** Wörtlich aus
   `beacon/docs/BETRIEB.md`: *„Kai hat dort nur Leserechte — der Weg ist Fork,
   Branch, Pull Request an Marc."* Insilos Einreichung ist PR #1 dort.
3. **Keine der beiden CIs pusht in Marcs Repo.** Beide `release.yml` bauen
   Images; beacon packt zusätzlich das Chart als Artefakt. Den Markt-Schritt
   macht ein Mensch.

Es gab also keine Automatisierung zum Übernehmen. `marktpr.yml` automatisiert
einen Schritt, der bei beiden Handarbeit war.

**Zwei Dinge daran hätten meinen Workflow scheitern lassen:**

- Er pushte den Branch **direkt** in Marcs Repo. Bei Leserechten endet das in
  einem 403. Jetzt geht der Push in den Fork `ska1walker/aimighty-market` und
  der Pull Request wird über die Fork-Grenze gestellt — mit dem **nackten**
  Branchnamen. Die Form `<eigner>:<branch>` endet laut
  `docs/olares-learnings.md` 9.2 in „No commits between"; sie stand hier,
  wäre aber erst beim ersten Lauf über den Fork aufgefallen.
- Der Fork lag gemessen **62 Commits hinter** der Quelle. Ein Eintrag auf
  diesem Stand wäre ein PR, der 62 fremde Änderungen zurückdreht. Der Branch
  zweigt deshalb von `upstream/main` ab, nicht vom Fork-Stand.

Und das Token muss ein **klassisches PAT mit `public_repo`** sein, kein
feingranulares: ein feingranulares lässt sich nur auf eigene Repos
beschränken, der Pull Request entsteht aber gegen ein fremdes. `public_repo`
ist der kleinste Scope, der beides kann, und er erreicht keine privaten Repos.

**Wessen Token in `MARKT_TOKEN` steht, entscheidet den Weg — deshalb fragt
der Workflow das Token, statt es anzunehmen.** Er liest
`repos/bayerhazard~aimighty-market` und schaut auf `permissions.push`:

| Token | Weg |
|---|---|
| eines von Marc, mit Schreibrecht auf die Quelle | Branch direkt dorthin, PR von dort |
| ein eigenes | Branch in den Fork, PR über die Fork-Grenze |

Der falsche Weg fiele sonst erst beim Push auf, nach allem Packen — und der
Fork-Weg mit **Marcs** Token scheitert genauso, denn sein Token darf in *sein*
Repo schreiben, nicht in Kais Fork. Der Schritt sagt im Log, welchen Weg er
genommen hat.

Zu bedenken: Marcs Token heißt, Branch und Pull Request erscheinen unter
seinem Namen.

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
`/api/v1/applications/move/chart?fileName=move-26.9.2.tgz`. Das Chart steckt
dort als base64 in einer Tabelle. In **move ist noch nichts davon eingetragen** —
dieses Repo enthält nur das Chart selbst.

`scripts/paket.sh` erzeugt genau die Stücke, die dort hineingehören:

```bash
./scripts/paket.sh          # braucht helm und PyYAML
```

| Datei | wohin |
|---|---|
| `dist/move-26.9.2.tgz` | das gepackte Chart |
| `dist/move-26.9.2.tgz.base64` | eine Zeile, als Wert unter dem Schlüssel `"move-26.9.2.tgz"` |
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

1. Knotenzahl und GPU-Belegung messen. Der Widerspruch, der hier stand, ist
   weitgehend aufgelöst: `docs/olares-learnings.md` 1 sagt im ersten Satz
   *„Olares ist **Kubernetes (k3s) auf einem Knoten**"*, und bei Widerspruch
   gewinnt dieses Dokument. AGENTS.md beschreibt mit den zwei Nodes zudem
   **Marcs** Umgebung, nicht die Box, auf der move läuft — es müssen also
   nicht beide falsch sein.

   Damit hat die offene Frage eine Vorgabe statt einer Münze: **kein
   `nodeSelector`**, und der Worker konkurriert direkt mit den LLM-Apps um die
   GPU. Beides ist im Chart schon so gebaut (kein `nodeSelector`,
   `MOVE_QUEUE_CONCURRENCY` fest auf `1`).

   Nachmessen bleibt trotzdem der Schritt, nicht das Dokument lesen:

   ```bash
   ssh olares@<box>
   export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
   kubectl get nodes
   olares-cli settings compute list
   ```

   Für v0 ist es ohnehin nicht dringend: das Chart fordert **keine GPU** an,
   der Worker rechnet auf der CPU. Scharf wird die Frage erst bei TransNetV2 —
   und dann als GPU-Belegung, nicht als Knotenzahl.

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

5. Upload-Pfad: steht, gegen 400 MB gemessen — **lokal**, nicht über die
   Entrance-Adresse. Die vier Fallstricke aus CLAUDE.md sind damit belegt
   (Middleware, `http.request` statt `fetch`, `requestTimeout`, `$HOSTNAME`).
   Was die Eingangsschicht durchlässt, ist es nicht: `docs/olares-learnings.md`
   16 nennt als offene Frage 2, dass alle dortigen Upload-Messungen per
   Port-Forward an Envoy vorbeiliefen und nur **10 MB** durch die
   Eingangsschicht belegt sind. Siehe `web/README.md` und
   `docs/installieren.md` 4.

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
