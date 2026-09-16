# move auf der Box installieren und `running` messen

Der letzte Schritt, und der einzige, den kein Automat übernehmen kann: die
Box steht im lokalen Netz, die Anmeldung braucht Browser und TOTP.

**Zielversion ist `26.9.2`.** Alles davor ist erledigt und live gemessen:

| | |
|---|---|
| Images auf ghcr | `move:26.9.2` und `moveworker:26.9.2`, anonym HTTP 200 |
| Katalog | listet `move 26.9.2` unter 22 Apps |
| Chart | HTTP 200, 8085 Byte, entpackt in einem Schritt, `envs`-Block drin |
| Hash | `abbe5337…` (vorher `666445a6…`), also synchronisieren die Boxen |

Damit ist dieses Dokument der einzige noch offene Schritt.

## 1. Ist move angekommen

Der Katalog steht auf 26.9.2, der Hash hat sich bewegt — die Boxen
synchronisieren also. Olares pollt alle fünf Minuten.

```bash
olares-cli market get move -s market.AImighty
```

Zeigt das nichts, ist der Sync noch nicht durch — abwarten, nicht nachhelfen.
Zeigt es **26.9.1**, ist es der alte Stand: dieses Chart rendert den Worker
ohne `apiVersion` und lässt sich nicht installieren. Dann nicht installieren,
sondern weiter warten oder die Market Source einmal entfernen und neu
hinzufügen (nach `docs/olares-learnings.md` 9.3 der einzige dauerhafte Fix,
wenn `raw_data` klemmt; der Sync-Knopf leert den Cache nicht).

## 2. Installieren

```bash
olares-cli market install move -s market.AImighty --watch
```

Olares fragt dabei drei Werte ab, **alle optional**:

| Name | Typ | leer lassen heißt |
|---|---|---|
| `FAL_KEY` | password | keine Generierung; Platzhalter und eigene Uploads |
| `MOVE_FAL_MODEL` | string | Vorgabe aus `generierung.py` |
| `MOVE_FAL_MAX_CLIPS` | int | 12 |

**Beim ersten Mal alle drei leer lassen.** Die Pipeline läuft ohne einen
einzigen Modellaufruf durch; ein fehlender Schlüssel darf die Installation
nicht aufhalten, und genau das ist zu prüfen.

## 3. Messen, nicht der Kachel glauben

Eine grüne Kachel sagt nicht, welches Image läuft.

```bash
kubectl get pods -n move-<nutzer> \
  -o custom-columns='N:.metadata.name,I:.spec.containers[*].image,R:.status.containerStatuses[0].ready'
```

Erwartet: zwei Pods, `move` und `moveworker`, beide `true`, beide auf
`ghcr.io/ska1walker/…:26.9.2`. Steht dort `26.9.1`, hat die Box noch den alten
Katalogstand synchronisiert — zurück zu Schritt 1, nicht weitermachen.

Die Adresse ist `https://3734a903<index>.<nutzer>.<zone>` — `3734a903` ist
`md5("move")[:8]`. Nicht selbst ausrechnen, sondern nachsehen:

```bash
olares-cli settings apps list
```

## 4. Was dort zum ersten Mal geprüft wird

Vier Dinge stehen im Repo als unverifiziert und entscheiden sich hier:

- **`authLevel: private`** — AGENTS.md nennt `internal`. Im offiziellen
  Katalog nutzen fünf Apps `private`, darunter `aimragflow` in Marcs Markt.
  Belegt, aber nie gegen unsere Box gemessen.
- **Der Identitäts-Header.** `MOVE_USER_HEADER`, Vorgabe **`remote-user`**.
  Hier stand `x-bfl-user`; das war falsch. Nach `docs/olares-learnings.md` 3.2,
  am `config_dump` des Sidecars gemessen, setzt Envoy `X-Bfl-User` nicht —
  und ein Browser kann ihn selbst setzen. `identitaet.ts` liest ihn deshalb
  nicht mehr, sondern ignoriert ihn ausdrücklich. Zu prüfen bleibt, ob
  `Remote-User` am Pod ankommt. Kommt er nicht, ist die App ohne Identität,
  und das fällt in der Oberfläche als „fehlt" auf, nicht still.
- **Ob `olaresEnv` nach der Installation änderbar ist.** Alle drei envs
  tragen `applyOnChange: true`; damit sollte eine Schlüsselrotation ohne
  Neuinstallation gehen.
- **Ob die Probes beim Installieren mutiert werden.**
- **Was die Eingangsschicht beim Upload durchlässt.** Der Upload-Pfad ist
  gegen 400 MB gemessen — aber am Port-Forward, also an Envoy und
  `beclab/auth` vorbei. Dieselbe Lücke nennt `docs/olares-learnings.md` 16 als
  offene Frage 2: belegt ist dort nur, dass **mindestens 10 MB** durch die
  Eingangsschicht kamen. Der erste Upload über die echte Adresse ist deshalb
  eine Messung, keine Bestätigung. Klemmt es, liegt es nicht an Next: dann
  einen kleinen und einen großen Upload gegenüberstellen und die Grenze
  suchen, statt an `server-zeitlimit.cjs` zu drehen.

