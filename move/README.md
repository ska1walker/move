# move

KI-Video-Plattform mit vorgefertigten cinematischen Presets und gelernten
Schnitt-Templates. Native Olares-App, ausgeliefert über eine eigene Market
Source.

Dieses Paket ist ein **Gerüst**, kein lauffähiges Projekt. Es enthält die
Regeln und Guards, damit Claude Code nicht bei null anfängt — das Chart und
der Code entstehen darin.

## Was drin ist

```
CLAUDE.md                     Projektregeln, Scope v0, Architektur, Fallstricke
AGENTS.md                     Market-Source-Playbook (AImighty, Marc)
scripts/check-chart.sh        Vorab-Guards, aus dem Insilo-Original umgebaut
move/values-olares-stub.yaml   Stub für helm lint/template
.gitignore
```

## Was noch fehlt — vor dem ersten Claude-Code-Lauf ergänzen

| Datei | Woher |
|---|---|
| `docs/olares-learnings.md` | das gemessene Olares-Dokument (Stand 15.09.2026). **Wichtigste fehlende Datei** — CLAUDE.md verweist bei jedem Widerspruch darauf. |
| `docs/design-guide.md` | Kopie aus dem AImighty-Markt-Repo. Ohne sie kann Claude Code die verbindliche Designvorgabe nicht lesen. |
| `icon.png` | 512x512, muss öffentlich mit HTTP 200 erreichbar sein |
| Geist Sans / Geist Mono | Schriftdateien ins Repo, kein Nachladen von fremden Servern |

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

2. Repo anlegen, dieses Gerüst hineinlegen, die fehlenden Dateien ergänzen.

3. Claude Code starten. Erster Prompt:

   > Lies CLAUDE.md und AGENTS.md. Leg das Chart-Gerüst unter move/ an —
   > Chart.yaml, beide OlaresManifest.yaml, values.yaml, templates/ — nach den
   > Regeln in CLAUDE.md. Danach halt an; Code kommt in eigenen Schritten.

4. `./scripts/check-chart.sh` laufen lassen, bevor irgendetwas gebaut wird.
   Der Guard kommt vor dem Fix.

## Reihenfolge, die nicht verhandelbar ist

Images bauen -> auf der eigenen Box installieren und `running` **messen** ->
erst dann in den Katalog. Eine App, die nie `running` erreicht hat, gehört in
keinen Markt.
