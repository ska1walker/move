/**
 * Upload-Annahme. Die teuerste Stelle des Projekts.
 *
 * Vier gemessene Fallen, und wie sie hier vermieden werden:
 *
 * 1. NEXT-MIDDLEWARE KLONT DEN KOERPER.
 *    Passt die Middleware auf einen Pfad, beendet Next 15.5 bei
 *    `middlewareClientMaxBodySize` (Vorgabe 10 MB) BEIDE Stroeme. Das Anheben
 *    der Grenze half nicht. Deshalb nimmt der Matcher in `middleware.ts`
 *    `/api/uploads` aus, und die Identitaet kommt hier aus derselben Funktion
 *    (`identitaet`), die die Middleware benutzt -- nicht aus einer Kopie.
 *
 * 2. NICHT PUFFERN.
 *    `request.formData()` und `request.arrayBuffer()` ziehen die ganze Datei
 *    in den Speicher. Gemessen bei 500 MB: `fetch`/undici +457-526 MB RSS
 *    gegenueber +57-85 MB mit `Readable.fromWeb` -> `pipeline`. Deshalb wird
 *    der rohe Koerper gestreamt und nie als Ganzes angefasst.
 *
 *    Auch kein multipart/form-data: ein Parser dafuer puffert oder bringt
 *    eine Abhaengigkeit mit. Der Browser schickt die Datei als rohen Koerper
 *    (`xhr.send(file)`), der Name kommt als Abfrageparameter. Nebeneffekt:
 *    Fortschritt gibt es nur ueber XMLHttpRequest, und der funktioniert damit
 *    ohnehin.
 *
 *    v0 reicht nichts weiter, die Datei landet direkt auf der Platte. Die
 *    Regel "ausgehend nie mit fetch streamen" ist damit gar nicht anwendbar;
 *    wenn das einmal noetig wird, steht in `identitaet.ts` schon die Liste
 *    der Hop-by-hop-Kopfzeilen, die dabei weg muessen.
 *
 * 3. NODE KAPPT NACH 300 SEKUNDEN.
 *    `server.requestTimeout`, Vorgabe 300 s, Beleg: Upload mit 30 kB/s endete
 *    nach 329 s in HTTP 408. Angehoben wird das nicht hier, sondern beim
 *    Start des Servers -- siehe `server-zeitlimit.cjs`.
 *
 * 4. KEINE EINGEBRANNTEN ADRESSEN.
 *    Der Browser spricht diesen Pfad relativ an. Es gibt kein
 *    NEXT_PUBLIC_API_URL und kein rewrites(), die beim Build festgeschrieben
 *    wuerden.
 *
 * Dazu, weil es sonst niemandem auffaellt: geschrieben wird nach `<id>.teil`
 * und erst am Ende umbenannt. Ein abgebrochener Upload darf keine halbe Datei
 * hinterlassen, die aussieht wie eine ganze.
 */

import { createWriteStream } from 'node:fs';
import { mkdir, rename, unlink } from 'node:fs/promises';
import { dirname, extname, join } from 'node:path';
import { Readable, Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';

import { datenVerzeichnis, neueId, uploadAblegen, type UploadArt } from '@/lib/daten';
import { identitaet } from '@/lib/identitaet';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * Was angenommen wird, und woran man es im Strom erkennt.
 *
 * Die Kennung wird in den ERSTEN Bytes geprueft, nicht nach dem Schreiben:
 * eine Pruefung danach haette die Platte schon vollgeschrieben. Die Endung
 * allein reicht nicht -- sie steht im Abfrageparameter und kommt damit vom
 * Browser.
 *
 * Bilder sind dazugekommen, weil eine Figur ein Referenzbild braucht: das ist
 * der starke Hebel fuer konsistente Personen, und ohne Bild bleibt es beim
 * Zufall, den ein Modell je Aufruf neu wuerfelt.
 */
type Kennung = {
  art: UploadArt;
  /** Wieviele Bytes gebraucht werden, um zu entscheiden. */
  mindestens: number;
  passt: (kopf: Buffer) => boolean;
  beschreibung: string;
};

const ANNAHME: Record<string, Kennung> = {
  '.mp4': {
    art: 'video',
    mindestens: 12,
    // MP4 traegt an Byte 4 die Kennung 'ftyp'.
    passt: (k) => k.subarray(4, 8).toString('latin1') === 'ftyp',
    beschreibung: "'ftyp' an Byte 4",
  },
  '.png': {
    art: 'bild',
    mindestens: 8,
    passt: (k) => k.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])),
    beschreibung: 'PNG-Signatur',
  },
  '.jpg': {
    art: 'bild',
    mindestens: 3,
    passt: (k) => k[0] === 0xff && k[1] === 0xd8 && k[2] === 0xff,
    beschreibung: 'JPEG-Signatur ff d8 ff',
  },
  '.jpeg': {
    art: 'bild',
    mindestens: 3,
    passt: (k) => k[0] === 0xff && k[1] === 0xd8 && k[2] === 0xff,
    beschreibung: 'JPEG-Signatur ff d8 ff',
  },
  '.webp': {
    art: 'bild',
    mindestens: 12,
    passt: (k) =>
      k.subarray(0, 4).toString('latin1') === 'RIFF' &&
      k.subarray(8, 12).toString('latin1') === 'WEBP',
    beschreibung: "'RIFF' und 'WEBP' im Kopf",
  },
};

