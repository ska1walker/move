'use client';

/**
 * Job anlegen. Drei Wege zu den Clips:
 *
 *   Platzhalter   der Worker erzeugt Flaechen mit Index und Timecode.
 *                 Kostet nichts, laeuft ohne einen einzigen KI-Aufruf.
 *   Dateien       eigene MP4s, eine je Einstellung.
 *   fal.ai        erzeugen lassen, eine Beschreibung je Einstellung.
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
import { useEffect, useRef, useState } from 'react';

type Einstellung = {
  index: number;
  at_ms: number;
  dauer_ms: number;
  shot_scale: string;
};

type Vorlage = {
  id: string;
  name: string;
  shot_count: number;
  duration_ms: number;
  einstellungen: Einstellung[];
};

type Quelle = 'placeholder' | 'upload' | 'fal';

type Hochgeladen = { uri: string; name: string; bytes: number };

function hochladen(datei: File, aufFortschritt: (anteil: number) => void): Promise<Hochgeladen> {
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

export default function JobFormular({
  vorlagen,
  falBereit,
}: {
  vorlagen: Vorlage[];
  /** Ob am Worker ein fal-Schluessel hinterlegt ist. Kommt aus dem Chart
   *  (MOVE_FAL_BEREIT) -- der Schluessel selbst ist hier nie sichtbar. */
  falBereit: boolean;
}) {
  const router = useRouter();
  const dateien = useRef<HTMLInputElement>(null);
  const [vorlageId, setVorlageId] = useState(vorlagen[0]?.id ?? '');
  const [quelle, setQuelle] = useState<Quelle>('placeholder');
  const [prompts, setPrompts] = useState<string[]>([]);
  const [model, setModel] = useState('');
  const [laeuft, setLaeuft] = useState(false);
  const [anteil, setAnteil] = useState<number | null>(null);
  const [wovon, setWovon] = useState('');
  const [meldung, setMeldung] = useState<string | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);

  const vorlage = vorlagen.find((v) => v.id === vorlageId);

  // Ein Prompt-Feld je Einstellung. Bei einem Wechsel des Templates werden
  // vorhandene Texte behalten, soweit es sie noch gibt.
  useEffect(() => {
    if (!vorlage) return;
    setPrompts((alt) =>
      Array.from({ length: vorlage.shot_count }, (_, i) => alt[i] ?? ''),
    );
  }, [vorlage]);

  const offenePrompts = prompts.filter((p) => p.trim() === '').length;
  const bereit =
    vorlageId !== '' && !laeuft && (quelle !== 'fal' || offenePrompts === 0);

  async function anlegen() {
    setLaeuft(true);
    setMeldung(null);
    setFehler(null);
    setAnteil(null);

    try {
      if (!vorlage) throw new Error('Kein Template gewaehlt');

      let clips: { index: number; source: Quelle; uri?: string; prompt?: string; model?: string }[] =
        [];

      if (quelle === 'upload') {
        const gewaehlt = Array.from(dateien.current?.files ?? []);
        if (gewaehlt.length !== vorlage.shot_count) {
          throw new Error(
            `Das Template hat ${vorlage.shot_count} Einstellungen, gewaehlt sind ` +
              `${gewaehlt.length} Dateien.`,
          );
        }
        for (const [i, datei] of gewaehlt.entries()) {
          setWovon(`${datei.name} (${i + 1} von ${gewaehlt.length})`);
          setAnteil(0);
          const ergebnis = await hochladen(datei, setAnteil);
          clips.push({ index: i, source: 'upload', uri: ergebnis.uri });
        }
        setAnteil(null);
        setWovon('');
      } else if (quelle === 'fal') {
        clips = prompts.map((p, i) => ({
          index: i,
          source: 'fal' as const,
          prompt: p.trim(),
          model: model.trim(),
        }));
      }

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
        quelle === 'fal'
          ? `Job ${daten.id} eingereiht. ${clips.length} Clips werden bei fal erzeugt.`
          : quelle === 'upload'
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

      <fieldset className="wahl" disabled={laeuft}>
        <legend>Woher kommen die Clips</legend>
        {(
          [
            ['placeholder', 'Platzhalter', 'kostet nichts, ohne einen einzigen KI-Aufruf'],
            ['upload', 'Eigene Dateien', `genau ${vorlage?.shot_count ?? 0} MP4`],
            [
              'fal',
              'Von fal.ai erzeugen',
              falBereit
                ? 'eine Beschreibung je Einstellung'
                : 'kein Schlüssel hinterlegt — bei der Installation unter FAL_KEY',
            ],
          ] as [Quelle, string, string][]
        ).map(([wert, titel, hinweis]) => (
          <label key={wert} className="wahl-zeile">
            <input
              type="radio"
              name="quelle"
              value={wert}
              checked={quelle === wert}
              disabled={wert === 'fal' && !falBereit}
              onChange={() => setQuelle(wert)}
            />
            <span>
              {titel} <span className="leise">— {hinweis}</span>
            </span>
          </label>
        ))}
      </fieldset>

      {quelle === 'upload' && (
        <label className="feld" htmlFor="clips">
          <span>Clips (MP4, genau {vorlage?.shot_count ?? 0})</span>
          <input id="clips" ref={dateien} type="file" accept="video/mp4,.mp4" multiple />
        </label>
      )}

      {quelle === 'fal' && vorlage && (
        <div className="feld">
          <span className="leise">
            Je Einstellung eine Beschreibung. Die Bildgröße aus dem Template wird
            angehängt.
          </span>

          {vorlage.einstellungen.map((e) => (
            <div key={e.index} className="einstellung">
              <label htmlFor={`prompt-${e.index}`} className="einstellung-kopf">
                <span className="mono">
                  {String(e.index).padStart(2, '0')}
                </span>
                <span className="leise">
                  {e.dauer_ms} ms · {e.shot_scale}
                </span>
              </label>
              <textarea
                id={`prompt-${e.index}`}
                rows={2}
                value={prompts[e.index] ?? ''}
                placeholder="was in dieser Einstellung zu sehen ist"
                disabled={laeuft}
                onChange={(ev) =>
                  setPrompts((alt) => {
                    const neu = [...alt];
                    neu[e.index] = ev.target.value;
                    return neu;
                  })
                }
              />
            </div>
          ))}

          <label className="feld" htmlFor="model">
            <span>Modell (leer: Vorgabe des Workers)</span>
            <input
              id="model"
              type="text"
              value={model}
              disabled={laeuft}
              placeholder="fal-ai/…"
              onChange={(ev) => setModel(ev.target.value)}
            />
          </label>

          {/* Jeder Aufruf wird abgerechnet. Das gehoert vor den Knopf, nicht
              in die Rechnung. */}
          <p className="meldung" role="note">
            {vorlage.shot_count} Aufrufe bei fal.ai, jeder wird über deinen
            Schlüssel abgerechnet.
            {offenePrompts > 0 && ` Noch ${offenePrompts} ohne Beschreibung.`}
          </p>
        </div>
      )}

      {quelle === 'placeholder' && (
        <p className="leise">
          Der Worker erzeugt Flächen mit Index und Timecode. Damit läuft die Pipeline
          ohne einen einzigen KI-Aufruf durch.
        </p>
      )}

      <button type="button" className="primaer" onClick={anlegen} disabled={!bereit}>
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
