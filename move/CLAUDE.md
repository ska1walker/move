# CLAUDE.md — move

## Quellenlage

Zwei Referenzdokumente, unterschiedlich belastbar:

- **`docs/olares-learnings.md`** — auf einer echten Box gemessen, Belege mit
  wörtlichen Fehlermeldungen. **Bei Widerspruch gewinnt dieses Dokument.**
- **`AGENTS.md`** — AImighty-Markt-Playbook (Marcs Umgebung). Gut für
  Market-Source-Mechanik und Katalogstörungen, aber Konvention statt Messung.

Grundregel von dort, die hier gilt: **Eine Beschreibung ist kein Beleg.** Nicht
dem Kommentar glauben, sondern die ausführende Datei lesen und den laufenden
Zustand abfragen. Eine grüne Kachel sagt nicht, welches Image läuft.

## Projekt

**move** — KI-Video-Plattform mit vorgefertigten cinematischen Presets und
gelernten Schnitt-Templates. Vorbild: Higgsfield. Native Olares-App auf der
eigenen Box, ausgeliefert über eine eigene Market Source.

Betreiber: kaivo.studio (Kai Böhm). Später auch als Kundenprodukt.

## Kernidee

Ein Schnitt-Template ist **keine KI**, sondern eine Liste von Zeitstempeln.
Templates werden aus Trailern statistisch extrahiert (Schnittzeitpunkte,
Pacing, Übergangsarten, Beat-Ausrichtung) und deterministisch mit ffmpeg
angewendet. Nur die einzelnen Clips sind generiert. Der Schnitt ist Arithmetik.

## Identität — vor dem ersten Release festlegen, danach unveränderlich

| Feld | Wert |
|---|---|
| Name (Ordner, `Chart.yaml.name`, `metadata.name`, `metadata.appid`) | `move` |
| `entrances[0].name` | `move` |
| `entrances[0].host` | `move` (= Service- und Frontend-Deployment-Name) |
| Titel | `Move` |
| Kategorie | `Utilities` |
| Erstversion | `26.9.1` |

Namensregex: `^[a-z0-9]{1,30}$`, **keine Bindestriche**. Gilt für den App-Namen,
nicht für Sub-Deployments.

**Nie umbenennen.** Die App-ID ist `md5(move)[:8]` und steckt in der URL.
Umbenennen = Neuinstallation mit neuem Namespace, neuer DB, neuer Adresse.

### Version

**Strikte SemVer ohne führende Null im Monatsfeld**: `26.9.1`, `26.10.1` —
niemals `26.09.1`. Aus so einer Version kommt man nur per Deinstallation
heraus. Gleich an vier Stellen: `Chart.yaml` `version` + `appVersion`,
Manifest `metadata.version` + `spec.versionName` (Beleg: HTTP 400 „must same").
Für die Market Source zusätzlich der Chart-Tabellen-Schlüssel
`move-26.9.1.tgz`.

**Jede Änderung braucht eine neue Version**, auch nur eine Beschreibung — der
Katalog-Hash entsteht aus `ID:name:version`.

Die erste Zeile von `upgradeDescription` nennt die ausgelieferte Version.

## Scope v0

Die Pipeline muss **ohne einen einzigen KI-Aufruf** durchlaufen:

1. MP4-Upload (kein YouTube-Downloader im Code)
2. Shot-Boundary-Erkennung -> Schnittzeitpunkte
3. Beat-Erkennung -> Beat-Grid
4. Ablage als `CutTemplate` (JSON)
5. Assembler: `CutTemplate` + N Clips -> fertiges MP4
6. Die N Clips sind **Platzhalter** (ffmpeg `lavfi`, Fläche mit Index und
   Timecode)

**Baureihenfolge: 5 und 6 zuerst**, mit handgeschriebenem `CutTemplate`.
Danach der Upload-Pfad (s. u., das ist der eigentliche Brocken), erst dann
TransNetV2.

### Nicht in v0

Keine Video-Generierung, kein LoRA-Training, kein Credit-System, kein
Multi-Tenant, keine eigene Anmeldung. Aufgaben außerhalb: nachfragen, nicht
bauen.

## Architektur

Ein Chart, **ein Entrance**, zwei Deployments:

```
move          Next.js 15 standalone, Port 3000
                  Entrance, UI, JSON-API, Job-Anlage, Upload-Annahme

moveworker    Python 3.11, kein Entrance, kein Service
                  Pollt die Job-Tabelle, macht Extraktion + ffmpeg
```

**Nur das Frontend bekommt einen Entrance.** Der Envoy-Sidecar fängt *jeden*
eingehenden TCP-Verkehr ab, auch clusterinternen — ein Aufruf vom eigenen Pod
auf einen Entrance-Service endet in 401 `ext_authz_denied`. Der Worker
kommuniziert deshalb gar nicht per HTTP, sondern ausschließlich über die
Job-Tabelle. Das ist der Grund für das Polling-Design, nicht Bequemlichkeit.

`spec.runAsInternal` **nicht setzen** — das ist ein Studio-Merkmal und die
dokumentierte Ursache für `Init:CrashLoopBackOff` mit „services aren't ready
in 20s". Die Init-Container (`check-auth`, `render-envoy-config`,
`olares-sidecar-init`) kommen automatisch und werden **nicht** überschrieben.

