"""Datenzugriff. Die einzige Stelle im Worker mit SQL.

v0 legt SQLite auf `/app/data` ab. Begruendung steht in CLAUDE.md: hostPath
brauchen wir fuer Uploads, Zwischenclips und Renderergebnisse ohnehin, und
`/app/data` ueberlebt die Deinstallation, waehrend die Olares-Postgres dabei
geloescht wird.

Alles laeuft durch dieses Modul, damit der Wechsel auf die Olares-Postgres in
v1 genau eine Datei betrifft. Kein SQL ausserhalb.

Die DDL steht NICHT hier, sondern in `db/schema.sql` -- derselben Datei, die
auch das Web liest. Zwei Schemata in zwei Sprachen driften auseinander, und
zwar still: ein fehlender CHECK nimmt kaputte Daten an, ein fehlender Index
macht nur langsam.

Nicht unter `db/migrations/`: jener Ordner ist fuer die Postgres-Migrationen
gedacht, die ueber eine ConfigMap ins Chart wandern. Fuer SQLite waere das ein
Umweg ueber den Cluster, den niemand braucht.

Zeitstempel sind ueberall ganze Millisekunden.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .templates import CutTemplate

LOG = logging.getLogger(__name__)

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_ALLE = (STATUS_QUEUED, STATUS_RUNNING, STATUS_DONE, STATUS_FAILED)

class RepositoryError(RuntimeError):
    """Ein Datenzugriff ist gescheitert."""


# Kandidaten fuer die Schemadatei. MOVE_SCHEMA_FILE gewinnt immer.
#
# Die DDL steht bewusst NICHT hier: Web und Worker lesen dieselbe Datei
# (db/schema.sql), damit zwei Sprachen nicht zwei Schemata bauen.
SCHEMA_KANDIDATEN = (
    Path("/app/db/schema.sql"),
    Path(__file__).resolve().parent.parent.parent / "db" / "schema.sql",
)


def schema_file() -> Path:
    gesetzt = os.environ.get("MOVE_SCHEMA_FILE")
    if gesetzt:
        pfad = Path(gesetzt)
        if not pfad.is_file():
            raise RepositoryError(f"MOVE_SCHEMA_FILE zeigt auf {pfad}, dort liegt keine Datei.")
        return pfad

    for kandidat in SCHEMA_KANDIDATEN:
        if kandidat.is_file():
            return kandidat

    raise RepositoryError(
        "db/schema.sql nicht gefunden. Das Image muss die Datei mitbringen, "
        "oder MOVE_SCHEMA_FILE muss auf sie zeigen. Gesucht wurde in: "
        + ", ".join(str(k) for k in SCHEMA_KANDIDATEN)
    )


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class Clip:
    """Ein Clip fuer eine Einstellung.

    `source` sagt, woher er kommt:

        placeholder  der Worker erzeugt eine Flaeche mit Index und Timecode
        fal          der Worker laesst ihn bei fal.ai erzeugen, aus `prompt`
        alles andere `uri` verweist auf eine Datei im Datenverzeichnis

    `prompt` und `model` stehen so nicht im Datenmodell in CLAUDE.md -- dort
    ist Video-Generierung fuer v0 ausgeschlossen. Ohne Beschreibung kann eine
    Generierung aber nicht wissen, was sie erzeugen soll. Beide Felder sind
    optional und fallen auf "" zurueck; aeltere Jobs bleiben lesbar.

    `model` leer heisst: das aus MOVE_FAL_MODEL.

    `figur_id` verweist auf eine Zeile in `figur` und ist der Weg zu
    konsistenten Personen: Beschreibung und Referenzbild dieser Figur wandern
    in JEDE Einstellung ein, die sie nennt. Das Feld steht im JSON und nicht
    in einer Spalte -- `render_job.clips` ist Text, ein neues Feld kostet
    darum keine DDL, und aeltere Jobs bleiben lesbar, weil hier jedes Feld
    einen Standard hat.
    """

    index: int
    source: str = "placeholder"
    uri: str = ""
    prompt: str = ""
    model: str = ""
    figur_id: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "source": self.source,
            "uri": self.uri,
            "prompt": self.prompt,
            "model": self.model,
            "figur_id": self.figur_id,
        }

    @classmethod
    def from_json(cls, roh: dict[str, Any]) -> "Clip":
        return cls(
            index=int(roh["index"]),
            source=str(roh.get("source", "placeholder")),
            uri=str(roh.get("uri", "")),
            prompt=str(roh.get("prompt", "")),
            model=str(roh.get("model", "")),
            figur_id=str(roh.get("figur_id", "")),
        )


@dataclass(frozen=True)
class RenderJob:
    id: str
    template_id: str
    status: str
    clips: tuple[Clip, ...] = ()
    error: str | None = None
    output_uri: str | None = None
    created_at: int = 0
    started_at: int | None = None
    finished_at: int | None = None

    @property
    def nur_platzhalter(self) -> bool:
        return all(c.source == "placeholder" for c in self.clips)


@dataclass(frozen=True)
class Upload:
    """Eine hochgeladene Datei. `art` ist 'video' oder 'bild'."""

    id: str
    name: str
    uri: str
    art: str
    bytes: int = 0
    created_at: int = 0


@dataclass(frozen=True)
class ExtractJob:
    """Auftrag: aus einem hochgeladenen Video ein CutTemplate gewinnen."""

    id: str
    upload_id: str
    name: str
    status: str
    error: str | None = None
    template_id: str | None = None
    created_at: int = 0
    started_at: int | None = None
    finished_at: int | None = None


@dataclass(frozen=True)
class Figur:
    """Eine Person, die ueber mehrere Einstellungen gleich aussehen soll.

    `seed` leer heisst nicht "kein Seed", sondern "einer, der aus der id
    kommt". Ein Zufallswert je Aufruf waere genau das Gegenteil von
    Konsistenz, und ein fester Wert im Code waere fuer alle Figuren derselbe.
    """

    id: str
    name: str
    beschreibung: str = ""
    referenz_uri: str = ""
    seed: int | None = None
    created_at: int = 0

    def wirksamer_seed(self) -> int:
        if self.seed is not None:
            return int(self.seed)
        # Stabil, aus der id. Auf 31 Bit begrenzt, weil manche Modelle
        # groessere Zahlen ablehnen.
        roh = hashlib.sha256(self.id.encode("utf-8")).digest()
        return int.from_bytes(roh[:4], "big") & 0x7FFFFFFF


@dataclass
class Repository:
    """SQLite auf dem App-Datenverzeichnis."""

    db_path: Path
    _conn: sqlite3.Connection | None = field(default=None, repr=False)

    # -- Verbindung -------------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn

        self.db_path = Path(self.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # isolation_level=None schaltet die automatische Transaktionssteuerung
        # ab. Ohne das kann BEGIN IMMEDIATE nicht selbst gesetzt werden -- und
        # genau darauf beruht das Ziehen aus der Warteschlange.
        conn = sqlite3.connect(str(self.db_path), isolation_level=None, timeout=30.0)
        conn.row_factory = sqlite3.Row

        # WAL: Leser blockieren den Schreiber nicht. Web und Worker greifen
        # gleichzeitig auf dieselbe Datei zu.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        # Wartet, statt sofort mit "database is locked" abzubrechen.
        conn.execute("PRAGMA busy_timeout=30000")

        self._conn = conn
        return conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Repository":
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def migrate(self) -> None:
        """Legt das Schema an. Mehrfach aufrufbar."""
        datei = schema_file()
        self.connect().executescript(datei.read_text(encoding="utf-8"))
        LOG.info("Schema bereit", extra={"db": str(self.db_path), "schema": str(datei)})

    # -- Templates --------------------------------------------------------

    def save_template(self, template: CutTemplate) -> str:
        template.validate()
        kennung = template.id or new_id()
        self.connect().execute(
            "INSERT OR REPLACE INTO cut_template "
            "(id, name, source_label, duration_ms, cuts, beat_grid, shot_count, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                kennung,
                template.name,
                template.source_label,
                template.duration_ms,
                json.dumps([c.to_json() for c in template.cuts]),
                json.dumps(template.beat_grid.to_json()) if template.beat_grid else None,
                template.shot_count,
                now_ms(),
            ),
        )
        return kennung

    def get_template(self, template_id: str) -> CutTemplate:
        zeile = self.connect().execute(
            "SELECT * FROM cut_template WHERE id = ?", (template_id,)
        ).fetchone()
        if zeile is None:
            raise RepositoryError(f"Kein cut_template mit der id {template_id!r}")

        return CutTemplate.from_json(
            {
                "id": zeile["id"],
                "name": zeile["name"],
                "source_label": zeile["source_label"],
                "duration_ms": zeile["duration_ms"],
                "cuts": json.loads(zeile["cuts"]),
                "beat_grid": json.loads(zeile["beat_grid"]) if zeile["beat_grid"] else None,
                "shot_count": zeile["shot_count"],
            }
        )

    # -- Warteschlange ----------------------------------------------------

    def enqueue(self, template_id: str, clips: list[Clip]) -> str:
        job_id = new_id()
        self.connect().execute(
            "INSERT INTO render_job (id, template_id, status, clips, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                job_id,
                template_id,
                STATUS_QUEUED,
                json.dumps([c.to_json() for c in clips]),
                now_ms(),
            ),
        )
        LOG.info("Job eingereiht", extra={"job_id": job_id, "template_id": template_id})
        return job_id

    def claim_next(self) -> RenderJob | None:
        """Nimmt den aeltesten wartenden Job und setzt ihn auf running.

        `BEGIN IMMEDIATE` nimmt die Schreibsperre sofort, nicht erst beim
        ersten UPDATE. Ohne das koennten zwei Worker dieselbe Zeile lesen und
        einer liefe beim Schreiben in "database is locked" -- oder, schlimmer,
        beide renderten denselben Job.
        """
        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            zeile = conn.execute(
                "SELECT * FROM render_job WHERE status = ? ORDER BY created_at LIMIT 1",
                (STATUS_QUEUED,),
            ).fetchone()
            if zeile is None:
                conn.execute("COMMIT")
                return None

            gestartet = now_ms()
            conn.execute(
                "UPDATE render_job SET status = ?, started_at = ? WHERE id = ?",
                (STATUS_RUNNING, gestartet, zeile["id"]),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        return self._als_job(zeile, status=STATUS_RUNNING, started_at=gestartet)

    def mark_done(self, job_id: str, output_uri: str) -> None:
        self.connect().execute(
            "UPDATE render_job SET status = ?, output_uri = ?, finished_at = ?, error = NULL "
            "WHERE id = ?",
            (STATUS_DONE, output_uri, now_ms(), job_id),
        )
        LOG.info("Job fertig", extra={"job_id": job_id, "output_uri": output_uri})

    def mark_failed(self, job_id: str, error: str) -> None:
        self.connect().execute(
            "UPDATE render_job SET status = ?, error = ?, finished_at = ? WHERE id = ?",
            (STATUS_FAILED, error, now_ms(), job_id),
        )
        # Auf error-Ebene, nicht debug: ein gescheiterter Job ist das Symptom,
        # unter dem oft ein Plattformfehler liegt.
        LOG.error("Job gescheitert", extra={"job_id": job_id, "error": error})

    def get_job(self, job_id: str) -> RenderJob:
        zeile = self.connect().execute(
            "SELECT * FROM render_job WHERE id = ?", (job_id,)
        ).fetchone()
        if zeile is None:
            raise RepositoryError(f"Kein render_job mit der id {job_id!r}")
        return self._als_job(zeile)

    def count_by_status(self) -> dict[str, int]:
        zeilen = self.connect().execute(
            "SELECT status, COUNT(*) AS anzahl FROM render_job GROUP BY status"
        ).fetchall()
        return {z["status"]: z["anzahl"] for z in zeilen}

    def reset_stale_running(self) -> int:
        """Setzt haengengebliebene Jobs zurueck in die Warteschlange.

        hostPath erzwingt `strategy: Recreate`: bei einem Update wird der Pod
        beendet und neu gestartet. Ein Job, der dabei mitten im Rendern war,
        stuende sonst fuer immer auf running und blockierte still.
        """
        conn = self.connect()
        cur = conn.execute(
            "UPDATE render_job SET status = ?, started_at = NULL WHERE status = ?",
            (STATUS_QUEUED, STATUS_RUNNING),
        )
        anzahl = cur.rowcount or 0
        if anzahl:
            LOG.warning(
                "haengende Jobs zurueck in die Warteschlange",
                extra={"anzahl": anzahl},
            )
        return anzahl

    # -- Uploads ----------------------------------------------------------

    def save_upload(self, name: str, uri: str, art: str, bytes_gesamt: int) -> str:
        if art not in ("video", "bild"):
            raise RepositoryError(f"art muss 'video' oder 'bild' sein, nicht {art!r}")
        kennung = new_id()
        self.connect().execute(
            "INSERT INTO upload (id, name, uri, art, bytes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (kennung, name, uri, art, int(bytes_gesamt), now_ms()),
        )
        LOG.info("Upload abgelegt", extra={"upload_id": kennung, "art": art, "bytes": bytes_gesamt})
        return kennung

    def get_upload(self, upload_id: str) -> Upload:
        zeile = self.connect().execute(
            "SELECT * FROM upload WHERE id = ?", (upload_id,)
        ).fetchone()
        if zeile is None:
            raise RepositoryError(f"Kein upload mit der id {upload_id!r}")
        return Upload(
            id=zeile["id"],
            name=zeile["name"],
            uri=zeile["uri"],
            art=zeile["art"],
            bytes=zeile["bytes"],
            created_at=zeile["created_at"],
        )

    # -- Extraktion -------------------------------------------------------

    def enqueue_extract(self, upload_id: str, name: str) -> str:
        job_id = new_id()
        self.connect().execute(
            "INSERT INTO extract_job (id, upload_id, name, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (job_id, upload_id, name, STATUS_QUEUED, now_ms()),
        )
        LOG.info("Extraktion eingereiht", extra={"job_id": job_id, "upload_id": upload_id})
        return job_id

    def claim_next_extract(self) -> ExtractJob | None:
        """Dasselbe BEGIN IMMEDIATE wie bei den Render-Jobs.

        Eigene Methode statt eines Parameters an claim_next: die beiden
        Tabellen haben verschiedene Spalten, und ein generisches Ziehen
        ueber zwei Tabellen waere zwei Abfragen in einer Transaktion -- mehr
        Schloss, kein Gewinn.
        """
        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            zeile = conn.execute(
                "SELECT * FROM extract_job WHERE status = ? ORDER BY created_at LIMIT 1",
                (STATUS_QUEUED,),
            ).fetchone()
            if zeile is None:
                conn.execute("COMMIT")
                return None

            gestartet = now_ms()
            conn.execute(
                "UPDATE extract_job SET status = ?, started_at = ? WHERE id = ?",
                (STATUS_RUNNING, gestartet, zeile["id"]),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        return ExtractJob(
            id=zeile["id"],
            upload_id=zeile["upload_id"],
            name=zeile["name"],
            status=STATUS_RUNNING,
            error=zeile["error"],
            template_id=zeile["template_id"],
            created_at=zeile["created_at"],
            started_at=gestartet,
            finished_at=zeile["finished_at"],
        )

    def mark_extract_done(self, job_id: str, template_id: str) -> None:
        self.connect().execute(
            "UPDATE extract_job SET status = ?, template_id = ?, finished_at = ?, error = NULL "
            "WHERE id = ?",
            (STATUS_DONE, template_id, now_ms(), job_id),
        )
        LOG.info("Extraktion fertig", extra={"job_id": job_id, "template_id": template_id})

    def mark_extract_failed(self, job_id: str, error: str) -> None:
        self.connect().execute(
            "UPDATE extract_job SET status = ?, error = ?, finished_at = ? WHERE id = ?",
            (STATUS_FAILED, error, now_ms(), job_id),
        )
        LOG.error("Extraktion gescheitert", extra={"job_id": job_id, "error": error})

    def reset_stale_extract(self) -> int:
        """Wie reset_stale_running, fuer die Extraktions-Warteschlange.

        Ohne das stuende eine Extraktion, die ein Pod-Neustart mitten in
        ffmpeg getroffen hat, fuer immer auf running -- und die Oberflaeche
        zeigte einen Auftrag, an dem niemand arbeitet.
        """
        cur = self.connect().execute(
            "UPDATE extract_job SET status = ?, started_at = NULL WHERE status = ?",
            (STATUS_QUEUED, STATUS_RUNNING),
        )
        anzahl = cur.rowcount or 0
        if anzahl:
            LOG.warning(
                "haengende Extraktionen zurueck in die Warteschlange",
                extra={"anzahl": anzahl},
            )
        return anzahl

    # -- Figuren ----------------------------------------------------------

    def save_figur(self, figur: Figur) -> str:
        kennung = figur.id or new_id()
        if not figur.name.strip():
            raise RepositoryError("Eine Figur braucht einen Namen")
        self.connect().execute(
            "INSERT OR REPLACE INTO figur "
            "(id, name, beschreibung, referenz_uri, seed, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                kennung,
                figur.name.strip(),
                figur.beschreibung,
                figur.referenz_uri,
                figur.seed,
                figur.created_at or now_ms(),
            ),
        )
        return kennung

    def get_figur(self, figur_id: str) -> Figur:
        zeile = self.connect().execute(
            "SELECT * FROM figur WHERE id = ?", (figur_id,)
        ).fetchone()
        if zeile is None:
            raise RepositoryError(f"Keine figur mit der id {figur_id!r}")
        return Figur(
            id=zeile["id"],
            name=zeile["name"],
            beschreibung=zeile["beschreibung"],
            referenz_uri=zeile["referenz_uri"],
            seed=zeile["seed"],
            created_at=zeile["created_at"],
        )

    def figuren(self) -> list[Figur]:
        zeilen = self.connect().execute(
            "SELECT * FROM figur ORDER BY name"
        ).fetchall()
        return [
            Figur(
                id=z["id"],
                name=z["name"],
                beschreibung=z["beschreibung"],
                referenz_uri=z["referenz_uri"],
                seed=z["seed"],
                created_at=z["created_at"],
            )
            for z in zeilen
        ]

    # -- intern -----------------------------------------------------------

    @staticmethod
    def _als_job(zeile: sqlite3.Row, **ueberschreiben: Any) -> RenderJob:
        daten = {
            "id": zeile["id"],
            "template_id": zeile["template_id"],
            "status": zeile["status"],
            "clips": tuple(Clip.from_json(c) for c in json.loads(zeile["clips"])),
            "error": zeile["error"],
            "output_uri": zeile["output_uri"],
            "created_at": zeile["created_at"],
            "started_at": zeile["started_at"],
            "finished_at": zeile["finished_at"],
        }
        daten.update(ueberschreiben)
        return RenderJob(**daten)
