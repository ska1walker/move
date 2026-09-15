# moveworker

Der Teil von move, der rechnet: `CutTemplate` + N Clips -> fertiges MP4.

Kein Modell, keine Inferenz. Nur Standardbibliothek und ffmpeg.

## Der Container

```bash
docker build -t moveworker:26.9.1 worker/
docker run --rm -v /pfad/zu/appdata:/app/data moveworker:26.9.1 \
  enqueue --template /app/examples/beat-8s.json
docker run --rm -v /pfad/zu/appdata:/app/data moveworker:26.9.1
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

SQLite auf `/app/data/db/move.sqlite3`, WAL-Modus. Alles SQL steht in
`repository.py` und nirgends sonst -- der Wechsel auf die Olares-Postgres in
v1 soll genau eine Datei betreffen.

Das Schema steht dort als Konstante und nicht unter `db/migrations/`: jener
Ordner ist fuer die Postgres-Migrationen gedacht, die ueber eine ConfigMap
ins Chart wandern. Fuer SQLite waere das ein Umweg ueber den Cluster.

Die Warteschlange zieht mit `BEGIN IMMEDIATE` und
`status='queued' ORDER BY created_at LIMIT 1`. `BEGIN IMMEDIATE` nimmt die
Schreibsperre sofort statt erst beim `UPDATE` -- sonst koennten zwei Worker
dieselbe Zeile lesen und beide denselben Job rendern.

`output_uri` ist **relativ zum Datenverzeichnis**. Absolut waere der Pfad im
Container richtig und auf dem Host falsch: `.Values.userspace.appData` ist der
Host-Pfad, `/app/data` der im Container.

## Noch nicht da

Shot-Boundary-Erkennung (TransNetV2), Beat-Erkennung (librosa), echte
Clip-Generierung. Die Clips sind Platzhalter, sobald `source` auf
`placeholder` steht.
