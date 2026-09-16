/**
 * Eine Datei hochladen, mit Fortschritt.
 *
 * ALS EIGENE DATEI, weil es jetzt drei Aufrufer gibt: Clips je Einstellung,
 * das Quellvideo fuer eine Extraktion und das Referenzbild einer Figur. Eine
 * Kopie je Aufrufer waere genau die Art Duplikat, das auseinanderdriftet --
 * und ein Upload, der bei 10 MB still abbricht, faellt in einer Kopie auf und
 * in den anderen nicht.
 *
 * XMLHttpRequest und nicht fetch: einen Fortschritt beim HOCHLADEN gibt es
 * nur dort. `fetch` meldet erst, wenn alles durch ist -- bei 400 MB also
 * minutenlang nichts.
 *
 * Geschickt wird die Datei als ROHER Koerper (`xhr.send(datei)`), nicht als
 * multipart/form-data. Der Route Handler streamt sie damit ohne Parser und
 * ohne Puffer auf die Platte; der Name kommt als Abfrageparameter.
 */

export type Hochgeladen = {
  /** id der Zeile in `upload`. Fuer Extraktion und Figur der Bezugspunkt. */
  id: string;
  /** Pfad relativ zum Datenverzeichnis. */
  uri: string;
  name: string;
  art: 'video' | 'bild';
  bytes: number;
};

export function hochladen(
  datei: File,
  aufFortschritt: (anteil: number) => void,
): Promise<Hochgeladen> {
  return new Promise((erfuellen, ablehnen) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/uploads?name=${encodeURIComponent(datei.name)}`);

    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) aufFortschritt(e.loaded / e.total);
    });

    xhr.addEventListener('load', () => {
      let antwort: Partial<Hochgeladen> & { fehler?: string } = {};
      try {
        antwort = JSON.parse(xhr.responseText);
      } catch {
        ablehnen(new Error(`Antwort ${xhr.status} war kein JSON`));
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300 && antwort.uri && antwort.id) {
        erfuellen({
          id: antwort.id,
          uri: antwort.uri,
          name: antwort.name ?? datei.name,
          art: antwort.art ?? 'video',
          bytes: antwort.bytes ?? 0,
        });
      } else {
        ablehnen(new Error(antwort.fehler ?? `Upload endete mit ${xhr.status}`));
      }
    });

    xhr.addEventListener('error', () => ablehnen(new Error('Netzwerkfehler beim Upload')));
    xhr.addEventListener('abort', () => ablehnen(new Error('Upload abgebrochen')));

    xhr.send(datei);
  });
}
