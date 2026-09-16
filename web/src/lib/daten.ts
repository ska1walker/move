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

export type Clip = {
  index: number;
  source: string;
  uri: string;
  /** Nur bei source 'fal': was erzeugt werden soll. */
  prompt?: string;
  /** Nur bei source 'fal'; leer heisst der Standard des Workers. */
  model?: string;
  /**
   * Nur bei source 'fal': die Figur, die in dieser Einstellung auftreten
   * soll. Ihre Beschreibung, ihr Referenzbild und ihr Seed wandern dann in
   * jede Einstellung ein, die sie nennt -- das ist der Mechanismus hinter
   * konsistenten Personen.
   *
   * Steht im JSON und nicht in einer Spalte: `render_job.clips` ist Text,
   * ein neues Feld kostet damit keine DDL, und aeltere Jobs bleiben lesbar.
   */
  figur_id?: string;

  /**
   * Referenzbild fuer GENAU DIESE Einstellung, Pfad relativ zum
   * Datenverzeichnis.
   *
   * Der Unterschied zu `figur_id` ist nicht technisch, sondern inhaltlich:
   * eine Figur ist ein Bild fuer VIELE Einstellungen (dieselbe Person
   * mehrfach), `bild_uri` ist ein Bild fuer EINE (jede Szene ihr eigenes).
   * Beides zusammen geht: die Figur traegt dann Beschreibung und Seed, das
   * Bild der Einstellung gewinnt als Bildvorgabe -- das Spezifischere
   * gewinnt.
   */
  bild_uri?: string;
};

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

// ---------------------------------------------------------------------------
// Uploads, Extraktion, Figuren
//
// Der Weg, der in v0 gefehlt hat: ohne ihn musste jemand im Worker-Pod
// `move_worker extract` tippen, um ueberhaupt ein Template zu bekommen -- und
// ohne Template zeigt die Seite nur einen Hinweistext.
//
// Das Web REIHT EIN und liest. Extrahiert wird im Worker, wie gerendert wird:
// ffmpeg und librosa liegen im Worker-Image, nicht im Web, und der Envoy-
// Sidecar laesst ohnehin keinen Aufruf zwischen den Pods zu.
// ---------------------------------------------------------------------------

export type UploadArt = 'video' | 'bild';

export type Upload = {
  id: string;
  name: string;
  uri: string;
  art: UploadArt;
  bytes: number;
  created_at: number;
};

export type ExtractJob = {
  id: string;
  upload_id: string;
  name: string;
  status: Status;
  error: string | null;
  template_id: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
};

export type Figur = {
  id: string;
  name: string;
  beschreibung: string;
  referenz_uri: string;
  seed: number | null;
  created_at: number;
};

type UploadZeile = {
  id: string;
  name: string;
  uri: string;
  art: string;
  bytes: number;
  created_at: number;
};

export function uploadAblegen(
  name: string,
  uri: string,
  art: UploadArt,
  bytes: number,
): string {
  const id = neueId();
  db()
    .prepare(
      'INSERT INTO upload (id, name, uri, art, bytes, created_at) VALUES (?, ?, ?, ?, ?, ?)',
    )
    .run(id, name, uri, art, bytes, jetztMs());
  return id;
}

export function uploads(art: UploadArt | null = null, grenze = 50): Upload[] {
  const zeilen = (
    art === null
      ? db().prepare('SELECT * FROM upload ORDER BY created_at DESC LIMIT ?').all(grenze)
      : db()
          .prepare('SELECT * FROM upload WHERE art = ? ORDER BY created_at DESC LIMIT ?')
          .all(art, grenze)
  ) as unknown as UploadZeile[];
  return zeilen.map((z) => ({ ...z, art: z.art as UploadArt }));
}

export function upload(id: string): Upload | null {
  const z = db().prepare('SELECT * FROM upload WHERE id = ?').get(id) as unknown as
    | UploadZeile
    | undefined;
  return z ? { ...z, art: z.art as UploadArt } : null;
}

export function extraktionEinreihen(uploadId: string, name: string): string {
  const id = neueId();
  db()
    .prepare(
      'INSERT INTO extract_job (id, upload_id, name, status, created_at) VALUES (?, ?, ?, ?, ?)',
    )
    .run(id, uploadId, name, 'queued', jetztMs());
  return id;
}

export function extraktionen(grenze = 25): ExtractJob[] {
  return db()
    .prepare('SELECT * FROM extract_job ORDER BY created_at DESC LIMIT ?')
    .all(grenze) as unknown as ExtractJob[];
}

export function figuren(): Figur[] {
  return db().prepare('SELECT * FROM figur ORDER BY name').all() as unknown as Figur[];
}

export function figur(id: string): Figur | null {
  const z = db().prepare('SELECT * FROM figur WHERE id = ?').get(id) as unknown as
    | Figur
    | undefined;
  return z ?? null;
}

export function figurAnlegen(
  name: string,
  beschreibung: string,
  referenzUri: string,
): string {
  const id = neueId();
  db()
    .prepare(
      'INSERT INTO figur (id, name, beschreibung, referenz_uri, created_at) ' +
        'VALUES (?, ?, ?, ?, ?)',
    )
    // seed bleibt NULL: der Worker leitet ihn dann stabil aus der id ab.
    // Eine Zahl hier zu erfinden waere fuer alle Figuren dieselbe oder je
    // Aufruf eine andere -- beides ist das Gegenteil von Konsistenz.
    .run(id, name.trim(), beschreibung, referenzUri, jetztMs());
  return id;
}

export function figurLoeschen(id: string): boolean {
  const cur = db().prepare('DELETE FROM figur WHERE id = ?').run(id);
  return Number(cur.changes ?? 0) > 0;
}
