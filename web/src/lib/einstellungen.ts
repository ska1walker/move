/**
 * Was die Umgebung ueber die Generierung sagt.
 *
 * MOVE_FAL_BEREIT ist die Ja/Nein-Auskunft aus dem Chart, NICHT der
 * Schluessel: der liegt nur am Worker. Das Web muss wissen, ob eine
 * Generierung moeglich ist -- es muss sie nicht selbst ausloesen koennen.
 *
 * Als eigene Datei, damit API und Oberflaeche dieselbe Funktion lesen. Zwei
 * Auslegungen derselben Variablen driften, und die Stelle, an der es
 * auffaellt, ist ein bezahlter Aufruf.
 */

export function falBereit(): boolean {
  return (process.env.MOVE_FAL_BEREIT ?? '').trim().toLowerCase() === 'true';
}
