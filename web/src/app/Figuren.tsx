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
  // UNKONTROLLIERTE FELDER, MIT ABSICHT -- und aus einem echten Fehlschlag.
  //
  // Vorher waren das kontrollierte Felder (`value={name}` + onChange). Der
  // Browser hat das Feld mit dem Label "Name" per Autofill gefuellt: der Wert
  // stand im DOM, React-State blieb leer, und die Pruefung meldete "Die Figur
  // braucht einen Namen", waehrend "Kai" sichtbar im Feld stand. Autofill
  // loest kein React-onChange aus, und passiert es vor der Hydration, setzt
  // React den Wert auch nicht zurueck -- beide Seiten bleiben verschieden.
  //
  // Mit Refs ist das DOM die einzige Wahrheit. Damit kann keine Eingabe mehr
  // verlorengehen, egal ob sie getippt, eingefuegt oder ausgefuellt wurde.
  // autoComplete="off" kommt dazu, damit es gar nicht erst passiert -- aber
  // die Refs sind der Riegel, denn Chrome ignoriert das Attribut manchmal.
  const nameFeld = useRef<HTMLInputElement>(null);
  const beschreibungFeld = useRef<HTMLTextAreaElement>(null);
  const [laeuft, setLaeuft] = useState(false);
  const [anteil, setAnteil] = useState<number | null>(null);
  const [meldung, setMeldung] = useState<string | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);

  async function anlegen() {
    setLaeuft(true);
    setMeldung(null);
    setFehler(null);

    try {
      const name = (nameFeld.current?.value ?? '').trim();
      const beschreibung = (beschreibungFeld.current?.value ?? '').trim();

      if (name === '') throw new Error('Die Figur braucht einen Namen');

      const gewaehlt = bild.current?.files?.[0];
      let uploadId = '';
      if (gewaehlt) {
        setAnteil(0);
        const ergebnis = await hochladen(gewaehlt, setAnteil);
        setAnteil(null);
        uploadId = ergebnis.id;
      }

      if (!gewaehlt && beschreibung === '') {
        throw new Error(
          'Ohne Referenzbild und ohne Beschreibung bleibt die Figur wirkungslos. ' +
            'Das Bild ist der stärkere Hebel.',
        );
      }

      const antwort = await fetch('/api/figuren', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          beschreibung,
          ...(uploadId ? { upload_id: uploadId } : {}),
        }),
      });
      const daten = (await antwort.json()) as { id?: string; fehler?: string };
      if (!antwort.ok || !daten.id) {
        throw new Error(daten.fehler ?? `Anlegen endete mit ${antwort.status}`);
      }

      setMeldung(
        `${name} angelegt${gewaehlt ? ', mit Referenzbild' : ', nur mit Beschreibung'}.`,
      );
      if (nameFeld.current) nameFeld.current.value = '';
      if (beschreibungFeld.current) beschreibungFeld.current.value = '';
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
          ref={nameFeld}
          type="text"
          autoComplete="off"
          disabled={laeuft}
          placeholder="z. B. Mara"
        />
      </label>

      <label className="feld" htmlFor="figurbeschreibung">
        <span>Beschreibung (wandert vor jede Einstellung mit dieser Figur)</span>
        <textarea
          id="figurbeschreibung"
          ref={beschreibungFeld}
          rows={3}
          autoComplete="off"
          disabled={laeuft}
          placeholder="Frau, Ende dreißig, dunkle Locken, graue Jacke, schmales Gesicht"
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

      <details className="feld">
        <summary>Wie viele Fotos, und welche? — hier aufklappen</summary>

        <p>
          <strong>Ein Bild.</strong> Move übergibt dem Modell genau eine
          Referenz, also wird auch nur eine benutzt. Mehrere Fotos hochzuladen
          würde heute nichts verbessern; wähle stattdessen das eine beste aus.
        </p>

        <p className="leise">
          Das ist eine Eigenschaft des Wegs, nicht eine Meinung: pro Aufruf geht
          ein <code>image_url</code> an fal. Erst wenn ein Modell mehrere
          Referenzen annimmt, wird die Frage „wie viele" sinnvoll — und das ist
          bei uns noch nicht gemessen.
        </p>

        <p>
          <strong>Was das Bild zeigen soll</strong>
        </p>
        <ul>
          <li>Frontal oder leicht schräg, Gesicht scharf und gut ausgeleuchtet</li>
          <li>Augen sichtbar, neutraler Ausdruck, keine Sonnenbrille</li>
          <li>
            <strong>Nur diese eine Person</strong> im Bild. Auf einem Gruppenfoto
            kann das Modell nicht wissen, wer gemeint ist
          </li>
          <li>
            Frisur und Kleidung so, wie sie in jeder Einstellung wiederkehren
            sollen — was auf dem Bild ist, kommt mit
          </li>
          <li>Weiches, gleichmäßiges Licht; harte Schatten verdecken Merkmale</li>
          <li>Ohne Filter, ohne Bewegungsunschärfe, nicht am Kinn beschnitten</li>
        </ul>

        <p>
          <strong>Was schadet:</strong> Gruppenfotos, extreme Perspektiven von
          unten oder oben, Mützen und Brillen, die nicht wiederkehren sollen,
          starker Farbstich, sehr kleine oder stark komprimierte Bilder.
        </p>

        <p>
          <strong>Zu den Posen, und das ist der unbequeme Teil:</strong> viele
          Bild-zu-Video-Modelle benutzen die Referenz als <em>erstes Bild</em> des
          Clips. Trifft das zu, beginnt jede Einstellung mit derselben Pose, und
          acht Einstellungen sehen einander sehr ähnlich. Ein neutrales,
          mittiges Portrait ist dann die beste Wahl, und die Abwechslung kommt
          aus der Beschreibung je Einstellung — nicht aus dem Bild.
        </p>

        <p className="leise">
          Ob es wirklich als erstes Bild benutzt wird, ist bei uns{' '}
          <strong>nicht gemessen</strong>: fal.ai und seine Doku sind aus der
          Entwicklungsumgebung gesperrt. Der erste echte Auftrag klärt das.
          Sieht das Ergebnis danach aus, als würde die Referenz als Startbild
          eingesetzt, dann lohnt es, je Einstellung eine eigene Figur mit
          passendem Bild anzulegen — das geht heute schon, weil die Figur pro
          Einstellung gewählt wird.
        </p>

        <p>
          <strong>Die Beschreibung ist kein Ersatz, sondern die Ergänzung.</strong>{' '}
          Sie steht vor jedem Prompt dieser Figur. Nenne, was gleich bleiben
          soll: Geschlecht, geschätztes Alter, Haare, Gesichtsform, Kleidung.
          Lass weg, was sich je Einstellung ändert — Ort, Handlung, Tageszeit,
          Kameraeinstellung. Die Bildgröße hängt move ohnehin aus dem Template an.
        </p>
      </details>

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
