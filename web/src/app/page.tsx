/**
 * Die eine Ansicht: Templates, Job anlegen, Ergebnis abspielen.
 *
 * Diese Server-Komponente liest DIREKT aus dem Repository-Modul und ruft
 * nicht die eigene JSON-API ueber HTTP. Das ist keine Bequemlichkeit: der
 * Envoy-Sidecar faengt jeden eingehenden TCP-Verkehr ab, auch den vom eigenen
 * Pod. Ein `fetch` auf die eigene Entrance-Adresse endet in
 * 401 ext_authz_denied.
 */

import { jobs, templates } from '@/lib/daten';

import JobFormular from './JobFormular';

export const dynamic = 'force-dynamic';

function zeit(ms: number | null): string {
  if (!ms) return '-';
  return new Date(ms).toISOString().replace('T', ' ').slice(0, 19);
}

function dauer(job: { started_at: number | null; finished_at: number | null }): string {
  if (!job.started_at || !job.finished_at) return '-';
  return `${((job.finished_at - job.started_at) / 1000).toFixed(1)} s`;
}

export default function Seite() {
  let vorlagen: ReturnType<typeof templates> = [];
  let liste: ReturnType<typeof jobs> = [];
  let fehler: string | null = null;

  try {
    vorlagen = templates();
    liste = jobs(25);
  } catch (e) {
    fehler = e instanceof Error ? e.message : String(e);
  }

  const fertig = liste.filter((j) => j.status === 'done');

  return (
    <main className="huelle">
      <header className="kopf">
        <h1>Move</h1>
        <p>Schnitt-Templates als Zeitstempel</p>
      </header>

      {fehler && (
        <div className="block">
          <h2>Datenbank nicht erreichbar</h2>
          <pre className="leise">{fehler}</pre>
        </div>
      )}

      <section className="block">
        <h2>Job anlegen</h2>
        {vorlagen.length === 0 ? (
          <p className="leise">
            Noch kein Template abgelegt. In v0 legt der Worker eines ab:{' '}
            <code>python3 -m move_worker enqueue --template examples/beat-8s.json</code>
          </p>
        ) : (
          <JobFormular
            vorlagen={vorlagen.map((v) => ({
              id: v.id,
              name: v.name,
              shot_count: v.shot_count,
              duration_ms: v.duration_ms,
            }))}
          />
        )}
      </section>

      <section className="block">
        <h2>Templates</h2>
        {vorlagen.length === 0 ? (
          <p className="leise">keine</p>
        ) : (
          <div className="tabelle">
            <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Einstellungen</th>
                <th>Laufzeit</th>
                <th>Quelle</th>
              </tr>
            </thead>
            <tbody>
              {vorlagen.map((v) => (
                <tr key={v.id}>
                  <td>{v.name}</td>
                  <td className="mono">{v.shot_count}</td>
                  <td className="mono">{v.duration_ms} ms</td>
                  <td className="leise">{v.source_label || '-'}</td>
                </tr>
              ))}
            </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="block">
        <h2>Jobs</h2>
        {liste.length === 0 ? (
          <p className="leise">keine</p>
        ) : (
          <div className="tabelle">
            <table>
            <thead>
              <tr>
                <th>Stand</th>
                <th>Angelegt</th>
                <th>Dauer</th>
                <th>Clips</th>
                <th>Ergebnis</th>
              </tr>
            </thead>
            <tbody>
              {liste.map((j) => (
                <tr key={j.id}>
                  <td className={`stand stand-${j.status}`}>{j.status}</td>
                  <td className="mono leise">{zeit(j.created_at)}</td>
                  <td className="mono">{dauer(j)}</td>
                  <td className="mono">{j.clips.length}</td>
                  <td>
                    {j.status === 'done' ? (
                      <a href={`/api/renders/${j.id}`}>MP4</a>
                    ) : j.status === 'failed' ? (
                      <span className="leise">{(j.error ?? '').split('\n')[0]}</span>
                    ) : (
                      <span className="leise">-</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
            </table>
          </div>
        )}
      </section>

      {fertig.length > 0 && (
        <section className="block">
          <h2>Letztes Ergebnis</h2>
          {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
          <video controls preload="metadata" src={`/api/renders/${fertig[0].id}`}>
            Das Video kann in diesem Browser nicht abgespielt werden.{' '}
            <a href={`/api/renders/${fertig[0].id}`}>MP4 herunterladen</a>
          </video>
        </section>
      )}
    </main>
  );
}