## 5. Wenn es klemmt

| Bild | Ursache |
|---|---|
| `registry_error` | ghcr-Paket privat. Beide sind auf Public geprüft — tritt es trotzdem auf, hat sich die Sichtbarkeit geändert |
| `Init:CrashLoopBackOff`, „services aren't ready in 20s" | `spec.runAsInternal`. Steht nicht im Manifest; `check-chart.sh` verbietet es |
| Kachel „running", Klick öffnet nichts | `openMethod: window` fehlt. Ist gesetzt und wird geprüft |
| 401 `ext_authz_denied` | ein Aufruf auf die eigene Entrance-Adresse aus dem Pod. Die Server-Komponente liest direkt aus `lib/daten.ts`, genau deshalb |
| Pods laufen, aber mit altem Image | Werte-Einfrieren beim Upgrade. Bei einer Erstinstallation ausgeschlossen |
| `downloadFailed` ohne Retry | Grund steht in `kubectl logs -n os-framework app-service-0` |

Zwei Befehle aus `docs/olares-learnings.md` 10, die move konkret braucht.

**Was Helm wirklich gespeichert hat** — die einzige Stelle, an der das
Werte-Einfrieren aus §6 sichtbar wird. Zweimal base64, das ist kein Tippfehler:

```bash
kubectl get secret -n move-<nutzer> sh.helm.release.v1.move.v<N> \
  -o jsonpath='{.data.release}' | base64 -d | base64 -d | gunzip
```

Steht dort ein `images.web.tag` mit einer Zahl drin, ist der Pin zurück, den
`check-chart.sh` im Repo verbietet — dann hilft nur `--set images.web.tag=`
beim Upgrade. Die Ausgabe enthält `olaresEnv.FAL_KEY` im Klartext, also nicht
in eine Datei umleiten.

**Speicher eines Pods.** Auf der Box gibt es keine Metrics API, `kubectl top`
antwortet nicht:

```bash
kubectl exec -n move-<nutzer> deploy/move -c move -- \
  sh -c 'grep VmRSS /proc/1/status; cat /sys/fs/cgroup/memory.current'
```

Damit ist die Annahme hinter dem 1-GiB-Limit nachzumessen (Begründung steht im
Kommentar in `move/templates/deployment-move.yaml`): der Streaming-Pfad lag
lokal bei 104–129 MB RSS gegen 400 MB Upload. Liegt der Wert auf der Box in
derselben Größenordnung, stimmt die Rechnung. Liegt er bei mehreren hundert MB,
läuft irgendwo wieder `fetch` — dann dort suchen, nicht das Limit anheben.

## 6. Erster Job zum Beweis

Nach `running`: im Web ein Template wählen, **Platzhalter**, Job anlegen. Der
Worker rendert Flächen mit Index und Timecode und legt das MP4 ab. Das ist
der v0-Beweis — Schnitt als Arithmetik, kein Modell beteiligt.

Erst danach lohnt ein `FAL_KEY`. Der erste echte fal-Aufruf ist nie gelaufen;
Modellname und Antwortform sind begründete Annahme. Scheitert er, steht die
vollständige Antwort in `render_job.error` und damit in der Oberfläche —
daraus ist es in einer Zeile zu korrigieren.

## 7. Ab dem zweiten Mal: vorher trocken prüfen

Gilt **nicht** für die Erstinstallation — es gibt noch keine gespeicherten
Werte. Ab dem ersten Upgrade ist es der wichtigste Schritt, und nach
`docs/olares-learnings.md` 6 ändert er nichts:

```bash
helm get values move -n move-<nutzer> -a -o yaml > /tmp/w.yaml
helm template move /tmp/move-<version>.tgz -n move-<nutzer> -f /tmp/w.yaml > /tmp/r.yaml
kubectl apply --dry-run=server -f /tmp/r.yaml -n move-<nutzer>
rm /tmp/w.yaml /tmp/r.yaml   # enthalten die envs im Klartext, also den fal-Schluessel
```

Der Sinn steht wörtlich im Dokument: *„Der Stub-Render hätte den leeren
hostPath nie gezeigt; die echten Werte schon."* Genau das ist der Unterschied
zu `check-chart.sh`. Der Guard rendert gegen eine Werte-Variante ohne die
neuen Schlüssel und findet damit den `nil pointer` — aber er kennt die Werte
nicht, die auf **dieser** Box seit der Erstinstallation eingefroren sind. Für
move ist der Kandidat `.Values.userspace.appData`: rendert er auf einer
Bestandsinstallation leer, lehnt die API mit
`spec.template.spec.volumes[0].hostPath.path: Required value` ab, und das ist
bei `strategy: Recreate` echte Downtime statt eines fehlgeschlagenen Rollouts.

`rm` nicht vergessen. `helm get values -a` schreibt `olaresEnv.FAL_KEY` im
Klartext in beide Dateien — CLAUDE.md: „Gerenderte Dateien mit Secrets nach
der Prüfung löschen."
