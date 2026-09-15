/**
 * Datenzugriff des Web. Die einzige Stelle im Web mit SQL.
 *
 * Alles laeuft durch dieses Modul, damit der Wechsel auf die Olares-Postgres
 * in v1 genau eine Datei betrifft. Die DDL steht in `db/schema.sql` --
 * derselben Datei, die der Worker liest.
 *
 * Das Web reiht Jobs ein und liest ihren Stand. Es zieht KEINE Jobs aus der
 * Warteschlange; das macht allein der Worker.
 *
 * `node:sqlite` ist ein Node-Builtin und braucht keine native Abhaengigkeit.
 * Es ist als experimentell markiert -- deshalb steht es hinter diesem Modul
 * und nirgends sonst.
 */

import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { DatabaseSync } from 'node:sqlite';

export type Status = 'queued' | 'running' | 'done' | 'failed';

export type Clip = { index: number; source: string; uri: string };

export type Cut = {
  at_ms: number;
  transition: string;
  shot_scale: string;
  transition_ms: number;
};

export type CutTemplate = {
  id: string;
  name: string;
  source_label: string;
  duration_ms: number;
  cuts: Cut[];
  beat_grid: { bpm: number; offsets_ms: number[] } | null;
  shot_count: number;
  created_at: number;
};

export type RenderJob = {
  id: string;
  template_id: string;
  status: Status;
  error: string | null;
  clips: Clip[];
  output_uri: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
};

/** Datenverzeichnis IM Container. Nicht `.Values.userspace.appData`. */
export function datenVerzeichnis(): string {
  return process.env.MOVE_DATA_DIR ?? '/app/data';
}

export function dbPfad(): string {
  return join(datenVerzeichnis(), 'db', 'move.sqlite3');
}

function schemaDatei(): string {
  const gesetzt = process.env.MOVE_SCHEMA_FILE;
  if (gesetzt) {
    if (!existsSync(gesetzt)) {
      throw new Error(`MOVE_SCHEMA_FILE zeigt auf ${gesetzt}, dort liegt keine Datei.`);
    }
    return gesetzt;
  }

  const kandidaten = [
    '/app/db/schema.sql',
    resolve(process.cwd(), 'db', 'schema.sql'),
    resolve(process.cwd(), '..', 'db', 'schema.sql'),
  ];
  for (const k of kandidaten) {
    if (existsSync(k)) return k;
  }
  throw new Error(
    'db/schema.sql nicht gefunden. Das Image muss die Datei mitbringen, oder ' +
      `MOVE_SCHEMA_FILE muss auf sie zeigen. Gesucht wurde in: ${kandidaten.join(', ')}`,
  );
}

// Eine Verbindung je Prozess. Next legt Module im Entwicklungsmodus mehrfach
// an, deshalb der Umweg ueber globalThis.
const zwischenspeicher = globalThis as unknown as { moveDb?: DatabaseSync };

export function db(): DatabaseSync {
  if (zwischenspeicher.moveDb) return zwischenspeicher.moveDb;

  const pfad = dbPfad();
  mkdirSync(dirname(pfad), { recursive: true });

  const verbindung = new DatabaseSync(pfad);
  // WAL: Leser blockieren den Schreiber nicht. Web und Worker greifen
  // gleichzeitig auf dieselbe Datei zu.
  verbindung.exec('PRAGMA journal_mode=WAL');
  verbindung.exec('PRAGMA synchronous=NORMAL');
  verbindung.exec('PRAGMA foreign_keys=ON');
  // Wartet, statt sofort mit "database is locked" abzubrechen.
  verbindung.exec('PRAGMA busy_timeout=30000');
  verbindung.exec(readFileSync(schemaDatei(), 'utf8'));

  zwischenspeicher.moveDb = verbindung;
  return verbindung;
}

export function jetztMs(): number {
  return Date.now();
}

export function neueId(): string {
  return crypto.randomUUID().replaceAll('-', '');
}

// -- Templates ---------------------------------------------------------------

type TemplateZeile = {
  id: string;
  name: string;
  source_label: string;
  duration_ms: number;
  cuts: string;
  beat_grid: string | null;
  shot_count: number;
  created_at: number;
};

function alsTemplate(z: TemplateZeile): CutTemplate {
  return {
    id: z.id,
    name: z.name,
    source_label: z.source_label,
    duration_ms: z.duration_ms,
    cuts: JSON.parse(z.cuts) as Cut[],
    beat_grid: z.beat_grid ? JSON.parse(z.beat_grid) : null,
    shot_count: z.shot_count,
    created_at: z.created_at,
  };
}

export function templates(): CutTemplate[] {
  const zeilen = db()
    .prepare('SELECT * FROM cut_template ORDER BY name')
    .all() as unknown as TemplateZeile[];
  return zeilen.map(alsTemplate);
}

export function template(id: string): CutTemplate | null {
  const z = db()
    .prepare('SELECT * FROM cut_template WHERE id = ?')
    .get(id) as unknown as TemplateZeile | undefined;
  return z ? alsTemplate(z) : null;
}

// -- Jobs --------------------------------------------------------------------

type JobZeile = {
  id: string;
  template_id: string;
  status: Status;
  error: string | null;
  clips: string;
  output_uri: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
};

function alsJob(z: JobZeile): RenderJob {
  return {
    id: z.id,
    template_id: z.template_id,
    status: z.status,
    error: z.error,
    clips: JSON.parse(z.clips) as Clip[],
    output_uri: z.output_uri,
    created_at: z.created_at,
    started_at: z.started_at,
    finished_at: z.finished_at,
  };
}

export function jobEinreihen(templateId: string, clips: Clip[]): string {
  const id = neueId();
  db()
    .prepare(
      'INSERT INTO render_job (id, template_id, status, clips, created_at) ' +
        'VALUES (?, ?, ?, ?, ?)',
    )
    .run(id, templateId, 'queued', JSON.stringify(clips), jetztMs());
  return id;
}

export function jobs(grenze = 50): RenderJob[] {
  const zeilen = db()
    .prepare('SELECT * FROM render_job ORDER BY created_at DESC LIMIT ?')
    .all(grenze) as unknown as JobZeile[];
  return zeilen.map(alsJob);
}

export function job(id: string): RenderJob | null {
  const z = db()
    .prepare('SELECT * FROM render_job WHERE id = ?')
    .get(id) as unknown as JobZeile | undefined;
  return z ? alsJob(z) : null;
}
