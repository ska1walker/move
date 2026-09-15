# moveworker

Der Teil von move, der rechnet: `CutTemplate` + N Clips -> fertiges MP4.

Kein Modell, keine Inferenz. Nur Standardbibliothek und ffmpeg.

## Laufen lassen

```bash
cd worker

# Was das Template bei einer Bildrate ergibt, ohne zu rendern
python3 -m move_worker show --template examples/beat-8s.json --fps 25

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

## Noch nicht da

Job-Tabelle, Polling-Schleife, SQLite-Repository, Shot-Boundary-Erkennung,
Beat-Erkennung. v0 faehrt bis hierher ueber die Kommandozeile.
