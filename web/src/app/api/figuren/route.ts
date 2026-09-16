/**
 * Figuren: Personen, die ueber mehrere Einstellungen gleich aussehen sollen.
 *
 * WIE KONSISTENZ HIER ZUSTANDE KOMMT, weil es sonst nach Magie aussieht: gar
 * nicht durch ein Modell, das sich erinnert. Sondern dadurch, dass jede
 * Einstellung mit derselben Figur DIESELBEN EINGABEN bekommt -- Beschreibung,
 * Referenzbild und Seed. Drei Hebel, von denen keiner allein reicht:
 *
 *   referenz_uri  ein Bild, das der Nutzer hochgeladen hat. Der starke Hebel,
 *                 ueber ein Bild-zu-Video-Modell. Ohne Bild bleibt es beim
 *                 Zufall, den ein Textmodell je Aufruf neu wuerfelt.
 *   beschreibung  Prompt-Bausteine, die VOR den Prompt der Einstellung
 *                 wandern. Schwach, aber kostenlos.
 *   seed          eine feste Zahl je Figur. Wird nicht hier gesetzt, sondern
 *                 im Worker stabil aus der id abgeleitet -- eine Zahl hier zu
 *                 erfinden waere fuer alle Figuren dieselbe oder je Aufruf
 *                 eine andere, und beides ist das Gegenteil von Konsistenz.
 *
 * LoRA-Training bleibt draussen: CLAUDE.md schliesst es aus, und es braeuchte
 * GPU-Zeit und Trainingsdaten.
 */

import { figurAnlegen, figurLoeschen, figuren, upload } from '@/lib/daten';
import { identitaet } from '@/lib/identitaet';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    return Response.json({ figuren: figuren() });
  } catch (fehler) {
    const text = fehler instanceof Error ? fehler.message : String(fehler);
    console.error(`[figuren] ${text}`);
    return Response.json({ fehler: text }, { status: 500 });
  }
}

export async function POST(request: Request) {
  const wer = identitaet(request.headers);
  try {
    const wunsch = (await request.json()) as {
      name?: unknown;
      beschreibung?: unknown;
      upload_id?: unknown;
    };

    const name = typeof wunsch.name === 'string' ? wunsch.name.trim() : '';
    if (name === '') {
      return Response.json({ fehler: 'name fehlt' }, { status: 400 });
    }

    const beschreibung =
      typeof wunsch.beschreibung === 'string' ? wunsch.beschreibung.trim() : '';
    const uploadId = typeof wunsch.upload_id === 'string' ? wunsch.upload_id.trim() : '';

    let referenzUri = '';
    if (uploadId !== '') {
      const bild = upload(uploadId);
      if (!bild) {
        return Response.json({ fehler: `Kein Upload mit der id ${uploadId}` }, { status: 404 });
      }
      if (bild.art !== 'bild') {
        return Response.json(
          { fehler: `Upload ${uploadId} ist art '${bild.art}'. Eine Referenz muss ein Bild sein.` },
          { status: 400 },
        );
      }
      referenzUri = bild.uri;
    }

    if (beschreibung === '' && referenzUri === '') {
      // Eine Figur ohne Beschreibung UND ohne Bild traegt nichts bei: sie
      // wuerde nur einen Seed setzen, und das allein macht keine Person
      // wiedererkennbar. Lieber hier ablehnen als spaeter erklaeren, warum
      // acht bezahlte Einstellungen acht verschiedene Gesichter zeigen.
      return Response.json(
        {
          fehler:
            'Eine Figur braucht eine Beschreibung oder ein Referenzbild, sonst ' +
            'bleibt sie wirkungslos. Das Bild ist der staerkere Hebel.',
        },
        { status: 400 },
      );
    }

    const id = figurAnlegen(name, beschreibung, referenzUri);
    console.log(
      `[figuren] angelegt id=${id} name=${name} bild=${referenzUri || '-'} ` +
        `nutzer=${wer.nutzer ?? '-'}`,
    );
    return Response.json({ id }, { status: 201 });
  } catch (fehler) {
    const text = fehler instanceof Error ? fehler.message : String(fehler);
    console.error(`[figuren] Anlegen gescheitert: ${text}`);
    return Response.json({ fehler: text }, { status: 400 });
  }
}

export async function DELETE(request: Request) {
  try {
    const id = new URL(request.url).searchParams.get('id') ?? '';
    if (id === '') {
      return Response.json({ fehler: 'Parameter id fehlt' }, { status: 400 });
    }
    // Das Referenzbild bleibt liegen. Es kann noch an einem fertigen Job
    // haengen, und ein geloeschtes Bild machte dessen Ergebnis nicht
    // nachvollziehbar.
    if (!figurLoeschen(id)) {
      return Response.json({ fehler: `Keine Figur mit der id ${id}` }, { status: 404 });
    }
    return Response.json({ geloescht: id });
  } catch (fehler) {
    const text = fehler instanceof Error ? fehler.message : String(fehler);
    console.error(`[figuren] Loeschen gescheitert: ${text}`);
    return Response.json({ fehler: text }, { status: 400 });
  }
}
