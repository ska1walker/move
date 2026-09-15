# moveworker
#
# Das Image bringt ffmpeg mit drawtext und eine Schriftdatei mit. Beides ist
# Voraussetzung fuer die Platzhalter-Clips: ohne drawtext entstehen Flaechen
# ohne Index und Timecode, und ein Platzhalter ohne Timecode ist zum Pruefen
# des Schnitts wertlos.
#
# BAUKONTEXT IST DAS REPO-WURZELVERZEICHNIS, nicht worker/:
#
#   docker build -f worker/Dockerfile -t moveworker:26.9.1 .
#
# Grund: db/schema.sql liegt ausserhalb von worker/ und wird von Web und
# Worker gemeinsam gelesen. Zwei Kopien des Schemas in zwei Sprachen
# driften still auseinander.
#
# Der Tag wird beim Bauen von aussen gesetzt und muss der Chart-AppVersion
# entsprechen -- das Chart leitet ihn aus .Chart.AppVersion ab, nicht aus
# values.yaml.

FROM python:3.11-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg \
      fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

ENV MOVE_FONT_FILE=/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf

# Guard zur Bauzeit. Ein ffmpeg ohne drawtext faellt sonst erst im Cluster
# auf, und dann als Clip ohne Text statt als Fehler -- drawtext meldet einen
# kaputten Textausdruck nur als Warnung, ffmpeg endet mit 0.
RUN ffmpeg -hide_banner -filters | awk '$2=="drawtext"{gefunden=1} END{exit !gefunden}' \
 && test -f "$MOVE_FONT_FILE" \
 && ffprobe -hide_banner -version >/dev/null

# Dieselbe UID, die das Chart in securityContext.runAsUser setzt. Der
# Init-Container chownt die Unterordner von appData auf 1000:1000.
RUN useradd --uid 1000 --user-group --create-home --shell /usr/sbin/nologin move

WORKDIR /app
COPY worker/move_worker/ ./move_worker/
COPY worker/examples/ ./examples/
COPY db/schema.sql ./db/schema.sql

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MOVE_DATA_DIR=/app/data \
    MOVE_SCHEMA_FILE=/app/db/schema.sql

USER 1000:1000

ENTRYPOINT ["python3", "-m", "move_worker"]
CMD ["work"]
