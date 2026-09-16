# move auf der Box installieren und `running` messen

Der letzte Schritt, und der einzige, den kein Automat übernehmen kann: die
Box steht im lokalen Netz, die Anmeldung braucht Browser und TOTP.

Stand davor, alles gemessen: Repo öffentlich, Icon HTTP 200, beide
ghcr-Pakete anonym abrufbar, Images `26.9.1` frisch, Eintrag im Katalog live
(`marktpruefen.yml` prüft das täglich).

## 1. Ist move angekommen

Olares pollt alle fünf Minuten. Der Katalog-Hash hat sich bewegt
(`666445a6…`), also synchronisiert die Box.

```bash
olares-cli market get move -s market.AImighty
```

Zeigt das nichts, ist der Sync noch nicht durch — abwarten, nicht nachhelfen.

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
`ghcr.io/ska1walker/…:26.9.1`.

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
- **Der Identitäts-Header.** `MOVE_USER_HEADER`, Vorgabe `x-bfl-user`. Kommt
  ein anderer Name an, liest die App die Identität nicht.
- **Ob `olaresEnv` nach der Installation änderbar ist.** Alle drei envs
  tragen `applyOnChange: true`; damit sollte eine Schlüsselrotation ohne
  Neuinstallation gehen.
- **Ob die Probes beim Installieren mutiert werden.**

## 5. Wenn es klemmt

| Bild | Ursache |
|---|---|
| `registry_error` | ghcr-Paket privat. Beide sind auf Public geprüft — tritt es trotzdem auf, hat sich die Sichtbarkeit geändert |
| `Init:CrashLoopBackOff`, „services aren't ready in 20s" | `spec.runAsInternal`. Steht nicht im Manifest; `check-chart.sh` verbietet es |
| Kachel „running", Klick öffnet nichts | `openMethod: window` fehlt. Ist gesetzt und wird geprüft |
| 401 `ext_authz_denied` | ein Aufruf auf die eigene Entrance-Adresse aus dem Pod. Die Server-Komponente liest direkt aus `lib/daten.ts`, genau deshalb |
| Pods laufen, aber mit altem Image | Werte-Einfrieren beim Upgrade. Bei einer Erstinstallation ausgeschlossen |
| `downloadFailed` ohne Retry | Grund steht in `kubectl logs -n os-framework app-service-0` |

## 6. Erster Job zum Beweis

Nach `running`: im Web ein Template wählen, **Platzhalter**, Job anlegen. Der
Worker rendert Flächen mit Index und Timecode und legt das MP4 ab. Das ist
der v0-Beweis — Schnitt als Arithmetik, kein Modell beteiligt.

Erst danach lohnt ein `FAL_KEY`. Der erste echte fal-Aufruf ist nie gelaufen;
Modellname und Antwortform sind begründete Annahme. Scheitert er, steht die
vollständige Antwort in `render_job.error` und damit in der Oberfläche —
daraus ist es in einer Zeile zu korrigieren.