### Knotenzahl — vor dem ersten Chart klären

Die beiden Referenzdokumente widersprechen sich: AGENTS.md nennt zwei Nodes
mit je einer RTX 5090, die Learnings eine Einzelknoten-Box.

```bash
kubectl get nodes
olares-cli settings compute list
```

Bei einem Knoten entfällt der `nodeSelector` und der Worker konkurriert direkt
mit den LLM-Apps um die GPU. Dann gilt: Queue mit Concurrency 1, und vor
Installation prüfen, dass die GPU frei ist (`compute resource is not enough`
sonst).

## Der Upload-Pfad — die teuerste Stelle des Projekts

Move nimmt Videodateien entgegen. Alle vier folgenden Punkte sind
gemessene Fehlerquellen, nicht Vorsichtsmaßnahmen:

1. **Next-Middleware klont den Request-Body.** Passt die Middleware auf einen
   Pfad, beendet Next 15.5 bei `middlewareClientMaxBodySize` (Vorgabe 10 MB)
   **beide** Ströme — auch den weitergereichten. Das Anheben der Grenze half
   nicht. **Fix: den Upload-Pfad aus dem Matcher nehmen** und in einem eigenen
   Route Handler streamen. Kopfzeilen (Geheimnis, Identität) aus derselben
   Funktion wie die Middleware ziehen, nicht duplizieren.

2. **Ausgehend nie mit `fetch` streamen.** Gemessen bei 500 MB:
   `fetch`/undici +457–526 MB RSS gegenüber +57–85 MB mit
   `Readable.fromWeb(request.body)` -> `stream/promises.pipeline` ->
   `http.request`. Auf der Box: Spitze 110 MB statt 798 MB.
   `http.request` hat kein eigenes Zeitlimit — `setTimeout` setzen und einen
   **dauerhaften** `error`-Listener anhängen (ein zweiter Socket-Fehler ohne
   Listener beendet den Prozess). Hop-by-Hop-Köpfe (`Expect` u. a.) vor dem
   Weiterreichen entfernen.

3. **Node kappt nach `server.requestTimeout` = 300 s.** Next setzt keinen
   eigenen Wert. Beleg: Upload mit 30 kB/s -> HTTP 408 nach 329 s. Fix: Start
   als `node --require ./server-zeitlimit.cjs server.js`, darin
   `http.createServer` umhüllen und `requestTimeout` auf z. B. 2 h setzen.

4. **`NEXT_PUBLIC_*` und `rewrites()`-Ziele werden beim Build eingebrannt.**
   Im Dockerfile `NEXT_PUBLIC_API_URL` leer lassen. Das Standalone-Image
   lauscht nur auf `$HOSTNAME` — auf `0.0.0.0` setzen.

Zusätzlich für die Oberfläche: Upload-Fortschritt gibt es nur per
`XMLHttpRequest`, nicht per fetch.

## Datenhaltung v0

**SQLite auf `/app/data`**, WAL-Modus. Begründung: hostPath brauchen wir für
Uploads, Zwischenclips und Renderergebnisse ohnehin (MinIO ist praktisch nicht
nutzbar — `tapr-s3-svc` ist per NetworkPolicy gesperrt), und `/app/data`
überlebt die Deinstallation, während die Olares-Postgres-DB dabei gelöscht
wird.

Preis dafür:

