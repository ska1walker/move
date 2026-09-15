/**
 * Liefert das Ergebnis eines Jobs aus.
 *
 * Mit Range-Unterstuetzung, sonst kann der Browser im Video nicht springen --
 * ein `<video>` ohne 206 spielt oft nur von vorn oder gar nicht.
 *
 * Gestreamt, nicht gelesen: ein fertiges Rendering kann hunderte Megabyte
 * gross sein, und `readFile` wuerde es komplett in den Speicher holen.
 */

import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import { isAbsolute, join, normalize } from 'node:path';
import { Readable } from 'node:stream';

import { datenVerzeichnis, job } from '@/lib/daten';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function alsWebStream(pfad: string, start: number, ende: number): ReadableStream {
  const lesen = createReadStream(pfad, { start, end: ende });
  return Readable.toWeb(lesen) as ReadableStream;
}

export async function GET(request: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;

  const eintrag = job(id);
  if (!eintrag) {
    return Response.json({ fehler: `Kein Job mit der id ${id}` }, { status: 404 });
  }
  if (eintrag.status !== 'done' || !eintrag.output_uri) {
    return Response.json(
      { fehler: `Job ${id} hat den Stand ${eintrag.status}, es gibt noch kein Ergebnis` },
      { status: 409 },
    );
  }

  // output_uri kommt aus der eigenen Datenbank, wird aber genauso geprueft
  // wie eine Eingabe. Eine Zeile kann auch durch einen Fehler dorthin
  // gelangen, und dann soll sie nicht aus dem Datenverzeichnis herausfuehren.
  const relativ = normalize(eintrag.output_uri);
  if (isAbsolute(relativ) || relativ.startsWith('..')) {
    console.error(`[renders/${id}] output_uri fuehrt aus dem Datenverzeichnis: ${relativ}`);
    return Response.json({ fehler: 'Ergebnispfad ist nicht zulaessig' }, { status: 500 });
  }
  const pfad = join(datenVerzeichnis(), relativ);

  let groesse: number;
  try {
    groesse = (await stat(pfad)).size;
  } catch {
    console.error(`[renders/${id}] Datei fehlt: ${pfad}`);
    return Response.json({ fehler: 'Ergebnisdatei fehlt' }, { status: 410 });
  }

  const kopf = {
    'Content-Type': 'video/mp4',
    'Accept-Ranges': 'bytes',
    'Cache-Control': 'private, max-age=0, must-revalidate',
  };

  const range = request.headers.get('range');
  if (!range) {
    return new Response(alsWebStream(pfad, 0, groesse - 1), {
      status: 200,
      headers: { ...kopf, 'Content-Length': String(groesse) },
    });
  }

  const treffer = /^bytes=(\d*)-(\d*)$/.exec(range.trim());
  if (!treffer) {
    return new Response(null, {
      status: 416,
      headers: { ...kopf, 'Content-Range': `bytes */${groesse}` },
    });
  }

  const [, vonRoh, bisRoh] = treffer;
  let von = vonRoh === '' ? 0 : Number(vonRoh);
  let bis = bisRoh === '' ? groesse - 1 : Number(bisRoh);

  if (vonRoh === '' && bisRoh !== '') {
    // bytes=-500 meint die letzten 500 Bytes.
    von = Math.max(0, groesse - Number(bisRoh));
    bis = groesse - 1;
  }

  if (von > bis || von >= groesse) {
    return new Response(null, {
      status: 416,
      headers: { ...kopf, 'Content-Range': `bytes */${groesse}` },
    });
  }
  bis = Math.min(bis, groesse - 1);

  return new Response(alsWebStream(pfad, von, bis), {
    status: 206,
    headers: {
      ...kopf,
      'Content-Range': `bytes ${von}-${bis}/${groesse}`,
      'Content-Length': String(bis - von + 1),
    },
  });
}
