/**
 * Identitaet aus den Kopfzeilen lesen.
 *
 * Diese Funktion ist der Grund, warum es sie als eigene Datei gibt: die
 * Middleware laeuft NICHT auf dem Upload-Pfad (siehe middleware.ts), der
 * Upload-Route-Handler muss die Identitaet also selbst ziehen. Beide rufen
 * dieselbe Funktion auf. Zwei Kopien derselben Logik driften, und der
 * Upload-Pfad ist genau der, auf dem es niemandem auffaellt.
 *
 * ACHTUNG, ungeprueft: Der Name der Kopfzeile stammt aus der Olares-
 * Konvention und ist auf der Box noch nicht nachgemessen. Eine Beschreibung
 * ist kein Beleg. Pruefen mit:
 *
 *   kubectl logs -n move-<nutzer> deploy/move
 *
 * und einem Handler, der die eingehenden Kopfzeilen protokolliert. Weicht
 * der Name ab, aendert sich nur MOVE_USER_HEADER.
 */

export const USER_HEADER = process.env.MOVE_USER_HEADER ?? 'x-bfl-user';

export type Identitaet = {
  /** Angemeldeter Nutzer, oder null wenn die Kopfzeile fehlt. */
  nutzer: string | null;
  /** Wie die Identitaet ermittelt wurde -- fuer das Log, nicht fuer Logik. */
  quelle: string;
};

export function identitaet(headers: Headers): Identitaet {
  const roh = headers.get(USER_HEADER);
  if (roh && roh.trim() !== '') {
    return { nutzer: roh.trim(), quelle: USER_HEADER };
  }
  return { nutzer: null, quelle: 'fehlt' };
}

/**
 * Kopfzeilen, die beim Weiterreichen einer Anfrage nicht mitgehen duerfen.
 *
 * Hop-by-hop-Kopfzeilen gelten nur fuer die eine Verbindung. `Expect:
 * 100-continue` weitergereicht bringt den naechsten Server dazu, auf ein
 * Weiter zu warten, das nie kommt.
 *
 * v0 reicht keinen Upload weiter -- er landet direkt auf der Platte. Die
 * Liste steht hier trotzdem, weil sie gebraucht wird, sobald der Worker
 * einmal ueber HTTP erreichbar waere, und weil sie dann gern vergessen wird.
 */
export const HOP_BY_HOP = [
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
  'expect',
] as const;

export function ohneHopByHop(headers: Headers): Record<string, string> {
  const raus: Record<string, string> = {};
  headers.forEach((wert, name) => {
    if (!HOP_BY_HOP.includes(name.toLowerCase() as (typeof HOP_BY_HOP)[number])) {
      raus[name] = wert;
    }
  });
  return raus;
}
