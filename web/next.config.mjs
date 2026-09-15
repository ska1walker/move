/** @type {import('next').NextConfig} */
const nextConfig = {
  // Das Image laeuft als standalone-Bundle. Der Startbefehl haengt davon ab:
  //   node --require ./server-zeitlimit.cjs server.js
  output: 'standalone',

  // Bewusst KEINE rewrites(). Ziele von rewrites() werden beim Build
  // eingebrannt und lassen sich zur Laufzeit nicht mehr aendern -- auf der
  // Box steht dann die Adresse der Buildumgebung im Image.
  //
  // Aus demselben Grund gibt es hier keine NEXT_PUBLIC_*-Werte. Alles, was
  // der Browser braucht, kommt ueber relative Pfade.

  poweredByHeader: false,
  reactStrictMode: true,

  // node:sqlite ist ein Builtin und darf nicht gebundelt werden.
  serverExternalPackages: ['node:sqlite'],
};

export default nextConfig;
