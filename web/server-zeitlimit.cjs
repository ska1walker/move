// Hebt Nodes Zeitlimit fuer eingehende Anfragen an.
//
// Node kappt eine Anfrage nach `server.requestTimeout`, Vorgabe 300 Sekunden.
// Next setzt keinen eigenen Wert und gibt auch keinen Weg, ihn zu setzen --
// das standalone-Bundle ruft `http.createServer` selbst auf.
//
// Beleg aus den Messungen: ein Upload mit 30 kB/s endete nach 329 Sekunden in
// HTTP 408. Fuer eine 400-MB-Datei ueber eine langsame Leitung ist das viel
// zu knapp.
//
// Deshalb wird `http.createServer` vor dem Start des Servers umhuellt:
//
//   node --require ./server-zeitlimit.cjs server.js
//
// Das `--require` laeuft vor `server.js`, der Patch sitzt also schon, wenn
// Next seinen Server anlegt.

'use strict';

const http = require('node:http');

const STUNDE_MS = 60 * 60 * 1000;

function zahlAusUmgebung(name, vorgabe) {
  const roh = process.env[name];
  if (roh === undefined || roh === '') return vorgabe;
  const wert = Number(roh);
  if (!Number.isFinite(wert) || wert < 0) {
    // Nicht still auf die Vorgabe zurueckfallen. Ein Tippfehler in der
    // Umgebung wuerde sonst erst beim abgebrochenen Upload auffallen.
    throw new Error(`${name}="${roh}" ist keine gueltige Zahl in Millisekunden`);
  }
  return wert;
}

const REQUEST_TIMEOUT_MS = zahlAusUmgebung('MOVE_REQUEST_TIMEOUT_MS', 2 * STUNDE_MS);

// Die Kopfzeilen sind in Millisekunden da oder gar nicht. Ein grosszuegiges
// Limit hier wuerde nur einen haengenden Socket laenger offen halten.
// Node verlangt, dass headersTimeout nicht ueber requestTimeout liegt.
const HEADERS_TIMEOUT_MS = Math.min(
  zahlAusUmgebung('MOVE_HEADERS_TIMEOUT_MS', 60_000),
  REQUEST_TIMEOUT_MS || Number.MAX_SAFE_INTEGER,
);

const original = http.createServer;

http.createServer = function createServer(...args) {
  const server = original.apply(this, args);
  server.requestTimeout = REQUEST_TIMEOUT_MS;
  server.headersTimeout = HEADERS_TIMEOUT_MS;
  // 0 = kein Zeitlimit auf einem untaetigen Socket. Das Zeitlimit der
  // Anfrage oben ist die Grenze, die zaehlt.
  server.timeout = 0;
  return server;
};

// Einmal beim Start ins Log, damit im Pod nachsehbar ist, was gilt -- eine
// Beschreibung ist kein Beleg.
console.log(
  `[server-zeitlimit] requestTimeout=${REQUEST_TIMEOUT_MS}ms ` +
    `headersTimeout=${HEADERS_TIMEOUT_MS}ms`,
);
