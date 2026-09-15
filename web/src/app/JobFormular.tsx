'use client';

/**
 * Job anlegen, mit optionalem Upload.
 *
 * Der Upload laeuft ueber XMLHttpRequest und nicht ueber fetch: einen
 * Fortschritt beim Hochladen gibt es nur dort. `fetch` meldet erst, wenn
 * alles durch ist -- bei einer 400-MB-Datei also minutenlang nichts.
 *
 * Geschickt wird die Datei als ROHER Koerper (`xhr.send(datei)`), nicht als
 * multipart/form-data. Der Route Handler streamt sie damit ohne Parser und
 * ohne Puffer auf die Platte.
 */

import { useRouter } from 'next/navigation';
import { useRef, useState } from 'react';

type Vorlage = {
  id: string;
  name: string;
  shot_count: number;
  duration_ms: number;
};

type Hochgeladen = { uri: string; name: string; bytes: number };

function hochladen(
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
      let antwort: { uri?: string; name?: string; bytes?: number; fehler?: string } = {};
      try {
        antwort = JSON.parse(xhr.responseText);
      } catch {
        ablehnen(new Error(`Antwort ${xhr.status} war kein JSON`));
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300 && antwort.uri) {
        erfuellen({ uri: antwort.uri, name: antwort.name ?? datei.name, bytes: antwort.bytes ?? 0 });
      } else {
        ablehnen(new Error(antwort.fehler ?? `Upload endete mit ${xhr.status}`));
      }
    });

    xhr.addEventListener('error', () => ablehnen(new Error('Netzwerkfehler beim Upload')));
    xhr.addEventListener('abort', () => ablehnen(new Error('Upload abgebrochen')));

    xhr.send(datei);
  });
}

export default function JobFormular({ vorlagen }: { vorlagen: Vorlage[] }) {
  const router = useRouter();
  const dateien = useRef<HTMLInputElement>(null);
  const [vorlageId, setVorlageId] = useState(vorlagen[0]?.id ?? '');
  const [laeuft, setLaeuft] = useState(false);
  const [anteil, setAnteil] = useState<number | null>(null);
  const [wovon, setWovon] = useState('');
  const [meldung, setMeldung] = useState<string | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);

  const vorlage = vorlagen.find((v) => v.id === vorlageId);

  async function anlegen() {
    setLaeuft(true);
    setMeldung(null);
    setFehler(null);
    setAnteil(null);

    try {
      const gewaehlt = Array.from(dateien.current?.files ?? []);

      if (gewaehlt.length > 0 && vorlage && gewaehlt.length !== vorlage.shot_count) {
        throw new Error(
          `Das Template hat ${vorlage.shot_count} Einstellungen, gewaehlt sind ` +
            `${gewaehlt.length} Dateien. Entweder genau so viele, oder keine ` +
            `-- dann werden Platzhalter erzeugt.`,
        );
      }

      const clips: { index: number; source: string; uri: string }[] = [];
      for (const [i, datei] of gewaehlt.entries()) {
        setWovon(`${datei.name} (${i + 1} von ${gewaehlt.length})`);
        setAnteil(0);
        const ergebnis = await hochladen(datei, setAnteil);
        clips.push({ index: i, source: 'upload', uri: ergebnis.uri });
      }
      setAnteil(null);
      setWovon('');

      const antwort = await fetch('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          template_id: vorlageId,
          ...(clips.length > 0 ? { clips } : {}),
        }),
      });
      const daten = (await antwort.json()) as { id?: string; fehler?: string };
      if (!antwort.ok || !daten.id) {
        throw new Error(daten.fehler ?? `Anlegen endete mit ${antwort.status}`);
      }

      setMeldung(
        clips.length > 0
          ? `Job ${daten.id} eingereiht, ${clips.length} Clips hochgeladen.`
          : `Job ${daten.id} eingereiht, mit Platzhalter-Clips.`,
      );
      if (dateien.current) dateien.current.value = '';
      router.refresh();
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
    } finally {
      setLaeuft(false);
      setAnteil(null);
      setWovon('');
    }
  }

  return (
    <div>
      <label className="feld" htmlFor="vorlage">
        <span>Template</span>
        <select
          id="vorlage"
          value={vorlageId}
          onChange={(e) => setVorlageId(e.target.value)}
          disabled={laeuft}
        >
          {vorlagen.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name} — {v.shot_count} Einstellungen, {v.duration_ms} ms
            </option>
          ))}
        </select>
      </label>

      <label className="feld" htmlFor="clips">
        <span>
          Clips (MP4, optional
          {vorlage ? ` — genau ${vorlage.shot_count} oder keine` : ''})
        </span>
        <input
          id="clips"
          ref={dateien}
          type="file"
          accept="video/mp4,.mp4"
          multiple
          disabled={laeuft}
        />
      </label>

      <p className="leise">
        Ohne Dateien erzeugt der Worker Platzhalter. Damit laeuft die Pipeline ohne
        einen einzigen KI-Aufruf durch.
      </p>

      <button type="button" className="primaer" onClick={anlegen} disabled={laeuft || !vorlageId}>
        {laeuft ? 'laeuft' : 'Job anlegen'}
      </button>

      {anteil !== null && (
        <div className="meldung">
          <p className="leise" style={{ margin: 0 }}>
            {wovon} — {Math.round(anteil * 100)} %
          </p>
          <progress value={anteil} max={1} aria-label={`Upload ${wovon}`} />
        </div>
      )}

      {meldung && (
        <p className="meldung" role="status">
          {meldung}
        </p>
      )}
      {fehler && (
        <p className="meldung" role="alert">
          {fehler}
        </p>
      )}
    </div>
  );
}
