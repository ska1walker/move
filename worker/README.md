# moveworker

Der Teil von move, der rechnet: `CutTemplate` + N Clips -> fertiges MP4.

Kein Modell, keine Inferenz. Nur Standardbibliothek und ffmpeg.

## Der Container

```bash
# Baukontext ist das Repo-Wurzelverzeichnis, nicht worker/ --
# db/schema.sql liegt ausserhalb und wird von Web und Worker gelesen.
docker build -f worker/Dockerfile -t moveworker:26.9.4 .

docker run --rm -v /pfad/zu/appdata:/app/data moveworker:26.9.4 \
  enqueue --template /app/examples/beat-8s.json
docker run --rm -v /pfad/zu/appdata:/app/data moveworker:26.9.4
```

Der Standardbefehl ist `work`: die Polling-Schleife auf der Job-Tabelle. Der
Worker spricht **nie HTTP**. Der Envoy-Sidecar faengt jeden eingehenden
TCP-Verkehr ab, auch clusterinternen -- ein Aufruf vom eigenen Pod auf einen
Entrance-Service endet in 401. Die Job-Tabelle ist deshalb der einzige Weg
zwischen Web und Worker, und das Polling ist die Folge, nicht Bequemlichkeit.

`SIGTERM` beendet die Schleife nach dem laufenden Job. hostPath erzwingt
`strategy: Recreate`, bei einem Update wird der Pod also beendet und neu
gestartet; beim Start holt `reset_stale_running` alles zurueck, was auf
`running` haengengeblieben ist.

## Laufen lassen

```bash
cd worker

# Was das Template bei einer Bildrate ergibt, ohne zu rendern
python3 -m move_worker show --template examples/beat-8s.json --fps 25

# Job einreihen, abarbeiten, nachsehen
MOVE_DATA_DIR=/tmp/movedata python3 -m move_worker enqueue --template examples/beat-8s.json
MOVE_DATA_DIR=/tmp/movedata python3 -m move_worker work --max-jobs 1
MOVE_DATA_DIR=/tmp/movedata python3 -m move_worker jobs

# Platzhalter erzeugen und in einem Lauf rendern
python3 -m move_worker demo --template examples/beat-8s.json --out-dir /tmp/move

# Beides einzeln
python3 -m move_worker placeholders --template examples/beat-8s.json --out-dir /tmp/move/clips
python3 -m move_worker render --template examples/beat-8s.json --out /tmp/move/out.mp4 \
  --clip /tmp/move/clips/clip-00.mp4 --clip ...

# Tests
python3 -m unittest discover -s tests -t .
```

Die Ende-zu-Ende-Tests werden ohne `ffmpeg` und `ffprobe` im PATH
uebersprungen, nicht rot.

## Was das Image mitbringen muss

- **ffmpeg mit `drawtext`** (also mit libfreetype). Schlanke Builds haben den
  Filter nicht; `ffmpeg.require_filter` scheitert dann mit Klartext, statt
  Platzhalter ohne Index zu schreiben.
- **eine Schriftdatei.** Gesucht wird in `MOVE_FONT_FILE`, danach in den
  ueblichen DejaVu-, Liberation- und FreeFont-Pfaden.
- `ffprobe` fuer die Laengenmessung.

`MOVE_FFMPEG` und `MOVE_FFPROBE` koennen auf abweichende Pfade zeigen.

## Die Rechnung

Das Template haelt **Millisekunden**, weil es unabhaengig von Aufloesung und
Bildrate gilt. Geschnitten werden kann aber nur auf Bildgrenzen: bei 25 fps
sind 500 ms genau 12,5 Bilder.

Deshalb wird aus `(Template, fps)` zuerst ein `FramePlan` in ganzen Bildern
gerechnet. Die sichtbaren Laengen entstehen als **Differenz der gerundeten
Startbilder**, nicht durch Rundung der Laengen selbst -- so kann sich die
Rundung nicht aufsummieren. Aus acht Einstellungen von 8000 ms werden bei
25 fps exakt 200 Bilder, auch wenn einzelne 500-ms-Einstellungen mal 12 und
mal 13 Bilder bekommen.

