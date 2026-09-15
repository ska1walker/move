# move (Frontend)
#
# BAUKONTEXT IST DAS REPO-WURZELVERZEICHNIS, nicht web/:
#
#   docker build -f web/Dockerfile -t move:26.9.1 .
#
# Grund: db/schema.sql liegt ausserhalb von web/ und wird von Web und Worker
# gemeinsam gelesen. Zwei Kopien des Schemas in zwei Sprachen driften still
# auseinander.
#
# Der Tag wird beim Bauen von aussen gesetzt und muss der Chart-AppVersion
# entsprechen -- das Chart leitet ihn aus .Chart.AppVersion ab.

FROM node:22-slim AS build

WORKDIR /build

COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY web/ ./

# NEXT_PUBLIC_* und rewrites()-Ziele werden hier EINGEBRANNT und lassen sich
# zur Laufzeit nicht mehr aendern. Deshalb wird hier bewusst nichts davon
# gesetzt: auf der Box stand sonst die Adresse der Buildumgebung im Image.
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build


FROM node:22-slim

# Kein useradd: node:22-slim bringt den Nutzer `node` mit UID 1000 schon mit,
# und `useradd --uid 1000` scheitert dann mit Code 4 (UID in use). Das ist
# genau die UID, die das Chart in securityContext.runAsUser setzt.

WORKDIR /app

COPY --from=build /build/.next/standalone ./
COPY --from=build /build/.next/static ./.next/static
COPY --from=build /build/public ./public
COPY --from=build /build/server-zeitlimit.cjs ./
COPY db/schema.sql ./db/schema.sql

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    MOVE_DATA_DIR=/app/data \
    MOVE_SCHEMA_FILE=/app/db/schema.sql
# Das standalone-Image lauscht nur auf $HOSTNAME. Ohne 0.0.0.0 ist der Pod
# vom Service aus nicht erreichbar.
ENV HOSTNAME=0.0.0.0

USER 1000:1000
EXPOSE 3000

# Nicht `node server.js`. Das --require hebt Nodes requestTimeout von 300 s
# auf 2 Stunden; ohne es endet ein langsamer Upload in HTTP 408.
CMD ["node", "--require", "./server-zeitlimit.cjs", "server.js"]
