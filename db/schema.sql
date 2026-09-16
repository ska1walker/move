-- Schema fuer move v0. SQLite auf dem App-Datenverzeichnis.
--
-- EINZIGE QUELLE DER WAHRHEIT. Web (TypeScript) und Worker (Python) lesen
-- diese Datei, keiner von beiden traegt eine eigene Kopie. Zwei DDLs in zwei
-- Sprachen driften auseinander, und zwar still: ein fehlender CHECK nimmt
-- kaputte Daten an, ein fehlender Index macht nur langsam. Beide Images
-- kopieren die Datei, ein CI-Guard vergleicht sie.
--
-- Mehrfach ausfuehrbar. Web und Worker starten in beliebiger Reihenfolge,
-- wer zuerst da ist, legt an.
--
-- Zeitstempel sind ueberall ganze Millisekunden. Keine Float-Sekunden.

CREATE TABLE IF NOT EXISTS cut_template (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    source_label TEXT NOT NULL DEFAULT '',
    duration_ms  INTEGER NOT NULL,
    cuts         TEXT NOT NULL,
    beat_grid    TEXT,
    shot_count   INTEGER NOT NULL,
    created_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS render_job (
    id          TEXT PRIMARY KEY,
    template_id TEXT NOT NULL REFERENCES cut_template(id),
    status      TEXT NOT NULL CHECK (status IN ('queued','running','done','failed')),
    error       TEXT,
    clips       TEXT NOT NULL,
    output_uri  TEXT,
    created_at  INTEGER NOT NULL,
    started_at  INTEGER,
    finished_at INTEGER
);

-- Die Abfrage der Warteschlange laeuft bei jedem Durchlauf des Workers. Ohne
-- Index durchsucht SQLite die ganze Tabelle, auch wenn nichts zu tun ist.
CREATE INDEX IF NOT EXISTS render_job_queue
    ON render_job (status, created_at);

-- Die Liste im Web zeigt die neuesten Jobs zuerst.
CREATE INDEX IF NOT EXISTS render_job_neueste
    ON render_job (created_at DESC);

-- ---------------------------------------------------------------------------
-- Ab hier: der Weg vom Upload zum Template, und konsistente Figuren.
--
-- ALLES NEUE STEHT IN NEUEN TABELLEN, und das ist keine Stilfrage. Diese
-- Datei laeuft als `executescript` mit `CREATE TABLE IF NOT EXISTS`. Eine
-- neue Tabelle entsteht damit auf einer Bestandsinstallation von selbst --
-- eine neue SPALTE in render_job oder cut_template waere dagegen still
-- wirkungslos, denn SQLite kennt kein `ADD COLUMN IF NOT EXISTS` und
-- `CREATE TABLE IF NOT EXISTS` aendert eine bestehende Tabelle nicht an.
-- Auf der Box liegt seit der Installation eine Datenbank mit Daten; sie
-- ueberlebt auch die Deinstallation. Ein Schema-Wechsel, der eine Spalte
-- braucht, braucht darum eine echte Migration und keinen Einzeiler hier.
--
-- Was ohne Schema-Aenderung geht: `render_job.clips` ist JSON. Ein neues
-- Feld darin (z. B. figur_id) kostet keine DDL, und aeltere Jobs bleiben
-- lesbar, weil jedes Feld beim Lesen einen Standard hat.
-- ---------------------------------------------------------------------------

-- Hochgeladene Dateien. Videos als Quelle fuer die Extraktion, Bilder als
-- Referenz fuer eine Figur.
CREATE TABLE IF NOT EXISTS upload (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    uri        TEXT NOT NULL,
    art        TEXT NOT NULL CHECK (art IN ('video','bild')),
    bytes      INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS upload_neueste
    ON upload (created_at DESC);

-- Extraktionsauftrag: aus einem hochgeladenen Video ein CutTemplate gewinnen.
--
-- Eigene Tabelle und eigene Warteschlange statt einer Spalte `art` in
-- render_job -- aus dem Grund im Block oben. Der Worker pollt beide, und
-- Extraktion geht vor: sie ist kurz, rechnet auf der CPU und kostet nichts,
-- waehrend ein Render mit fal-Clips Minuten dauern und Geld kosten kann.
CREATE TABLE IF NOT EXISTS extract_job (
    id          TEXT PRIMARY KEY,
    upload_id   TEXT NOT NULL REFERENCES upload(id),
    name        TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('queued','running','done','failed')),
    error       TEXT,
    template_id TEXT REFERENCES cut_template(id),
    created_at  INTEGER NOT NULL,
    started_at  INTEGER,
    finished_at INTEGER
);

CREATE INDEX IF NOT EXISTS extract_job_queue
    ON extract_job (status, created_at);

CREATE INDEX IF NOT EXISTS extract_job_neueste
    ON extract_job (created_at DESC);

-- Figur: eine Person, die ueber mehrere Einstellungen hinweg gleich aussehen
-- soll.
--
-- Das ist der Kern der Konsistenz, und sie entsteht aus drei Dingen, die
-- zusammenwirken -- keines davon reicht allein:
--
--   referenz_uri  ein Bild. Der starke Hebel: dasselbe Bild in jeder
--                 Einstellung, ueber ein Bild-zu-Video-Modell.
--   beschreibung  Prompt-Bausteine, die in JEDE Einstellung mit dieser Figur
--                 einwandern. Schwach, aber kostenlos.
--   seed          eine feste Zahl. Manche Modelle liefern damit stabilere
--                 Ergebnisse; ohne Angabe wird sie aus der id abgeleitet,
--                 damit dieselbe Figur immer dieselbe Zahl bekommt.
--
-- LoRA-Training bleibt draussen -- CLAUDE.md schliesst es aus, und es
-- braeuchte GPU-Zeit und Trainingsdaten.
CREATE TABLE IF NOT EXISTS figur (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    beschreibung TEXT NOT NULL DEFAULT '',
    referenz_uri TEXT NOT NULL DEFAULT '',
    seed         INTEGER,
    created_at   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS figur_name
    ON figur (name);