Wer ffmpeg stattdessen Sekunden uebergibt, laesst ffmpeg diese Rundung still
erledigen. Gemessen: ein Platzhalter von 520 ms, wo 500 ms verlangt waren.

Bei einer Blende sind zwei Einstellungen gleichzeitig zu sehen, die vorherige
muss also laenger geschnitten werden als sie allein zu sehen ist:

```
source[i] = visible[i] + fade[i+1]
```

Nach Einstellung i ist der Strom `start[i] + source[i]` Bilder lang, also
genau `start[i+1] + fade[i+1]` -- exakt so lang, wie `xfade` mit
`offset = start[i+1]` und `duration = fade[i+1]` braucht. Ein harter Schnitt
ist keine Blende der Laenge 0, die kennt xfade nicht, sondern ein `concat`.

## Festlegung im Datenmodell

`cuts[i]` beschreibt **die i-te Einstellung**, nicht die Grenze davor:
`at_ms` ist ihr Beginn auf der fertigen Zeitachse, `transition` wie in sie
hineingeschnitten wird, `shot_scale` ihre Bildgroesse. `cuts[0].at_ms` ist
immer 0.

`transition_ms` steht so nicht im Datenmodell in CLAUDE.md. Ohne Laenge laesst
sich eine Blende nicht anwenden, und die Laenge ist Teil dessen, was aus einem
Trailer gelernt wird -- keine Einstellung des Renderers. Das Feld ist optional
und faellt auf 0 zurueck, aeltere Templates bleiben lesbar.

## Gemessene Fallen

| Falle | Fix |
|---|---|
| `xfade`: "First input link main timebase (1/1000000) do not match" | `concat` setzt die Zeitbasis um. Nach jedem Schritt `settb=1/<fps>` |
| Platzhalter wird 520 statt 500 ms lang | Nicht `color=...:d=<Sekunden>`, sondern `-frames:v <Bilder>` |
| `drawtext` zeichnet nichts, ffmpeg endet mit 0 | stderr auf `Parsed_drawtext` pruefen -- der Fehler ist nicht fatal |
| `drawtext=timecode=...` scheitert mit "Both text and text file provided" | In ffmpeg 6.1.1 kaputt, auch ohne `text`. Stattdessen `text=%{pts\\:hms}`, das zeigt ohnehin Millisekunden |
| Filtergraph verschluckt einen Doppelpunkt | Zwei Entpack-Durchgaenge: `:` braucht **zwei** Backslashes, `,` nur einen |
| Harter Schnitt liegt ein Bild zu spaet, Laufzeit und Bildzahl stimmen trotzdem | `concat` rechnet den Versatz aus den PTS des ersten Stroms. Nach `xfade` liegen die in ffmpeg 5.1 auf einem Mikrosekunden-Raster, das aufrundet. `settb=1/fps,setpts=N` nach jedem Schritt |

## Was von der ffmpeg-Version abhaengt

Gemessen mit `tools/schnittgrenzen.py` im gebauten Image (ffmpeg 5.1,
Debian bookworm) gegen ffmpeg 6.1.1:

- **Harte Schnitte liegen exakt auf dem geplanten Bild**, in beiden Versionen.
  Daran haengt die Zusage, dass der Schnitt Arithmetik ist.
- **Das Ende einer Blende kann um ein Bild abweichen.** 5.1 laesst die Rampe
  ein Bild frueher auslaufen als 6.1.1; der Versatz stimmt in beiden, weil
  `xfade` ihn in Sekunden vorgegeben bekommt. Das Messwerkzeug erlaubt dort
  ein Bild und schreibt die Toleranz in die Ausgabe.
- **Byteweise Reproduzierbarkeit gilt je Umgebung**, nicht ueber
  ffmpeg-Versionen hinweg.

## Datenhaltung

