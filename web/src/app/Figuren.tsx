'use client';

/**
 * Figuren: Personen, die über mehrere Einstellungen gleich aussehen sollen.
 *
 * WIE DIE KONSISTENZ ENTSTEHT, damit es nicht nach Magie aussieht: nicht
 * durch ein Modell, das sich erinnert — fal.ai erinnert sich zwischen zwei
 * Aufrufen an nichts. Sondern dadurch, dass jede Einstellung mit derselben
 * Figur DIESELBEN EINGABEN bekommt:
 *
 *   Referenzbild   der starke Hebel, über ein Bild-zu-Video-Modell
 *   Beschreibung   wandert VOR den Prompt der Einstellung
 *   Seed           dieselbe Zahl je Figur, im Worker aus der id abgeleitet
 *
 * Deshalb verlangt das Formular mindestens eines von beiden. Eine Figur, die
 * nur einen Namen trägt, würde nichts bewirken und wäre eine Zusage, die
 * nicht eingehalten wird.
 */

import { useRouter } from 'next/navigation';
import { useRef, useState } from 'react';

import { hochladen } from './hochladen';

export type FigurAnzeige = {
  id: string;
  name: string;
  beschreibung: string;
  referenz_uri: string;
};

export default function Figuren({ figuren }: { figuren: FigurAnzeige[] }) {
  const router = useRouter();
  const bild = useRef<HTMLInputElement>(null);
  const [name, setName] = useState('');
  const [beschreibung, setBeschreibung] = useState('');
  const [laeuft, setLaeuft] = useState(false);
  const [anteil, setAnteil] = useState<number | null>(null);
  const [meldung, setMeldung] = useState<string | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);

  async function anlegen() {
    setLaeuft(true);
    setMeldung(null);
    setFehler(null);

    try {
      if (name.trim() === '') throw new Error('Die Figur braucht einen Namen');

      const gewaehlt = bild.current?.files?.[0];
      let uploadId = '';
      if (gewaehlt) {
        setAnteil(0);
        const ergebnis = await hochladen(gewaehlt, setAnteil);
        setAnteil(null);
        uploadId = ergebnis.id;
      }

      if (!gewaehlt && beschreibung.trim() === '') {
        throw new Error(
          'Ohne Referenzbild und ohne Beschreibung bleibt die Figur wirkungslos. ' +
            'Das Bild ist der stärkere Hebel.',
        );
      }

      const antwort = await fetch('/api/figuren', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name.trim(),
          beschreibung: beschreibung.trim(),
          ...(uploadId ? { upload_id: uploadId } : {}),
        }),
      });
      const daten = (await antwort.json()) as { id?: string; fehler?: string };
      if (!antwort.ok || !daten.id) {
        throw new Error(daten.fehler ?? `Anlegen endete mit ${antwort.status}`);
      }

      setMeldung(
        `${name.trim()} angelegt${gewaehlt ? ', mit Referenzbild' : ', nur mit Beschreibung'}.`,
      );
      setName('');
      setBeschreibung('');
      if (bild.current) bild.current.value = '';
      router.refresh();
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
    } finally {
      setLaeuft(false);
      setAnteil(null);
    }
  }

  async function loeschen(id: string, wie: string) {
    setMeldung(null);
    setFehler(null);
    try {
      const antwort = await fetch(`/api/figuren?id=${encodeURIComponent(id)}`, {
        method: 'DELETE',
      });
      if (!antwort.ok) {
        const daten = (await antwort.json()) as { fehler?: string };
        throw new Error(daten.fehler ?? `Löschen endete mit ${antwort.status}`);
      }
      setMeldung(`${wie} gelöscht. Fertige Jobs behalten ihr Ergebnis.`);
      router.refresh();
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div>
      {figuren.length === 0 ? (
        <p className="leise">
          Noch keine Figur. Eine Figur sorgt dafür, dass dieselbe Person in mehreren
          Einstellungen gleich aussieht — fal.ai erinnert sich zwischen zwei Aufrufen
          an nichts, also bekommt jede Einstellung dieselben Eingaben.
        </p>
      ) : (
        <div className="tabelle">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Beschreibung</th>
                <th>Referenz</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {figuren.map((f) => (
                <tr key={f.id}>
                  <td>{f.name}</td>
                  <td className="leise">{f.beschreibung || '-'}</td>
                  <td className="leise">{f.referenz_uri ? 'Bild' : 'kein Bild'}</td>
                  <td>
                    <button
                      type="button"
                      className="sekundaer"
                      onClick={() => loeschen(f.id, f.name)}
                    >
                      löschen
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <label className="feld" htmlFor="figurname">
        <span>Name</span>
        <input
          id="figurname"
          type="text"
          value={name}
          disabled={laeuft}
          placeholder="z. B. Mara"
          onChange={(e) => setName(e.target.value)}
        />
      </label>

      <label className="feld" htmlFor="figurbeschreibung">
        <span>Beschreibung (wandert vor jede Einstellung mit dieser Figur)</span>
        <textarea
          id="figurbeschreibung"
          rows={3}
          value={beschreibung}
          disabled={laeuft}
          placeholder="Frau, Ende dreißig, dunkle Locken, graue Jacke, schmales Gesicht"
          onChange={(e) => setBeschreibung(e.target.value)}
        />
      </label>

      <label className="feld" htmlFor="figurbild">
        <span>Referenzbild (PNG, JPEG oder WebP) — der stärkere Hebel</span>
        <input
          id="figurbild"
          ref={bild}
          type="file"
          accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp"
          disabled={laeuft}
        />
      </label>

      <button type="button" className="sekundaer" onClick={anlegen} disabled={laeuft}>
        {laeuft ? 'laeuft' : 'Figur anlegen'}
      </button>

      {anteil !== null && (
        <div className="meldung">
          <p className="leise" style={{ margin: 0 }}>
            {Math.round(anteil * 100)} %
          </p>
          <progress value={anteil} max={1} aria-label="Upload des Referenzbilds" />
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
