import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

import { identitaet } from '@/lib/identitaet';

/**
 * DER UPLOAD-PFAD DARF HIER NICHT HINEIN.
 *
 * Next 15.5 klont den Anfragekoerper, sobald eine Middleware auf den Pfad
 * passt. Ueberschreitet er `middlewareClientMaxBodySize` (Vorgabe 10 MB),
 * beendet Next BEIDE Stroeme -- auch den weitergereichten, den die Middleware
 * gar nicht anfasst. Das Anheben der Grenze half nicht.
 *
 * Deshalb nimmt der Matcher `/api/uploads` aus. Der Route Handler dort zieht
 * die Identitaet mit derselben Funktion (`identitaet`), die auch hier
 * benutzt wird -- nicht mit einer zweiten Kopie.
 *
 * Wer diesen Matcher anfasst, misst danach einen Upload ueber 10 MB.
 */
export const config = {
  matcher: [
    '/((?!api/uploads|_next/static|_next/image|fonts/|favicon.ico).*)',
  ],
};

export function middleware(request: NextRequest) {
  const wer = identitaet(request.headers);

  const antwort = NextResponse.next();
  // Nur zur Nachvollziehbarkeit im Browser-Netzwerkreiter. Keine Logik haengt
  // daran; die Autorisierung macht der Envoy-Sidecar vor uns.
  antwort.headers.set('x-move-identitaet', wer.quelle);
  return antwort;
}