SQLite auf `/app/data/db/move.sqlite3`, WAL-Modus. Alle Abfragen stehen in
`repository.py` und nirgends sonst -- der Wechsel auf die Olares-Postgres in
v1 soll genau eine Datei betreffen.

Die DDL steht in `../db/schema.sql` und wird von Web **und** Worker gelesen.
Zwei Schemata in zwei Sprachen driften still auseinander: ein fehlender CHECK
nimmt kaputte Daten an, ein fehlender Index macht nur langsam. Beide Images
kopieren die Datei, die CI vergleicht sie gegen das Repo.

Nicht unter `db/migrations/`: jener Ordner ist fuer die Postgres-Migrationen
gedacht, die ueber eine ConfigMap ins Chart wandern. Fuer SQLite waere das ein
Umweg ueber den Cluster.

Die Warteschlange zieht mit `BEGIN IMMEDIATE` und
`status='queued' ORDER BY created_at LIMIT 1`. `BEGIN IMMEDIATE` nimmt die
Schreibsperre sofort statt erst beim `UPDATE` -- sonst koennten zwei Worker
dieselbe Zeile lesen und beide denselben Job rendern.

`output_uri` ist **relativ zum Datenverzeichnis**. Absolut waere der Pfad im
Container richtig und auf dem Host falsch: `.Values.userspace.appData` ist der
Host-Pfad, `/app/data` der im Container.

## Extraktion: aus einem Video ein Template

```bash
python3 -m move_worker extract --video trailer.mp4 --out template.json
python3 -m move_worker extract --video trailer.mp4 --save          # in die DB
python3 -m move_worker extract --video trailer.mp4 --snap-to-beat 60
```

### ffmpeg statt TransNetV2

CLAUDE.md nennt fuer Schritt 8 TransNetV2. Der Scope v0 verlangt aber, dass
die Pipeline **ohne einen einzigen KI-Aufruf** durchlaeuft -- und TransNetV2
ist ein Modell. ffmpegs `scdet` ist deterministisch, liegt schon im Image und
braucht keine Gewichte.

Gemessen an einem Video mit vier bekannten harten Schnitten (320x180, 25 fps,
Schnitte auf Bild 25, 63, 81, 126):

| | |
|---|---|
| gefundene Schnitte | 1000, 2520, 3240, 5040 ms -- genau die vier |
| Werte an den Schnitten | 25 bis 66 |
| Werte an allen anderen Bildern | um 0,01 |
| Rundlauf: Plan aus dem Template | 25, 38, 18, 45, 49 Bilder -- genau die Quelllaengen |
| Render daraus | 175 Bilder, 7,000000 s |

Der Abstand zwischen Schnitt und Nicht-Schnitt traegt eine robuste Schwelle;
die Vorgabe ist 10.

TransNetV2 bleibt der Ausbau, wenn die Qualitaet an echtem Material nicht
reicht. Dann kehrt allerdings auch die GPU-Frage zurueck, die das
CPU-only-Chart gerade gegenstandslos macht.

### Was NICHT erkannt wird

CLAUDE.md nennt als Bestandteile eines gelernten Templates auch
Uebergangsarten und Bildgroessen. `scdet` liefert beides nicht:

- **Uebergangsart**: erkannt werden Zeitpunkte, nicht ob dort hart
  geschnitten oder geblendet wurde. Jeder Punkt wird als harter Schnitt
  abgelegt. Eine Blende im Quellmaterial erzeugt einen flachen Anstieg statt
  einer Spitze und wird je nach Schwelle gar nicht oder an einer beliebigen
  Stelle darin gefunden.
- **Bildgroesse**: braucht Bildinhalt-Verstaendnis. Steht auf `medium`.

Beides steht als Vorgabe da und ist nicht geraten.

### Beat-Grid

librosa auf einer Tonspur, die ffmpeg vorher nach WAV holt -- so braucht
librosa kein audioread und damit keinen zweiten Weg zu ffmpeg.

