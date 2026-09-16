/**
 * Extraktion einreihen: aus einem hochgeladenen Video ein CutTemplate.
 *
 * Das Web REIHT NUR EIN. Gearbeitet wird im Worker, aus zwei Gruenden, die
 * beide nicht verhandelbar sind:
 *
 *   - ffmpeg und librosa liegen im Worker-Image. Das Web-Image bringt sie
 *     nicht mit, und es soll sie nicht mitbringen -- eine Extraktion im
 *     Next-Prozess wuerde den Server fuer Minuten blockieren.
 *   - Zwischen den Pods gibt es keinen Aufruf. Der Envoy-Sidecar faengt jeden
 *     eingehenden TCP-Verkehr ab, auch clusterinternen; ein Aufruf vom
 *     Web-Pod auf den Worker endete in 401 ext_authz_denied. Die Job-Tabelle
 *     ist der einzige Weg.
 *
 * Deshalb gibt es hier kein Ergebnis zurueck, sondern eine Auftragsnummer.
 * Fertig ist es, wenn `extract_job.status` auf done steht und `template_id`
 * gesetzt ist.
 */

import { extraktionEinreihen, extraktionen, upload } from '@/lib/daten';
import { identitaet } from '@/lib/identitaet';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    return Response.json({ extraktionen: extraktionen() });
  } catch (fehler) {
    const text = fehler instanceof Error ? fehler.message : String(fehler);
    console.error(`[extractions] ${text}`);
    return Response.json({ fehler: text }, { status: 500 });
  }
}

export async function POST(request: Request) {
  const wer = identitaet(request.headers);
  try {
    const wunsch = (await request.json()) as { upload_id?: unknown; name?: unknown };

    const uploadId = typeof wunsch.upload_id === 'string' ? wunsch.upload_id.trim() : '';
    if (uploadId === '') {
      return Response.json({ fehler: 'upload_id fehlt' }, { status: 400 });
    }

    const quelle = upload(uploadId);
    if (!quelle) {
      return Response.json({ fehler: `Kein Upload mit der id ${uploadId}` }, { status: 404 });
    }
    if (quelle.art !== 'video') {
      // Frueh und im Klartext. Sonst nimmt die Warteschlange den Auftrag an
      // und der Worker scheitert Sekunden spaeter an derselben Bedingung.
      return Response.json(
        {
          fehler:
            `Upload ${uploadId} ist art '${quelle.art}'. Ein Template entsteht nur ` +
            `aus einem Video; Bilder sind Referenzen fuer eine Figur.`,
        },
        { status: 400 },
      );
    }

    const name = typeof wunsch.name === 'string' && wunsch.name.trim() !== ''
      ? wunsch.name.trim().slice(0, 200)
      : quelle.name;

    const id = extraktionEinreihen(uploadId, name);
    console.log(
      `[extractions] eingereiht id=${id} upload=${uploadId} name=${name} ` +
        `nutzer=${wer.nutzer ?? '-'}`,
    );
    return Response.json({ id }, { status: 201 });
  } catch (fehler) {
    const text = fehler instanceof Error ? fehler.message : String(fehler);
    console.error(`[extractions] Anlegen gescheitert: ${text}`);
    return Response.json({ fehler: text }, { status: 400 });
  }
}
