/**
 * Zielpunkt der Probes im Chart.
 *
 * Bewusst mehr als ein leeres 200: der Pod soll erst bereit sein, wenn er
 * auch an die Datenbank kommt. Ein "running", hinter dem die Anwendung nicht
 * arbeiten kann, ist die Kachel, die gruen leuchtet und nichts tut.
 */

import { db, dbPfad } from '@/lib/daten';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const zeile = db().prepare('SELECT COUNT(*) AS anzahl FROM render_job').get() as unknown as {
      anzahl: number;
    };
    return Response.json({
      status: 'ok',
      db: dbPfad(),
      jobs: zeile.anzahl,
    });
  } catch (fehler) {
    const text = fehler instanceof Error ? fehler.message : String(fehler);
    console.error(`[health] Datenbank nicht erreichbar: ${text}`);
    return Response.json({ status: 'fehler', grund: text }, { status: 503 });
  }
}
