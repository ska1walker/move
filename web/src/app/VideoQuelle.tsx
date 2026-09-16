'use client';

/**
 * Ein Video hochladen und daraus ein Template gewinnen lassen.
 *
 * DAS IST DER SCHRITT, DER GEFEHLT HAT. Ohne ihn musste jemand
 * `move_worker extract` per `kubectl exec` im Worker-Pod tippen, um
 * ueberhaupt ein Template zu bekommen -- und ohne Template zeigt die Seite
 * nur einen Hinweistext, so wie es auf der Box aussah.
 *
 * Zwei Schritte, bewusst getrennt sichtbar: erst laeuft die Datei hoch (das
 * dauert und hat einen Fortschritt), dann wird ein Auftrag eingereiht (das
 * ist sofort). Gearbeitet wird danach im Worker -- ffmpeg und librosa liegen
 * dort, und zwischen den Pods gibt es keinen Aufruf, weil der Envoy-Sidecar
 * jeden eingehenden TCP-Verkehr abfaengt.
 */

import { useRouter } from 'next/navigation';
import { useRef, useState } from 'react';

import { hochladen } from './hochladen';

export default function VideoQuelle() {
  const router = useRouter();
  const datei = useRef<HTMLInputElement>(null);
  // Unkontrolliert mit Ref, aus demselben Grund wie in Figuren.tsx: ein Feld
  // mit dem Label "Name" ist ein Autofill-Ziel, und Autofill loest kein
  // React-onChange aus. Der Wert stand dann im DOM und der State war leer.
  const nameFeld = useRef<HTMLInputElement>(null);
  const [laeuft, setLaeuft] = useState(false);
  const [anteil, setAnteil] = useState<number | null>(null);
  const [meldung, setMeldung] = useState<string | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);

  async function los() {
    const gewaehlt = datei.current?.files?.[0];
    if (!gewaehlt) {
      setFehler('Keine Datei gewaehlt');
      return;
    }

    setLaeuft(true);
    setMeldung(null);
    setFehler(null);
    setAnteil(0);

    try {
      const upload = await hochladen(gewaehlt, setAnteil);
      setAnteil(null);

      const gewuenschterName = (nameFeld.current?.value ?? '').trim();
      const antwort = await fetch('/api/extractions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ upload_id: upload.id, name: gewuenschterName || upload.name }),
      });
      const daten = (await antwort.json()) as { id?: string; fehler?: string };
      if (!antwort.ok || !daten.id) {
        throw new Error(daten.fehler ?? `Einreihen endete mit ${antwort.status}`);
      }

      setMeldung(
        `${upload.name} liegt (${upload.bytes.toLocaleString('de-DE')} Bytes). ` +
          `Extraktion ${daten.id} eingereiht — der Worker holt sie in wenigen Sekunden.`,
      );
      if (datei.current) datei.current.value = '';
      if (nameFeld.current) nameFeld.current.value = '';
      router.refresh();
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
    } finally {
      setLaeuft(false);
      setAnteil(null);
    }
  }

  return (
    <div>
      <p className="leise">
        Ein MP4 hochladen. Der Worker sucht die Schnittzeitpunkte und das Beat-Grid
        und legt daraus ein Template ab. Das Video selbst wird nicht Teil des
        Ergebnisses — nur seine Zeitstempel.
      </p>

      <label className="feld" htmlFor="quellvideo">
        <span>Quellvideo (MP4)</span>
        <input id="quellvideo" ref={datei} type="file" accept="video/mp4,.mp4" disabled={laeuft} />
      </label>

      <label className="feld" htmlFor="templatename">
        <span>Name für das Template (leer: Dateiname)</span>
        <input
          id="templatename"
          ref={nameFeld}
          type="text"
          autoComplete="off"
          disabled={laeuft}
          placeholder="z. B. Trailer, harte Schnitte"
        />
      </label>

      <button type="button" className="primaer" onClick={los} disabled={laeuft}>
        {laeuft ? 'laeuft' : 'Hochladen und auswerten'}
      </button>

      {anteil !== null && (
        <div className="meldung">
          <p className="leise" style={{ margin: 0 }}>
            {Math.round(anteil * 100)} %
          </p>
          <progress value={anteil} max={1} aria-label="Upload des Quellvideos" />
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
