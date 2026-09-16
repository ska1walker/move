'use client';

/**
 * Job anlegen. Drei Wege zu den Clips:
 *
 *   Platzhalter   der Worker erzeugt Flaechen mit Index und Timecode.
 *                 Kostet nichts, laeuft ohne einen einzigen KI-Aufruf.
 *   Dateien       eigene MP4s, eine je Einstellung.
 *   fal.ai        erzeugen lassen, eine Beschreibung je Einstellung.
 *
 * Bei fal.ai kann je Einstellung eine FIGUR gewaehlt werden. Dann wandern
 * Beschreibung, Referenzbild und Seed dieser Figur in genau diese Einstellung
 * ein -- und in jede andere, die dieselbe Figur nennt. Das ist der ganze
 * Mechanismus hinter konsistenten Personen: nicht ein Modell, das sich
 * erinnert, sondern dieselben Eingaben, jedes Mal.
 *
 * Der Upload liegt in `hochladen.ts` und nicht hier: es gibt inzwischen drei
 * Aufrufer (Clips, Quellvideo, Referenzbild), und eine Kopie je Aufrufer
 * waere genau das Duplikat, das auseinanderdriftet.
 */

import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

import { hochladen } from './hochladen';

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

export default function JobFormular({
  vorlagen,
  falBereit,
  figuren,
}: {
  vorlagen: Vorlage[];
  /** Ob am Worker ein fal-Schluessel hinterlegt ist. Kommt aus dem Chart
   *  (MOVE_FAL_BEREIT) -- der Schluessel selbst ist hier nie sichtbar. */
  falBereit: boolean;
  /** Angelegte Figuren, zur Wahl je Einstellung. */
  figuren: { id: string; name: string; referenz_uri: string }[];
}) {
  const router = useRouter();
  const dateien = useRef<HTMLInputElement>(null);
  const [vorlageId, setVorlageId] = useState(vorlagen[0]?.id ?? '');
  const [quelle, setQuelle] = useState<Quelle>('placeholder');
  const [prompts, setPrompts] = useState<string[]>([]);
  // Figur je Einstellung. Leer heisst: keine, dann entscheidet allein der
  // Prompt -- und die Person sieht in jeder Einstellung anders aus.
  const [figurIds, setFigurIds] = useState<string[]>([]);
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
    setFigurIds((alt) =>
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

      let clips: {
        index: number;
        source: Quelle;
        uri?: string;
        prompt?: string;
        model?: string;
        figur_id?: string;
      }[] = [];

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
          figur_id: figurIds[i] ?? '',
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
            {figuren.length > 0
              ? ' Dieselbe Figur in mehreren Einstellungen lässt die Person gleich aussehen: ' +
                'ihre Beschreibung, ihr Referenzbild und ihr Seed gehen dann in jede dieser ' +
                'Einstellungen ein.'
              : ' Für eine Person, die in mehreren Einstellungen gleich aussieht, zuerst unten ' +
                'eine Figur anlegen — sonst würfelt das Modell jede Einstellung neu.'}
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

              {figuren.length > 0 && (
                <label className="feld" htmlFor={`figur-${e.index}`}>
                  <span className="leise">Figur in dieser Einstellung</span>
                  <select
                    id={`figur-${e.index}`}
                    value={figurIds[e.index] ?? ''}
                    disabled={laeuft}
                    onChange={(ev) =>
                      setFigurIds((alt) => {
                        const neu = [...alt];
                        neu[e.index] = ev.target.value;
                        return neu;
                      })
                    }
                  >
                    <option value="">keine</option>
                    {figuren.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.name}
                        {f.referenz_uri ? ' (mit Bild)' : ' (nur Beschreibung)'}
                      </option>
                    ))}
                  </select>
                </label>
              )}
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
