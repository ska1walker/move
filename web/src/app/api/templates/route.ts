/**
 * Nur lesen.
 *
 * Templates entstehen nicht im Web: in v0 legt sie der Worker per
 * `move_worker enqueue` ab, ab Schritt 8 die Extraktion aus einem Trailer.
 * Ein POST hier haette die vollstaendige Pruefung der Schnittliste ein
 * zweites Mal gebraucht -- in einer zweiten Sprache. Diese Pruefung steht in
 * `worker/move_worker/templates.py` und soll dort allein stehen.
 */

import { templates } from '@/lib/daten';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    return Response.json({ templates: templates() });
  } catch (fehler) {
    const text = fehler instanceof Error ? fehler.message : String(fehler);
    console.error(`[templates] ${text}`);
    return Response.json({ fehler: text }, { status: 500 });
  }
}
