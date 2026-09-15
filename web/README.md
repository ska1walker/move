# move (Frontend)

Next.js 15.5 standalone. Entrance, Oberfläche, JSON-API, Job-Anlage,
Upload-Annahme.

## Laufen lassen

```bash
cd web
npm ci
npm run dev            # Entwicklung
npm run typecheck
npm run build          # standalone-Bundle nach .next/standalone
```

Für einen Lauf ohne Container muss das standalone-Bundle zusammengesetzt
werden — Next kopiert `public/` und `.next/static` nicht selbst:

```bash
cp -r .next/standalone /tmp/moverun
cp -r public /tmp/moverun/public
cp -r .next/static /tmp/moverun/.next/static
cp server-zeitlimit.cjs /tmp/moverun/
mkdir -p /tmp/moverun/db && cp ../db/schema.sql /tmp/moverun/db/

cd /tmp/moverun
MOVE_DATA_DIR=/tmp/movewebdata HOSTNAME=0.0.0.0 PORT=3000 \
  node --require ./server-zeitlimit.cjs server.js
```

**Nicht `node server.js`.** Ohne das `--require` gilt Nodes Vorgabe von
300 Sekunden und ein langsamer Upload endet in HTTP 408.

## Der Upload-Pfad

Die teuerste Stelle des Projekts. Vier Fallstricke, alle gemessen.

### 1. Die Middleware schneidet still ab

**Das ist der wichtigste Befund und er ist schlimmer als erwartet.** Liegt
`/api/uploads` im Matcher der Middleware, kappt Next 15.5.25 den Strom bei
rund 10 MB — und meldet **HTTP 200**. Die Pipeline sieht ein saubares
Stromende, kein Fehler, nichts im Log.

Gemessen mit Next 15.5.25:

| Matcher | hochgeladen | HTTP | auf der Platte |
|---|---|---|---|
| `/api/uploads` **drin** | 20.000.000 B | **200** | **10.449.920 B** |
| `/api/uploads` **drin** | 5.000.000 B | 200 | 5.000.000 B |
| `/api/uploads` **draußen** | 20.000.000 B | 200 | 20.000.000 B |
| `/api/uploads` **draußen** | 419.438.592 B | 200 | 419.438.592 B |

Zwei Maßnahmen, weil eine nicht reicht:

- Der Matcher in `src/middleware.ts` nimmt `/api/uploads` aus. Die Identität
  zieht der Route Handler aus **derselben** Funktion (`lib/identitaet.ts`),
  die die Middleware benutzt — nicht aus einer zweiten Kopie.
- Der Route Handler hält die angekündigte `Content-Length` gegen die
  geschriebenen Bytes und scheitert mit HTTP 400, wenn sie abweichen. Damit
  fällt ein Abschneiden auf, egal wodurch es entsteht.

Der zweite Riegel ist geprüft: mit wieder eingebautem Matcher kommt statt
einer stillen 10-MB-Datei

```
HTTP 400  Unvollstaendig: angekuendigt waren 20000000 Bytes,
          angekommen sind 10449920.
```

und das Upload-Verzeichnis bleibt leer — die `.teil`-Datei wird aufgeräumt.

### 2. Nicht puffern

`request.formData()` und `request.arrayBuffer()` ziehen die ganze Datei in
den Speicher. Stattdessen `Readable.fromWeb(request.body)` →
`stream/promises.pipeline` → `createWriteStream`.

Gemessen bei 400 MB: **RSS 104–129 MB**, prozessintern über
`process.memoryUsage().rss`. Die Learnings nennen auf der Box eine Spitze von
110 MB statt 798 MB — das deckt sich.

Kein `multipart/form-data`: ein Parser dafür puffert oder bringt eine
Abhängigkeit mit. Der Browser schickt die Datei als rohen Körper
(`xhr.send(datei)`), der Name kommt als Abfrageparameter.

v0 reicht nichts weiter, die Datei landet direkt auf der Platte — die Regel
„ausgehend nie mit `fetch` streamen" ist damit gar nicht anwendbar. Die Liste
der Hop-by-hop-Kopfzeilen steht trotzdem in `lib/identitaet.ts`, weil sie beim
ersten Weiterreichen gern vergessen wird.

### 3. Node kappt nach 300 Sekunden

`server.requestTimeout`, Vorgabe 300 s. Next setzt keinen eigenen Wert und
gibt keinen Weg, ihn zu setzen — das standalone-Bundle ruft
`http.createServer` selbst auf. `server-zeitlimit.cjs` umhüllt den Aufruf
und setzt 2 Stunden.

Geprüft, dass der Schalter wirklich greift: mit
`MOVE_REQUEST_TIMEOUT_MS=3000` und einem Upload bei 2 kB/s kam **HTTP 408**.
Mit der Vorgabe steht beim Start im Log
`[server-zeitlimit] requestTimeout=7200000ms`, und die CI prüft genau diese
Zeile.

