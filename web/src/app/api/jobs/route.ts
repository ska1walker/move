/**
 * Jobs auflisten und anlegen.
 *
 * Das Web reiht nur ein. Gezogen wird allein vom Worker, mit
 * `BEGIN IMMEDIATE` -- der Weg ueber die Job-Tabelle ist der einzige zwischen
 * Web und Worker, weil der Envoy-Sidecar jeden eingehenden TCP-Verkehr
 * abfaengt, auch clusterinternen.
 */

import { existsSync } from 'node:fs';
import { isAbsolute, join, normalize } from 'node:path';

import { datenVerzeichnis, jobEinreihen, jobs, template, type Clip } from '@/lib/daten';
import { identitaet } from '@/lib/identitaet';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

type Wunsch = {
  template_id?: unknown;
  clips?: unknown;
};

/** Prueft, dass eine uri im Datenverzeichnis liegt und die Datei da ist. */
function pruefeUri(index: number, uri: string): void {
  if (uri === '' || isAbsolute(uri)) {
    throw new Error(`clips[${index}].uri muss ein Pfad relativ zum Datenverzeichnis sein`);
  }
  const normal = normalize(uri);
  if (normal.startsWith('..')) {
    throw new Error(`clips[${index}].uri fuehrt aus dem Datenverzeichnis heraus`);
  }
  if (!existsSync(join(datenVerzeichnis(), normal))) {
    throw new Error(`clips[${index}].uri: ${normal} gibt es nicht`);
  }
}

export async function GET() {
  try {
    return Response.json({ jobs: jobs() });
  } catch (fehler) {
    const text = fehler instanceof Error ? fehler.message : String(fehler);
    console.error(`[jobs] ${text}`);
    return Response.json({ fehler: text }, { status: 500 });
  }
}

export async function POST(request: Request) {
  const wer = identitaet(request.headers);
  try {
    const wunsch = (await request.json()) as Wunsch;

    const templateId = typeof wunsch.template_id === 'string' ? wunsch.template_id : '';
    if (templateId === '') {
      return Response.json({ fehler: 'template_id fehlt' }, { status: 400 });
    }

    const vorlage = template(templateId);
    if (!vorlage) {
      return Response.json({ fehler: `Kein Template mit der id ${templateId}` }, { status: 404 });
    }

    // Ohne Angabe: Platzhalter fuer jede Einstellung. Das ist der Weg, auf
    // dem v0 ohne einen einzigen KI-Aufruf durchlaeuft.
    let clips: Clip[];
    if (Array.isArray(wunsch.clips) && wunsch.clips.length > 0) {
      clips = (wunsch.clips as unknown[]).map((roh, i) => {
        const c = roh as {
          index?: unknown;
          source?: unknown;
          uri?: unknown;
          prompt?: unknown;
          model?: unknown;
        };
        const index = typeof c.index === 'number' ? c.index : i;
        const source = typeof c.source === 'string' ? c.source : 'placeholder';
        const uri = typeof c.uri === 'string' ? c.uri : '';
        const prompt = typeof c.prompt === 'string' ? c.prompt.trim() : '';
        const model = typeof c.model === 'string' ? c.model.trim() : '';

        if (source === 'fal') {
          // Ohne Beschreibung kann nichts erzeugt werden -- und das soll hier
          // auffallen, nicht erst nachdem ein Aufruf bezahlt ist.
          if (prompt === '') {
            throw new Error(`clips[${index}]: source 'fal' braucht einen prompt`);
          }
        } else if (source !== 'placeholder') {
          pruefeUri(index, uri);
        }

        return { index, source, uri, prompt, model };
      });

      const indizes = new Set(clips.map((c) => c.index));
      const fehlend = [...Array(vorlage.shot_count).keys()].filter((i) => !indizes.has(i));
      if (fehlend.length > 0) {
        return Response.json(
          {
            fehler:
              `Das Template hat ${vorlage.shot_count} Einstellungen, ` +
              `fuer diese fehlt ein Clip: [${fehlend.join(', ')}]`,
          },
          { status: 400 },
        );
      }
    } else {
      clips = [...Array(vorlage.shot_count).keys()].map((index) => ({
        index,
        source: 'placeholder',
        uri: '',
      }));
    }

    const id = jobEinreihen(templateId, clips);
    console.log(
      `[jobs] eingereiht id=${id} template=${templateId} clips=${clips.length} ` +
        `nutzer=${wer.nutzer ?? '-'}`,
    );
    return Response.json({ id }, { status: 201 });
  } catch (fehler) {
    const text = fehler instanceof Error ? fehler.message : String(fehler);
    console.error(`[jobs] Anlegen gescheitert: ${text}`);
    return Response.json({ fehler: text }, { status: 400 });
  }
}