Die BPM kommt aus dem **Mittel** der Beat-Abstaende, nicht aus librosas
Tempo-Ausgabe. Gemessen an einem Klick-Track mit exakt 120 bpm:

| Verfahren | Ergebnis |
|---|---|
| librosas `tempo` | 117,5 bpm |
| Median der Abstaende | 117,6 bpm |
| **Mittel der Abstaende** | **119,9 bpm** |

Der Median war keinen Deut besser: die Abstaende wechseln zwischen 511 und
487 ms, und der Median greift dann einen der beiden Werte statt der Mitte.
Ausreisser fallen vorher heraus (was zwischen der Haelfte und dem
Eineinhalbfachen des Medians liegt, bleibt) -- ein uebersehener Beat
verdoppelt sonst einen Abstand und verzieht das Mittel.

`--snap-to-beat MS` zieht jeden Schnitt auf den naechsten Beat, wenn er so
nah liegt. Standard ist 0: aus gemessenen Daten werden sonst veraenderte,
und das muss verlangt werden. Gemessen mit Toleranz 60 ms: 1000 -> 1022,
2520 -> 2531, 3240 bleibt (naechster Beat 221 ms entfernt), 5040 -> 5016.

### librosa ist optional

`extraktion.py` importiert librosa absichtlich spaet. `work`, `render`,
`demo` und der ganze Datenzugriff laufen ohne -- nur `extract` mit Tonspur
braucht es und sagt sonst, was fehlt. Die Testsuite ueberspringt die drei
Beat-Tests dann sauber.

Im Image ist librosa enthalten (`worker/requirements.txt`). Es zieht numpy,
scipy, numba und llvmlite mit; das Image waechst dadurch deutlich.

## Clip-Generierung bei fal.ai

**Ausserhalb des v0-Scope.** CLAUDE.md schliesst Video-Generierung aus; das
hier ist eine bewusste Erweiterung darueber hinaus. Der Assembler bleibt
unberuehrt -- der Schnitt ist weiterhin Arithmetik, generiert wird nur der
Inhalt der einzelnen Einstellungen.

Ein Clip mit `source: "fal"` und einem `prompt` wird erzeugt:

```json
{ "index": 0, "source": "fal", "prompt": "a wide desert at dawn", "model": "" }
```

`model` leer heisst: der Wert aus `MOVE_FAL_MODEL`, sonst der Standard in
`generierung.py`.

### Der Schluessel

`FAL_KEY` aus der Umgebung, so wie fal-client es selbst erwartet. Im Chart
kommt er ueber `(.Values.olaresEnv).FAL_KEY` dorthin und steht **an keiner
Stelle im Repo** -- `values.yaml` traegt nur `olaresEnv: {}`.

Er wird auch nirgends geloggt: `_ohne_geheimnis` raeumt ihn aus jeder Meldung,
bevor sie in `render_job.error` und damit in die Oberflaeche wandert. Ein
Schluessel, der einmal dort steht, steht dort dauerhaft. Ein Test prueft das.

### Drei Entscheidungen

**Gepollt, nicht zurueckgerufen.** fal kann das Ergebnis per Webhook
schicken. Fuer move nicht nutzbar: der Envoy-Sidecar faengt jeden eingehenden
TCP-Verkehr ab, ein Rueckruf von aussen endet in 401 `ext_authz_denied`.
`subscribe` pollt selbst. Dieselbe Einschraenkung, die schon das Polling auf
der Job-Tabelle erzwingt.

**Zwischengespeichert, sonst kostet ein Pod-Neustart doppelt.** hostPath
erzwingt `strategy: Recreate`; bei einem Update wird der Pod beendet, und
`reset_stale_running` legt den laufenden Job zurueck in die Warteschlange.
Ohne Zwischenspeicher wuerde derselbe Job danach alle Einstellungen neu
erzeugen -- und neu abgerechnet. Der Schluessel ist ein Hash ueber Modell,
Prompt, Laenge und Format; liegt die Datei unter `data/generated/<hash>.mp4`,
wird sie genommen. Ein Test prueft, dass ein zweiter Lauf fal NICHT erneut
ruft.

