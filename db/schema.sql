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