const ERLAUBTE_ENDUNGEN = Object.keys(ANNAHME);

/**
 * Bilder duerfen deutlich kleiner sein als Videos.
 *
 * Ein Referenzbild ist ein Portrait, keine Filmdatei. Die grosse Grenze fuer
 * ein Bild hiesse, dass ein Fehlgriff erst nach einem Gigabyte auffaellt.
 */
const MAX_BILD_BYTES = Number(process.env.MOVE_MAX_BILD_BYTES ?? 32 * 1024 * 1024);

/** Obergrenze. 0 schaltet die Pruefung ab. */
const MAX_BYTES = Number(process.env.MOVE_MAX_UPLOAD_BYTES ?? 4 * 1024 * 1024 * 1024);

class UploadFehler extends Error {
  constructor(
    readonly status: number,
    nachricht: string,
  ) {
    super(nachricht);
  }
}

function sauberName(roh: string): { name: string; endung: string; kennung: Kennung } {
  // Kein Pfad, keine Traversierung. Der Name wird ohnehin nicht als
  // Dateiname benutzt -- die Datei heisst nach ihrer id -- aber er landet in
  // der Antwort und spaeter in der Oberflaeche.
  const nur = roh.split(/[/\\]/).pop() ?? '';
  if (nur === '' || nur === '.' || nur === '..') {
    throw new UploadFehler(400, 'Parameter name fehlt oder ist kein Dateiname');
  }
  const endung = extname(nur).toLowerCase();
  const kennung = ANNAHME[endung];
  if (!kennung) {
    throw new UploadFehler(
      415,
      `Endung ${endung || '(keine)'} wird nicht angenommen. Erlaubt: ${ERLAUBTE_ENDUNGEN.join(', ')}`,
    );
  }
  return { name: nur.slice(0, 200), endung, kennung };
}

/**
 * Zaehlt die Bytes im Durchlauf und sieht sich die ersten Bytes an.
 *
 * Beides im Strom, nicht danach: eine Groessenpruefung nach dem Schreiben
 * haette die Platte schon vollgeschrieben.
 */
function pruefStrom(kennung: Kennung, endung: string, grenze: number): Transform {
  let gezaehlt = 0;
  let kopf: Buffer | null = Buffer.alloc(0);

  return new Transform({
    transform(stueck: Buffer, _kodierung, weiter) {
      gezaehlt += stueck.length;
      if (grenze > 0 && gezaehlt > grenze) {
        weiter(new UploadFehler(413, `Datei ist groesser als ${grenze} Bytes`));
        return;
      }

      if (kopf !== null) {
        kopf = Buffer.concat([kopf, stueck]);
        if (kopf.length >= kennung.mindestens) {
          // Kein vollstaendiger Test, aber er faengt die verwechselte Datei
          // sofort statt erst im Worker -- oder, bei einem Referenzbild,
          // erst nach einem bezahlten fal-Aufruf.
          const geprueft = kennung.passt(kopf);
          kopf = null;
          if (!geprueft) {
            weiter(
              new UploadFehler(
                415,
                `Passt nicht zu ${endung}: erwartet war ${kennung.beschreibung}`,
              ),
            );
            return;
          }
        }
      }

      weiter(null, stueck);
    },
  });
}

