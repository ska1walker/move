# move

KI-Video-Plattform mit vorgefertigten cinematischen Presets und gelernten
Schnitt-Templates. Native Olares-App, ausgeliefert über eine eigene Market
Source.

Stand: **Chart, Assembler, Worker-Image, Job-Tabelle und Upload-Pfad stehen.**
Die Pipeline läuft von einem handgeschriebenen `CutTemplate` bis zum fertigen
MP4, ohne einen einzigen KI-Aufruf. Beide Images bauen in CI. Auf der Box war
noch nichts installiert — `running` ist nicht gemessen.

## Was drin ist

```
CLAUDE.md                      Projektregeln, Scope v0, Architektur, Fallstricke
AGENTS.md                      Market-Source-Playbook (AImighty, Marc)
scripts/check-chart.sh         Vorab-Guards, aus dem Insilo-Original umgebaut
scripts/make-icon.py           erzeugt icon.png reproduzierbar, ohne Bildbibliothek
icon.png                       512x512, Hanseatenblau + Gold
OlaresManifest.yaml            Root-Manifest (Store)
move/Chart.yaml                Version 26.9.1
move/OlaresManifest.yaml       Chart-Manifest, byteweise identisch zum Root
move/values.yaml               keine Pins, keine Secrets
move/values-olares-stub.yaml   Stub für helm lint/template
move/templates/                zwei Deployments, ein Service
db/schema.sql                  Schema, von Web UND Worker gelesen
worker/                        Assembler, Platzhalter, Job-Schleife
web/                           Next.js 15.5, Upload-Pfad, Oberfläche
.github/workflows/ci.yml       Guards, Tests, beide Images
.dockerignore
.gitignore
```

Beide Images bauen mit dem **Repo-Wurzelverzeichnis** als Kontext, weil
`db/schema.sql` von Web und Worker gemeinsam gelesen wird:

```bash
docker build -f worker/Dockerfile -t moveworker:26.9.1 .
docker build -f web/Dockerfile    -t move:26.9.1 .
```

Chart-Stand: ein Entrance auf `move` (Port 3000), Worker `moveworker` ohne
Entrance und ohne Service, SQLite auf `appData` per hostPath mit
`strategy: Recreate`, Init-Container als root nur aus dem beclab-Image,
Image-Tags aus `.Chart.AppVersion`, kein GPU-Bedarf in v0.

## Was noch fehlt

| Datei | Woher |
|---|---|
| `docs/olares-learnings.md` | das gemessene Olares-Dokument (Stand 15.09.2026). **Wichtigste fehlende Datei** — CLAUDE.md verweist bei jedem Widerspruch darauf. |
| `docs/design-guide.md` | Kopie aus dem AImighty-Markt-Repo. Ohne sie kann Claude Code die verbindliche Designvorgabe nicht lesen. |
| ghcr-Veröffentlichung | Die Images bauen in CI, werden aber nicht gepusht. Das Paket muss öffentlich sein, sonst `registry_error` bei der Installation. |
| erreichbare `icon.png`-URL | Aus demselben Grund: das Repo ist privat, `raw.githubusercontent.com` liefert kein HTTP 200. |

Optional, falls vorhanden: `scripts/release.sh` und
`scripts/regen-migrations.py` aus dem Insilo-Repo. `check-chart.sh` ruft den
Generator auf, überspringt den Guard aber sauber, solange er fehlt.

## Erste Schritte

1. Knotenzahl und GPU-Belegung messen — die beiden Referenzdokumente
   widersprechen sich (ein Knoten oder zwei):

   ```bash
   ssh olares@<box>
   export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
   kubectl get nodes
   olares-cli settings compute list
   ```

   Das Ergebnis entscheidet, ob der Worker einen `nodeSelector` bekommt.

   Für v0 ist das noch nicht dringend: das Chart fordert **keine GPU** an, der
   Worker rechnet auf der CPU. Die Frage wird erst bei TransNetV2 scharf.

2. `./scripts/check-chart.sh` laufen lassen. Der Guard kommt vor dem Fix.

   Ohne `helm` und `olares-cli` im PATH überspringt er das Ressourcen-Budget,
   den Root-Image-Guard, `helm lint/template` und den offiziellen Validator —
   also genau die Prüfungen, die Struktur und Budget betreffen. Vor einem
   Release muss er dort laufen, wo beide Werkzeuge liegen.

3. Assembler: steht. `worker/README.md`, dann

   ```bash
   cd worker && python3 -m move_worker demo \
     --template examples/beat-8s.json --out-dir /tmp/move
   ```

4. Worker-Image: steht. Es bringt ffmpeg **mit `drawtext`** und eine
   Schriftdatei mit; ohne beides gibt es keine Platzhalter.

5. Upload-Pfad: steht, gegen 400 MB gemessen. Siehe `web/README.md`.

6. Offen: ghcr-Veröffentlichung, dann installieren und `running` auf der Box
   **messen**.

## Reihenfolge, die nicht verhandelbar ist

Images bauen -> auf der eigenen Box installieren und `running` **messen** ->
erst dann in den Katalog. Eine App, die nie `running` erreicht hat, gehört in
keinen Markt.
