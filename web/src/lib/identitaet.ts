/**
 * Identitaet aus den Kopfzeilen lesen.
 *
 * Diese Funktion ist der Grund, warum es sie als eigene Datei gibt: die
 * Middleware laeuft NICHT auf dem Upload-Pfad (siehe middleware.ts), der
 * Upload-Route-Handler muss die Identitaet also selbst ziehen. Beide rufen
 * dieselbe Funktion auf. Zwei Kopien derselben Logik driften, und der
 * Upload-Pfad ist genau der, auf dem es niemandem auffaellt.
 *
 * WELCHE KOPFZEILE -- korrigiert nach docs/olares-learnings.md 3.2, dort auf
 * einer echten Box gemessen:
 *
 *   "Der Sidecar setzt X-Bfl-User NICHT. Gemessen ueber den Admin-Port
 *    (config_dump): kein request_headers_to_add; allowed_upstream_headers
 *    kennt nur authorization, proxy-authorization, remote-*, authelia-*.
 *    Nach oben kommt Remote-User. Das ist die verlaessliche Identitaet."
 *
 * Vorher stand hier `x-bfl-user`, aus der Konvention abgeleitet und als
 * ungeprueft markiert. Es war falsch, und zwar auf zwei Ebenen:
 *
 *   1. Der Kopf kommt am Pod gar nicht an -- die Identitaet waere immer
 *      `null` gewesen.
 *   2. Waere er angekommen, haette ihn der BROWSER behaupten koennen. Das
 *      Dokument ist da unmissverstaendlich: bei einem Dienst ohne Entrance
 *      "frei behauptbar", bei authLevel public "aus dem Internet
 *      faelschbar". Eine serverseitig gelesene Identitaet darf nicht das
 *      sein, was der Client von sich behauptet.
 *
 * Deshalb gibt es KEINEN Rueckfall auf x-bfl-user. Fehlt Remote-User, ist
 * die Identitaet unbekannt -- auf der Box ein Fehler, nicht ein Gast.
 *
 * MOVE_DEV_USER ersetzt den Kopf fuer die Entwicklung ohne Envoy. Das Chart
 * setzt die Variable nicht, auf der Box bleibt ein fehlender Kopf also ein
 * fehlender Kopf.
 *
 * MOVE_USER_HEADER bleibt der Notausgang: weicht die Messung auf der Box ab,
 * aendert sich ein Wert und nicht der Code.
 */

export const USER_HEADER = (process.env.MOVE_USER_HEADER ?? 'remote-user').toLowerCase();

/** Wird bewusst NICHT gelesen. Der Browser kann ihn setzen. */
export const NICHT_VERTRAUEN = 'x-bfl-user';

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

  // Nur fuer die Entwicklung ohne Envoy. Auf der Box nicht gesetzt.
  const dev = process.env.MOVE_DEV_USER;
  if (dev && dev.trim() !== '') {
    return { nutzer: dev.trim(), quelle: 'MOVE_DEV_USER' };
  }

  // Die Quelle sagt, WARUM nichts da ist -- und ob jemand es mit dem
  // ungenutzten Kopf versucht hat. Das gehoert ins Log: still geschluckt
  // verbirgt es genau die Verwechslung, die diese Datei behebt.
  const behauptet = headers.get(NICHT_VERTRAUEN);
  return {
    nutzer: null,
    quelle: behauptet ? `fehlt (${NICHT_VERTRAUEN} behauptet, ignoriert)` : 'fehlt',
  };
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