- **hostPath erzwingt `strategy: Recreate`** (400 „can not enable rolling
  update with hostpath"). Ein kaputtes Update ist echte Downtime.
- **hostPath gehört root** und überdeckt das `chown` aus dem Dockerfile.
  `fsGroup` greift nicht. Fix: Init-Container mit `runAsUser: 0`, der nur den
  **eigenen** Unterordner chownt; Hauptcontainer `runAsUser: 1000`.
- Root-Container nur aus beclab-Images:
  `docker.io/beclab/aboveos-busybox:1.37.0` (sonst 400 „non-beclab image …
  root-equivalent").
- `options.runAsUser` als **String** `"1000"`, nicht als Bool.
- **`.Values.userspace.appData` ist der Host-Pfad**, nicht der Pfad im
  Container. Im Code immer `/app/data` verwenden.

Datenzugriff hinter einem Repository-Modul, damit der Wechsel auf die
Olares-Postgres (`middleware:`-Block, `.Values.postgres.*`) in v1 eine Datei
betrifft.

Queue: `BEGIN IMMEDIATE` + `status='queued' ORDER BY created_at LIMIT 1`.
Bei einem Worker-Pod ausreichend.

## Datenmodell

```
cut_template
  id, name, source_label, duration_ms
  cuts          json   [{ at_ms, transition, shot_scale }]
  beat_grid     json   { bpm, offsets_ms[] }
  shot_count    int
  created_at

render_job
  id, template_id, status, error
  clips         json   [{ index, source, uri }]
  output_uri
  created_at, started_at, finished_at
```

`status`: queued | running | done | failed

Zeitstempel **immer Millisekunden als Integer**. Keine Float-Sekunden.

## Manifest (v3)

```yaml
olaresManifest.version: '0.12.0'
apiVersion: 'v3'
workloadReplicas: ...          # Pflicht, oberste Ebene
options:
  apiTimeout: 0                # sonst kappt Envoy nach 15 s
  runAsUser: "1000"            # String!
  allowedOutboundPorts: [443]  # Modellgewichte laden
  dependencies:
    - name: olares
      version: '>=1.12.6-0'    # unter v3 exakt so
```

- Pflichtfelder: `name`, `appid`, `title`, `version`, `icon`, `requiredDisk`,
  `supportArch`. `chart lint` verlangt kein `appid`, `market upload` lehnt
  ohne ab.
- `openMethod: window` am Entrance, sonst zeigt die Kachel „running" und ein
  Klick öffnet nichts.
- **Zwei identische Manifeste**: im Chart und im Repo-Root. Ein CI-Guard
  vergleicht sie, sonst driftet die Kopie still.
- **Ressourcen**: entweder flach oder `spec.resources[]`, nie beides. Die
  Summe **aller** Container einschließlich Init-Container muss in
  `required*`/`limited*` passen. ffmpeg und TransNetV2 großzügig ansetzen.
- Die Zeichenkette `OLARES_USER_` darf in keiner Chart-Datei vorkommen, auch
  nicht im Kommentar — der Prüfer durchsucht den rohen Dateitext.

## Chart-Regeln

- `metadata.name` literal, nie `{{ .Release.Name }}`. Label `app: move`,
  sonst fehlen die Service-Endpoints.
- **Replikas nie fest ins Template.** app-service rendert mit 0 und skaliert
  hoch. Bei Bindestrich-Namen erreicht die Punktsyntax nicht:
  `replicas: {{ (index .Values.workloads "moveworker").replicaCount }}`
  plus Default in `values.yaml`.
- **Kein `.Files.Get/Glob/AsConfig`** — Olares' Renderer kennt `.Files` nicht
  (500 `can't evaluate field Files`). SQL und Konfiguration per
  Generator-Skript als Literal-Block in eine ConfigMap schreiben, im CI gegen
  die Quelle prüfen.
- **Keine Helm-Hooks, die Postgres oder Redis brauchen.** Das Namespace-Label
  `ns-owner` kommt erst *nach* erfolgreichem `helm install`; bis dahin sperrt
  die NetworkPolicy die Middleware. Migrationen als **Init-Container mit
  Wiederholschleife**.
- **`image:` als ein gequoteter String**, nie als Mapping.
- Nur `ClusterIP`. Kein PodDisruptionBudget bei `replicas: 1`.
- **Den gepackten Tarball linten**, nicht den Ordner:
  `olares-cli chart lint dist/move-26.9.1.tgz --with-rbac --with-security-context`
- Nur einmal gzippen, base64 direkt aus `helm package`.
- **`values.yaml` ist öffentlich.** Keine Schlüssel, keine Default-Secrets.

## Upgrades — die folgenreichste Regel der Plattform

**Ein Markt-Upgrade spielt die bei der *Erstinstallation* gespeicherten Werte
zurück** und übernimmt die Vorgaben des neuen Charts nicht. Chart-Metadaten
(`.Chart.AppVersion`) kommen dagegen frisch an.

Belegt mit Erfolgsmeldung: Kachel und `helm history` meldeten die neue
Version, die Pods liefen mit den alten Images.

- **Image-Tag immer aus `.Chart.AppVersion`:**
  `image: "ghcr.io/…/move:{{ .Values.images.web.tag | default .Chart.AppVersion }}"`
  mit `tag: ""` in `values.yaml`. Ein CI-Guard verbietet einen Pin.
- **Neue Werteschlüssel nie direkt dereferenzieren** — auf
  Bestandsinstallationen fehlen sie (`nil pointer`). Immer
  `{{ (default (dict) .Values.neu).feld | default "" }}` bzw.
  `(.Values.neu).feld`. `dig` funktioniert nicht.
- **Boolesche neue Schlüssel mit `hasKey` lesen** — `| default true` macht aus
  einem gesetzten `false` wieder `true`.
- **Neue Berechtigungen sind auch neue Werteschlüssel.** Mount, Env und
  `mkdir` bedingt: `{{- if (.Values.userspace).appCommon }} … {{- end }}`.
- `helm template -f values.yaml` mischt die Defaults ein und **kann diesen
  Fehler nicht zeigen**. Zusätzlich gegen eine Werte-Variante *ohne* die neuen
  Schlüssel rendern.
- **Nach jedem Ausrollen messen, was läuft**, nicht der Helm-Meldung glauben:
  ```bash
  kubectl get pods -n move-<nutzer> \
    -o custom-columns='N:.metadata.name,I:.spec.containers[*].image,R:.status.containerStatuses[0].ready'
  ```

## Adressen

Die URL ist `https://<md5(move)[:8]><index>.<nutzer>.<zone>`, **nicht**
`move.<nutzer>.<zone>` (das gibt es nur für Systemapps, sonst 421). Im
Chart `.Values.domain.move` verwenden, live mit
`olares-cli settings apps list` nachsehen, nie selbst ausrechnen.

Merkbarer Name nachträglich, nicht im Chart:
`olares-cli settings apps domain set move move --third-level move`
Eine Deinstallation löscht ihn.

**Ein zweiter Entrance ändert die Adresse des ersten.** Deshalb bleibt es bei
genau einem.

## Frontend-Design

Verbindlich: `docs/design-guide.md` (AImighty-Standard). Hanseatenblau
`#051729` + Gold `#caa960`, keine dritte Farbfamilie. Geist Sans und Geist Mono
selbst gehostet. Kein Verlauf, kein Schatten. Keine festen Pixelwerte,
`--am-*`-Maße auf Grundeinheit 4 px. Genau eine primäre Handlung je Ansicht.
Kein Nachladen von fremden Servern. WCAG 2.2 AA.

**Entschieden:** AImighty-Farbwelt, auch für eine spätere Kundenversion. Kein
Signal-Magenta, keine kaivo-Tokens.

Zwei Konsequenzen fürs Repo:

- `docs/design-guide.md` liegt bisher im Markt-Repo. Kopie nach
  `docs/design-guide.md` in diesem Repo, sonst kann Claude Code die
  verbindliche Vorgabe nicht lesen.
- **Geist Sans und Geist Mono selbst hosten.** Kein Nachladen von fremden
  Servern heißt: Schriftdateien liegen im Repo und im Image, `@font-face` zeigt
  auf den eigenen Pfad. Kein Google-Fonts-Import, auch nicht im Dev.

## Harte Regeln

- **Keine Emojis** in Code, Kommentaren, Commit-Messages, UI-Texten oder
  Dokumentation. Icons als Vektor oder gar nichts.
- **ffmpeg-Aufrufe als Argument-Arrays**, nie als Shell-String.
- **Keine Downloader im Repo.** Quellvideos lädt der Nutzer hoch. Bewusste
  rechtliche Entscheidung, keine fehlende Funktion.
- **Still geschluckte Fehler verbergen Plattformfehler.** Mindestens mit
  Metadaten loggen. Zwei Fehler liegen oft übereinander.
- Testvideos unter 60 Sekunden.
- Secrets nie im Repo. Gerenderte Dateien mit Secrets nach der Prüfung löschen.

## Reihenfolge, die nicht verhandelbar ist

Images bauen -> auf der eigenen Box installieren und `running` **messen** ->
erst dann in den Katalog. Eine App, die nie `running` erreicht hat, gehört in
keinen Markt.

## Nächste Schritte

1. Knotenzahl und GPU-Belegung messen (s. o.), erst dann das Chart entwerfen
2. Repo anlegen, Chart-Gerüst, beide Manifeste, `icon.png` 512x512 öffentlich
3. `check-chart.sh` als Guard, bevor der erste Fix geschrieben wird
4. Assembler: handgeschriebenes `CutTemplate` + Platzhalter -> MP4
5. Upload-Pfad mit allen vier Fallstricken, gegen eine 400-MB-Datei gemessen
6. Web: Template-Liste, Job anlegen, Ergebnis abspielen
7. Release `26.9.1`, Install, Route-ID `move`
8. TransNetV2 + librosa
9. Erst danach: echte Clip-Generierung