### 4. Keine eingebrannten Adressen

`NEXT_PUBLIC_*` und die Ziele von `rewrites()` werden beim Build
festgeschrieben. Es gibt beides nicht: der Browser spricht alles relativ an.
Das Dockerfile setzt kein `NEXT_PUBLIC_*`, und die CI prüft, dass keines im
Image steht.

Das standalone-Image lauscht nur auf `$HOSTNAME` — im Dockerfile auf `0.0.0.0`
gesetzt, ebenfalls von der CI geprüft.

### Fortschritt nur über XMLHttpRequest

`fetch` meldet beim Hochladen keinen Fortschritt, sondern erst das Ende. Bei
400 MB wären das minutenlang null Rückmeldung. `JobFormular.tsx` benutzt
deshalb `XMLHttpRequest` mit `upload.progress`.

## Weitere Prüfungen des Upload-Pfads

| Eingabe | Antwort |
|---|---|
| Datei ohne `ftyp` an Byte 4 | 415, im Strom erkannt |
| Endung nicht `.mp4` | 415, vor dem ersten Byte |
| `name=../../etc/passwd.mp4` | 200, Name auf `passwd.mp4` entschärft; gespeichert wird unter einer UUID |
| größer als `MOVE_MAX_UPLOAD_BYTES` | 413, angekündigte Größe vorab, sonst im Strom |

## Kein Aufruf der eigenen API

Die Server-Komponente in `app/page.tsx` liest **direkt** aus
`lib/daten.ts` und ruft nicht die eigene JSON-API über HTTP. Der
Envoy-Sidecar fängt jeden eingehenden TCP-Verkehr ab, auch den vom eigenen
Pod: ein `fetch` auf die eigene Entrance-Adresse endet in
401 `ext_authz_denied`.

## Datenhaltung

`node:sqlite` — ein Node-Builtin, keine native Abhängigkeit, kein
Build-Schritt. Als experimentell markiert, deshalb steht es hinter
`lib/daten.ts` und nirgends sonst.

Das Schema liegt in `../db/schema.sql` und wird von Web **und** Worker
gelesen. Zwei DDLs in zwei Sprachen driften still auseinander; die CI
vergleicht die Kopie in jedem Image gegen die Datei im Repo.

Das Web reiht Jobs ein und liest ihren Stand. Gezogen wird allein vom Worker,
mit `BEGIN IMMEDIATE`.

## Oberfläche

Verbindlich ist `docs/design-guide.md` (AImighty-Standard): Hanseatenblau
`#051729` + Gold `#caa960`, keine dritte Farbfamilie, kein Verlauf, kein
Schatten, keine festen Pixelwerte (`--am-*` auf Grundeinheit 4 px, in rem),
genau eine primäre Handlung je Ansicht, kein Nachladen von fremden Servern.

Geist Sans und Geist Mono liegen als `woff2` unter `public/fonts/` im Repo und
im Image, mit der OFL-Lizenz daneben. Kein Google-Fonts-Import, auch nicht im
Entwicklungsmodus.

Der Zustand eines Jobs steht als Wort da, nicht als Farbfleck — Farbe allein
trägt keine Information (WCAG 1.4.1).

## Clips: drei Wege

Das Formular bietet, woher die Clips kommen sollen:

| Wahl | was passiert |
|---|---|
| Platzhalter | der Worker erzeugt Flächen mit Index und Timecode. Kostet nichts. |
| Eigene Dateien | genau eine MP4 je Einstellung, über den Upload-Pfad |
| Von fal.ai erzeugen | eine Beschreibung je Einstellung |

Beim dritten Weg steht zu jeder Einstellung ihre Dauer und die Bildgröße aus
dem Template daneben — beides beeinflusst, was man sinnvollerweise
beschreibt. Die Bildgröße hängt der Worker an den Prompt an.

**Die Kosten stehen vor dem Knopf, nicht in der Rechnung:** „N Aufrufe bei
fal.ai, jeder wird über deinen Schlüssel abgerechnet." Der Knopf bleibt
gesperrt, solange eine Beschreibung fehlt, und die API weist einen `fal`-Clip
ohne `prompt` mit HTTP 400 ab — bevor irgendetwas bezahlt wird.

Der Schlüssel ist der **des Nutzers**, nicht des Betreibers. Auf Olares hat
jede Installation ihren eigenen Namespace; eine Installation ist ein Kunde,
und `olaresEnv` heißt, dass er seinen Schlüssel bei der Installation einträgt.

## Noch nicht da

Template-Anlage im Web. Templates entstehen in v0 über
`move_worker enqueue`, ab Schritt 8 aus der Extraktion. Ein POST hätte die
vollständige Prüfung der Schnittliste ein zweites Mal gebraucht, in einer
zweiten Sprache — die steht in `worker/move_worker/templates.py` und soll dort
allein stehen.