**Das Modell ist nicht fest verdrahtet.** Welche Video-Modelle es bei fal gibt
und wie ihre Ausgabe aussieht, konnte ich nicht nachsehen: fal.ai und
docs.fal.ai sind vom Egress-Proxy gesperrt. Die Antwort wird deshalb tolerant
nach einer URL durchsucht (`video`, `output`, `file`, `result`, `url`, auch
verschachtelt und in Listen). Findet sich keine, scheitert der Job **mit der
Antwort im Text** statt zu raten.

### Nur http und https

Die Antwort kommt von aussen. Ohne Schema-Pruefung wuerde eine URL wie
`file:///etc/passwd` dazu fuehren, dass der Worker eine lokale Datei liest und
als erzeugten Clip ablegt. Jeder Kandidat laeuft durch `ist_web_url`. Diese
Luecke hat ein Test gefunden, nicht der Autor.

### Was in die Anfrage geht

Der Prompt wird zusammengesetzt: `"<prompt>, <shot_scale> shot"`. Die
Bildgroesse steht im Template und ist Teil dessen, was es ueber den Schnitt
aussagt. Die Zusammensetzung steht im Log, damit sichtbar ist, was gefragt
wurde.

Die Laenge wird auf ganze Sekunden **aufgerundet**: Modelle nehmen meist
ganzzahlige Laengen, und ein zu kurzer Clip ist teuer -- der Assembler lehnt
ihn dann ab (`check_clips`, mit Zahlen), und die Generierung waere bezahlt und
unbrauchbar.

### Obergrenze, weil jeder Aufruf Geld kostet

fal ist eine Entwicklerplattform: Abrechnung pro Aufruf, keine
Endnutzerkonten. Ein Template mit acht Einstellungen ist damit ein Job mit
acht Rechnungspositionen.

`MOVE_FAL_MAX_CLIPS` begrenzt, wie viele Clips ein Job erzeugen darf --
Vorgabe 12. Geprueft wird **bevor der erste Aufruf laeuft**, nicht danach.
`0` schaltet die Generierung ganz ab, brauchbar fuer eine Installation, die
nur Platzhalter und Uploads zulassen soll. Ein Tippfehler in der Variablen
faellt nicht auf die Vorgabe zurueck, sondern scheitert -- eine offene Grenze
durch einen Vertipper waere teuer.

Das zaehlt fuer v0 wenig: du bist Betreiber und einziger Nutzer, dein
Schluessel, deine Rechnung. Fuer eine Kundenversion zaehlt es sehr: der Kunde
hat kein fal-Konto, der Schluessel bleibt der des Betreibers, und jede
Generierung landet auf dessen Abrechnung. Das ist genau das Credit-System,
das CLAUDE.md fuer v0 ausschliesst -- mit fal im Produkt wird aus dem
Aufschub ein Loch.

### Was hier nicht geprueft ist

Der Netzaufruf selbst. fal ist gesperrt und ein Schluessel liegt hier nicht.
Getestet ist alles andere -- Anfrage, Zwischenspeicher, Auslesen der Antwort,
Download, Fehlerwege -- gegen ein Doppel, das seine Aufrufe aufzeichnet.
**Der erste echte Aufruf gegen fal ist noch nicht gelaufen.**

## Noch nicht da

Der `envs:`-Block im Manifest, der Olares dazu bringt, den Schluessel bei der
Installation abzufragen. Ohne ihn bleibt `olaresEnv` leer und `FAL_KEY` muss
von Hand ins Deployment. Der Grund steht in der Antwort auf die Frage, wo der
Schluessel hingehoert: AGENTS.md und CLAUDE.md widersprechen sich an dieser
Stelle, und `check-chart.sh` setzt CLAUDE.md durch. Zu klaeren gegen ein
Live-Chart.

TransNetV2 als Ausbau der Schnitterkennung, Erkennung der Uebergangsarten und
Bildgroessen.