export async function POST(request: Request) {
  const wer = identitaet(request.headers);
  const beginn = Date.now();

  try {
    const { name, endung, kennung } = sauberName(
      new URL(request.url).searchParams.get('name') ?? '',
    );
    const grenze = kennung.art === 'bild' ? MAX_BILD_BYTES : MAX_BYTES;

    if (!request.body) {
      throw new UploadFehler(400, 'Kein Anfragekoerper. Die Datei gehoert roh in den Koerper.');
    }

    // Wenn die Groesse angekuendigt wird, hier ablehnen statt erst nach
    // einem Gigabyte.
    const angekuendigt = Number(request.headers.get('content-length') ?? '0');
    if (grenze > 0 && angekuendigt > grenze) {
      throw new UploadFehler(
        413,
        `Angekuendigte Groesse ${angekuendigt} Bytes ueberschreitet ${grenze}`,
      );
    }

    const id = neueId();
    const ziel = join(datenVerzeichnis(), 'uploads', `${id}${endung}`);
    const teil = `${ziel}.teil`;
    await mkdir(dirname(ziel), { recursive: true });

    let bytes = 0;
    const zaehler = new Transform({
      transform(stueck: Buffer, _k, weiter) {
        bytes += stueck.length;
        weiter(null, stueck);
      },
    });

    try {
      await pipeline(
        Readable.fromWeb(request.body as import('node:stream/web').ReadableStream),
        pruefStrom(kennung, endung, grenze),
        zaehler,
        createWriteStream(teil),
      );

      // RIEGEL GEGEN STILLES ABSCHNEIDEN.
      //
      // Gemessen mit Next 15.5.25: liegt dieser Pfad im Matcher der
      // Middleware, endet der Strom bei rund 10 MB -- und zwar SAUBER. Die
      // Pipeline meldet keinen Fehler, der Handler kaeme mit HTTP 200
      // zurueck, und aus einer 19,1-MB-Datei lagen 10.449.920 Bytes auf der
      // Platte. Eine Datei, die vollstaendig aussieht und es nicht ist.
      //
      // Deshalb wird die angekuendigte Groesse gegen die geschriebene
      // gehalten. Der Matcher ist der eine bekannte Grund; jeder andere,
      // der einen Upload abschneidet, faellt hier genauso auf.
      if (angekuendigt > 0 && bytes !== angekuendigt) {
        throw new UploadFehler(
          400,
          `Unvollstaendig: angekuendigt waren ${angekuendigt} Bytes, angekommen sind ${bytes}. ` +
            `Haeufigster Grund: /api/uploads liegt im Matcher der Middleware -- ` +
            `dann kappt Next 15.5 den Strom bei rund 10 MB, ohne einen Fehler zu melden.`,
        );
      }

      await rename(teil, ziel);
    } catch (fehler) {
      await unlink(teil).catch(() => {});
      throw fehler;
    }

    // ERST NACH DEM UMBENENNEN in die Datenbank. Eine Zeile, die auf eine
    // `.teil`-Datei zeigt, waere ein Upload, den die Oberflaeche anbietet und
    // der Worker nicht findet.
    const uri = `uploads/${id}${endung}`;
    const uploadId = uploadAblegen(name, uri, kennung.art, bytes);

    const dauer = Date.now() - beginn;
    console.log(
      `[upload] ok nutzer=${wer.nutzer ?? '-'} name=${name} art=${kennung.art} ` +
        `bytes=${bytes} ms=${dauer} ` +
        `rssMb=${Math.round(process.memoryUsage().rss / 1024 / 1024)}`,
    );

    return Response.json({
      id: uploadId,
      uri,
      name,
      art: kennung.art,
      bytes,
      dauer_ms: dauer,
    });
  } catch (fehler) {
    // Mit Metadaten. Ein still geschluckter Upload-Fehler verbirgt genau die
    // Plattformfehler, um die es hier geht.
    const status = fehler instanceof UploadFehler ? fehler.status : 500;
    const text = fehler instanceof Error ? fehler.message : String(fehler);
    console.error(
      `[upload] gescheitert status=${status} nutzer=${wer.nutzer ?? '-'} ` +
        `ms=${Date.now() - beginn} grund=${text}`,
    );
    return Response.json({ fehler: text }, { status });
  }
}
