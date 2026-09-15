# Olares Custom Market Source — Deployment Guide (Gold Standard)

> Battle-tested Playbook für den **AIMighty Market** (`aimighty-market.pages.dev`, Repo `bayerhazard/aimighty-market`).
> Kernlogik: **Git → Cloudflare-Deploy → Olares-Sync (5 min) → Katalog/Install**.
> Golden Rules: (1) Version identisch an 5 Stellen · (2) Metadaten-Änderung = Version bumpen · (3) Aktualisierung via `market upgrade`, `uninstall + install` nur als Backup bei Problemen · (4) Live-Chart als Baseline · (5) Titel = `AIM <Modell> <Größe> <Aufgabe>`.

---

## 1. Architektur & Datenebenen

### 1.1 Architektur

```
┌─ Olares One (Sync alle 5 min) ─────────────────────┐
│ GET  /api/v1/appstore/hash?version=X               │
│ GET  /api/v1/appstore/info                         │
│ POST /api/v1/applications/info                     │
│ GET  /api/v1/applications/{name}/chart              │
└──────────────────────┬─────────────────────────────┘
                       │ HTTPS
                       ▼
Cloudflare Pages (aimighty-market.pages.dev)
  functions/_apps.ts  ← App-Definitionen (Titel, Version, Kategorien, Descriptions)
  functions/_lib.ts   ← CHARTS-Dict (base64-Charts) + Hash/Summary/Detail-Builder
  functions/api/v1/   ← appstore/info|hash · applications/info · applications/[app]/chart
```

### 1.2 Drei Datenebenen — IMMER abgleichen

| Ebene | Quelle | Achtung |
|---|---|---|
| Git | `functions/_apps.ts` + `_lib.ts` | kann hinter Live liegen (es wurde schon mehrfach deployed ohne zu committen) |
| Live | `aimighty-market.pages.dev` | deployed Reality |
| Olares-Katalog | `olares-cli market list -s market.AImighty` | stale bis der Hash sich ändert |

- **Basis für Chart-Arbeiten: IMMER das Live-Chart** (Download via `/api/v1/applications/<app>/chart?fileName=<app>-<version>.tgz`), Repo darauf synchronisieren. Repos lagen wiederholt hinter Live (aimqwen36llama-Repo 3.3.1 vs Live 3.3.3).
- Remote-`origin/main` kann vor lokalem Stand sein → vor Edits `git fetch` vergleichen. Bei non-fast-forward: `git rebase --abort` + `git reset --hard origin/main` + Edits deterministisch (Skript) neu anwenden — nie manuell Konflikte auflösen.
- Locale-Keys in `_apps.ts` überall **`en`** (nicht `en-US`).

### 1.3 App-ID, Hash, Route-URL

- `appID = MD5(name).substring(0,8)` → Default-URL `appid.<user>.olares.com`. Die AppID hängt am K8s-Namen → **`metadata.name`, `entrance.name`, `entrance.host` NIE ändern** (bricht Open-WebUI-/LiteLLM-URLs).
- `hash = MD5(sorted "ID:name:version")`. Ändert sich NUR bei add/remove/`name`/`version`. **Titel, Kategorien, Descriptions ändern den Hash NICHT** → Olares re-synct nicht. Fix: Version bumpen (Live-Beispiel: Kategorie-Fix `AI Agents→AI` erforderte `26.08.1→26.08.2`).

### 1.4 Olares Sync-Flow (5-min-Zyklus)

1. **Hash-Vergleich** — match → alle weiteren Schritte übersprungen
2. **Data Fetch** — GET `/appstore/info` → Apps als "pending"
3. **Detail Fetch** — POST `/applications/info` (Batches à ~10) → raw data inkl. `chartName`
4. **TaskForApiStep** — POST Chart an `chart-repo-service:82/chart-repo/api/v2/dcr/sync-app`; Fehler/Timeout → **render-failed-Liste, kein Auto-Retry** (nur Version-Bump erzwingt Re-Fetch)
5. **Hydration** — periodisch: hat jede App RawPackage (Chart)?

---

## 2. Konventionen (seit 2026-08-07)

> Goldene Regel: `_apps.ts`, Chart-`OlaresManifest.yaml` UND Repo-Root-`OlaresManifest.yaml` tragen **IDENTISCHE** Werte. Für neue Apps von Anfang an anwenden.

### 2.1 App-Titel: `AIM <Modell> <Größe> <Aufgabe>`

- **Kein Engine-Suffix** im Titel (vLLM/Beellama/HQ/Dashboard). Engine, Quant, KV-Typ, Vision → nur in die Description.
- `entrance.title` = App-Titel (identisch). `entrance.name`/`host`/K8s-`name` bleiben unverändert.
- Größe vor Aufgabe: `AIM Qwen3 1.7B TTS` (nicht "AIM Qwen3 TTS 1.7B").

### 2.2 Kategorien

Nur **AI**, **Audio**, **Utilities** (+ **Vision** als Zweit-Kategorie NUR bei multimodalen LLMs: aimqwen36llama, aimqwen3635bllama, aimqwen38llama, aimllmgemma4vllm). Genau eine primäre Kategorie pro App. Nicht mehr verwenden: `LLM Chat`, `AI Agents`, `Developer Tools`, `Productivity_v112`. Identisch in `_apps.ts` und Chart.

### 2.3 Sprache

**Englisch** (short + fullDescription). fullDescription-Template: `**Model** / **Inference Engine** / **Key Features** / **API** / **Resource Usage**`. Short-Description: `<Modell> <Größe> <Aufgabe> via <Engine> — <Key-Differentiator>, OpenAI-compatible API`.

### 2.4 Versionierung `YY.MM.<n>` (Datumsschema)

| Regel | Wert |
|---|---|
| Format | `26.08.1`, `26.08.2`, … = `YY.MM.<laufend>` |
| Zähler | **Pro App**; Monatswechsel setzt zurück (September = `26.09.1`) |
| Olares-Bezug | Erste Zeile in `upgradeDescription`: *"v26.08.1: … Built for Olares 1.12.6."* + Pin `options.dependencies: olares >=1.12.6-0` |
| SemVer | `26.08.1` funktioniert (führende Null von Helm-Parser + Olares chartrepo toleriert; `helm lint` warnt nur). Fallback, falls ein strikter Validator meckert: `26.8.1` |
| **`market upgrade` blockiert bei `YY.09.XX`** | Die olares-cli validiert Versionen mit Masterminds/strict-semver und lehnt die führende Null im Monatsfeld ab (`invalid target version '26.09.81'` UND `invalid installed version '26.09.80'` — beide Formatseiten). Workaround: `uninstall + install` als Backup-Weg (App-Daten bleiben via userspace-hostPath erhalten). Prüfen: Masterminds akzeptiert `26.9.81` als Ziel, aber die installierte `26.09.80` bleibt invalid → nur kompletter Wechsel des Versionsschemas (alle Vorversionen) würde `market upgrade` für relay ermöglichen |
| 5 Stellen | `_apps.ts` · `_lib.ts`-Key `name-<version>.tgz` · `Chart.yaml` · OlaresManifest `metadata.version` + `spec.versionName` |

**Metadaten-only-Änderung (Kategorien/Beschreibungen) = Version bumpen** — sonst bleibt Olares auf altem Stand.

### 2.5 App-Inventar (aktueller Stand)

| App (K8s-name) | Titel (alle Ebenen) | Kategorie | Route ID |
|---|---|---|---|
| aimqwen36llama | AIM Qwen3.6 27B | AI, Vision | `expert` |
| aimqwen38llama | AIM Qwen3.8 27B | AI, Vision | — |
| aimqwen3635bllama | AIM Qwen3.6 35B A3B | AI, Vision | `analyst` |
| aimllmgemma4vllm | AIM Gemma 4 26B A4B | AI, Vision | `assistant` |
| aimvoxtral4bvllm | AIM Voxtral 4B TTS | Audio | `tts` |
| aimvoxtral3asr | AIM Voxtral 3B ASR | Audio | — |
| aimqwen3ttsvllm | AIM Qwen3 1.7B TTS | Audio | — |
| aimqwen3asr | AIM Qwen3 1.7B ASR | Audio | `stt` |
| aimembqwen3vino | AIM Qwen3 4B Embedding | AI | `embedder` |
| aimrerqwen3vllm | AIM Qwen3 0.6B Reranker | AI | `reranker` |
| aimrerqwen3vino | AIM Qwen3 0.6B Reranker CPU | AI | — |
| wings | Wings for Hermes | AI | `wings` |
| rewind | Rewind | Utilities | `rewind` |

### 2.6 Custom Route ID (per-User, nach Install)

- Default-Route-URL = `appid.<user>.olares.com`; Custom Route ID = **Alias** (beide URLs funktionieren), gespeichert in `spec.settings.customDomain.<entrance>.third_level_domain` des Application-CR.
- **Nicht im Chart setzbar** — nach Install: `olares-cli settings apps domain set <app> <entrance> --third-level <routeid>`.
- Kein Zertifikat nötig (Wildcard `*.olares.de`).

---

## 3. Market Source API (Referenz)

### 3.1 `GET /api/v1/appstore/hash?version=X`
`{"hash": "...", "last_updated": "...", "version": "1.0.0"}` — Basis der Sync-Entscheidung.

### 3.2 `GET /api/v1/appstore/info`
Voller Katalog. Wichtigste Felder je App: `title`, `description`, `fullDescription`, `icon`, `version`, `categories`. `topic_lists.content` = kommaseparierte **MD5-Hashes**; `latest` = App-**Namen**.

### 3.3 `POST /api/v1/applications/info`
Body: `{"app_ids": [...], "version": "1.0.0"}`. Pflichtfelder im Detail: `title`, `i18n["en-US"].metadata.title`, `i18n["en-US"].spec.fullDescription`, `fullDescription`, `chartName` (= `"<name>-<version>.tgz"` — muss dem `CHARTS`-Key in `_lib.ts` entsprechen).

### 3.4 `GET /api/v1/applications/{app}/chart?fileName=...`
Liefert das Chart als gzip-Tarball (`Content-Type: application/gzip`). Muss enthalten: `Chart.yaml` (name = App-Name), `values.yaml`, `templates/deployment.yaml` + `service.yaml` (Ressourcen-Namen = App-Name), `OlaresManifest.yaml` (`metadata.name` = App-Name).

---

## 4. App-Release-Prozess

### 4.1 Repo-Struktur (jede App: `bayerhazard/<app>`)

```
repo-root/
  icon.png                          ← 512×512
  OlaresManifest.yaml               ← ROOT-Manifest (identische Werte wie Chart!)
  <appname>/                        ← Helm-Chart-Ordner (Name = App-Name!)
    Chart.yaml                      ← name = App-Name, version = YY.MM.<n>
    OlaresManifest.yaml             ← CHART-Manifest (metadata + spec + entrances + envs)
    values.yaml                     ← olaresEnv: {HF_TOKEN: ""} Default NICHT vergessen
    templates/deployment.yaml, service.yaml, ...
```

- `Chart.yaml`: `apiVersion: v2`, `name` = App-Name, `version`/`appVersion` = `YY.MM.<n>`.
- Entrances: `name`/`host` = App-Name, `title` = App-Titel, `openMethod: window` (Pflicht für Dashboard), `authLevel: internal`.

### 4.2 Neue App anlegen — Schritte

1. **Repo anlegen** (s. 4.1) mit v3-Manifest (s. Abschnitt 5), Titel/Kategorien/Descriptions nach Konvention (Abschnitt 2).
2. `helm package <appname>/` → `<appname>-26.08.1.tgz`.
3. **Frisches base64** erzeugen: `base64 -i <appname>-26.08.1.tgz | tr -d '\n'` — **nie alten Base64 wiederverwenden** (deploy-seitig korrumpierbar, Cloudflare error 1101).
4. `_apps.ts`: Entry mit `metadata` (name, version, icon, title, description, fullDescription, upgradeDescription, categories, developer, resources) + `spec.entrance`.
5. `_lib.ts`: Key `"<appname>-26.08.1.tgz"` + base64 in `CHARTS`. Closing-Pattern des Dicts prüfen (`,\n};` vs `\n};`) — kein doppeltes Komma.
6. **Auch Repo committen + pushen** (Root-Manifest + Chart) — erst dann Market-Source committen.

### 4.3 App aktualisieren

**Metadaten-only** (Titel/Description/Kategorien): `_apps.ts` ändern + **Version bumpen** + frisches base64 (Chart kann identisch sein: aus `_lib.ts` extrahieren, neu komprimieren mit `mtime=0`, neu encodieren) + `_lib.ts`-Key auf neue Version.

**Chart-Änderung**: Chart-Repo anpassen (OlaresManifest + Chart.yaml + values.yaml) → Version bumpen → `helm package` → frisches base64 → `_apps.ts` + `_lib.ts` → beides committen/pushen.

**Immer**: Version an allen 5 Stellen identisch, sonst 404 beim Chart-Download und Olares zeigt die alte Version.

### 4.4 Deploy-Befehle

```bash
cd "/Users/marc/Documents/OpenCode/Olares Apps und Market/Olares Market Source"
git add functions/ && git commit -m "…" && git push          # IMMER erst committen!
export PATH="/usr/local/bin:$PATH"
./node_modules/.bin/wrangler pages deploy functions/ --project-name=aimighty-market
```

### 4.5 Verify

```bash
# 1. Deploy-URL zeigt SOFORT neue Daten (Kanonikal-Domain cachet ~1-2 min, s. 6.4):
curl -s "https://<hash>.aimighty-market.pages.dev/api/v1/appstore/info" | python3 -m json.tool
# 2. Charts liefern 200:
for a in <app>; do curl -s -o /dev/null -w "%{http_code}\n" \
  "https://aimighty-market.pages.dev/api/v1/applications/$a/chart?fileName=$a-26.08.1.tgz"; done
# 3. Olares: 5-min-Sync abwarten → Katalog zeigt neue Version:
olares-cli market get <app> -s market.AImighty
# 4. Installierte Apps: via Update aktualisieren (Namen/Endpoints kommen aus dem Chart):
olares-cli market upgrade <app> --watch
#    ACHTUNG relay (Versionsformat YY.09.XX): market upgrade validiert strikt-semver
#    und lehnt die führende Null im Monatsfeld ab (s. 2.4) → ERZWUNGENER Backup-Weg:
#    olares-cli market uninstall relay --watch && olares-cli market install relay -s market.AImighty --watch
#    Danach IMMER: Route-ID neu setzen (uninstall löscht das customDomain-Feld):
#    olares-cli settings apps domain set relay relay --third-level mail
#    Bei CrashLoopBackOff nach Install: Image aus dem Node-Cache entfernen, damit frisch gepullt wird:
#    kubectl exec ... rmi ghcr.io/bayerhazard/relay-one:<v> (crictl rmi) → kubectl rollout restart deploy/relay -n relay-aimighty
# 5. Route-ID setzen (falls in Tabelle): olares-cli settings apps domain set <app> <app> --third-level <routeid>
```

---

## 5. OlaresManifest v3 & Charts

### 5.1 v3-Header + Pflicht-Reihenfolge

```yaml
olaresManifest.version: '0.12.0'
olaresManifest.type: app
apiVersion: 'v3'
workloadReplicas:            # TOP-LEVEL, zwingend
  <appname>: 1
metadata:                    # name, appid, icon, description, title, version, categories
entrances:                   # name, port, host, title, icon, openMethod, authLevel
spec:                        # ALLE Metadaten unter spec:
  versionName
  fullDescription / upgradeDescription
  developer, website, sourceCode
  locale, supportArch
  requiredCpu, limitedCpu, requiredMemory, limitedMemory, requiredDisk, requiredGpu
permission:
envs:                        # User-Env-Mapping (HF_TOKEN etc.)
options:                     # apiTimeout, allowMultipleInstall, dependencies
```

### 5.2 Die 5 v3-Validierungen (app-service lint, HTTP 400/403)

| # | Fehler | Fix |
|---|---|---|
| 1 | `apiVersion v2 is incompatible` (403) | `apiVersion: 'v3'` + `olaresManifest.version: '0.12.0'` |
| 2 | `must restrict to >=1.12.6-0 for apiVersion=v3` | `options.dependencies[name=olares].version: '>=1.12.6-0'` |
| 3 | `replicas must reference .Values.workloads.<name>.replicaCount` | `deployment.replicas: {{ .Values.workloads.<name>.replicaCount }}` + values.yaml Default |
| 4 | `must not use OLARES_USER_* names` | `.Values.olaresEnv.<name>` + `envs:`-Mapping (valueFrom `OLARES_USER_HUGGINGFACE_TOKEN`) |
| 5 | `requiredCpu must be empty when spec.resources[] is set` | ENTWEDER flat fields ODER accelerator + resources[] — nie beides. `requiredGpu>0` synthetisiert {nvidia} |

### 5.3 YAML-Fallen (app-service parst Templates als RAW-YAML vor Helm-Rendering)

| Falle | Fix |
|---|---|
| Doppelte Keys in `values.yaml` (z. B. 2× `olaresEnv`) | Zu einer Sektion zusammenführen |
| Block-Skalar (`\|`) mit inkonsistentem Einzug | Alle Zeilen ≥ Einzug der ersten Zeile |
| `.Values.olaresEnv.X` nil-Pointer bei `helm template` | `olaresEnv: {HF_TOKEN: ""}` als Default in values.yaml (Olares überschreibt zur Installzeit) |
| `{{ .Values.xxx }}` außerhalb von Block-Skalaren in YAML | In `\|`-Block oder quoten |
| Chart validiert nicht: `invalid tar header` | Nur EINMAL gzip — base64 aus rohem `helm package`-Output |

### 5.4 check-auth / Sidecar

- Olares sandbox-webhook injiziert `check-auth` (`beclab/wait-for:0.1.0`, wartet auf authelia HTTP 200 — bekommt aber 400 ohne Auth-Header → CrashLoopBackOff).
- **Fix:** check-auth manuell im Chart mit `command: ["true"]` definieren (Webhook-Patch wird harmlos). Erfordert manuelle Definition von `render-envoy-config`, `olares-sidecar-init`, `olares-envoy-sidecar` (sonst fehlt der Sidecar).
- `render-envoy-config`: `awk` statt `sed` für Token-Substitution (JWT-Zeichen brechen sed-Delimiter).

---

## 6. Runtime-Fallen (destilliert aus allen Debug-Sessions)

### 6.1 GPU & HAMi

| Falle | Fix |
|---|---|
| `cudaMalloc failed: out of memory` bei leerer GPU | **`CUDA_DEVICE_MEMORY_LIMIT_0=30000m`** als Container-Env (Pflicht!). `nvidia.com/gpumem` wird von Olares-HAMi ignoriert |
| `insufficient GPUBindings for app X, requested=1, bound=0` → Pending | `market install` hat kein `--compute-binding`-Flag → uninstall + frische install (erzeugt Binding automatisch, GPU muss frei sein) |
| `compute resource is not enough for selected mode` (400) | GPU durch andere App belegt. RTX 5090 (24 GB): nur 1 großes LLM exklusiv pro Node; Worker-GPU (MemorySlice) oft voll. CPU-Apps (embedder/reranker-cpu) haben kein GPU-Problem |
| GPU-Zuteilung prüfen | `olares-cli settings compute list` (Node, Modus, belegte Apps) |

### 6.2 beellama / llama.cpp (aimqwen36llama, aimqwen3635bllama)

**KV-Cache-Typen** (Engine `aamsellem/beellama-cpp:0.1.3-rc1`): erlaubt sind `f32, f16, bf16, q8_0, q4_0, q4_1, iq4_nl, q5_0, q5_1, turbo2, turbo3, turbo4, turbo3_tcq, turbo2_tcq`. **`kvarn4` existiert nicht mehr** (Boot-Crash). **turbo4 = Default** (16.5 KB/tok, Decode ~90 t/s, bessere Langkontext-Qualität als turbo3). q8_0 (~34 KB/tok) → bei 200k zu eng.

**Fallen:**

| Falle | Fix |
|---|---|
| `--spec-dflash-cross-ctx` unrecognized (v0.4.x) | `--spec-type draft-dflash` (kein cross-ctx) — Draft-GGUFs müssen upstream `arch=dflash` sein |
| `vector::_M_range_check` beim Draft-Load | **`--parallel 1`** (draft-dflash Multi-Slot-Bug; nicht file-/quant-spezifisch) |
| `internal prompt-cache rollback failure` (500) bei jedem Request | kvarn + draft-dflash = issue #93 → Standard-Quant: `--cache-type-k/v q4_0`. `--cache-ram 0` hilft NICHT |
| `n_gpu_layers already set ... abort` (v0.4.x) | `--fit off` (Memory-Fitter kollidiert mit gepinntem `-ngl`) |
| `mtmd_helper_bitmap_init_from_buf: failed to decode image bytes` | PNG programmatisch mit zlib/CRC bauen, nicht hand-basen |

**Aktuelle llama-server-Flags (aimqwen36llama 26.08.1):** `--model $TARGET --spec-draft-model $DRAFT --spec-type dflash --spec-dflash-cross-ctx 1024 --host 0.0.0.0 --port 8000 -ngl 99 --spec-draft-ngl 99 --ctx-size 200000 --threads 16 --cache-type-k turbo4 --cache-type-v turbo4 --cache-ram -1 --batch-size 2048 --ubatch-size 512 --parallel 1 --kv-unified --flash-attn on --jinja --no-mmap --mlock --temp 0.6 --top-k 20 --min-p 0.0` (+ Reasoning-Flags: `--reasoning on --reasoning-budget 4096`, Loop-Guard etc.).

**Probes (LLM-Apps):** startup `720/30s/60s`, liveness `5/30s/600s` — sonst CrashLoop bei langem Boot.

**Access:** Envoy-Sidecar gate-t allen Ingress (direkter ClusterIP-curl = 401). Debug: `kubectl port-forward -n <ns> svc/<app> 8000:8000` oder `kubectl exec <pod> -- curl http://localhost:8000`.

### 6.2a buun-llama-cpp Eigenbau (aimqwen38llama, Qwen3.8-27B)

**Release 26.08.2 (market.AImighty):** Qwen3.8-27B UD-Q4_K_XL via **selbst kompiliertem spiritbuun/buun-llama-cpp master HEAD d89f0aeb** (MTP-Vocab-Optimierungsserie: FR-Spec draft-vocab trim, auto-trim Qwen 27B MTP vocab, recursive MTP draft depth). Build via GitHub-Actions-Workflow `build-buun-llama-server.yml` (aimqwen3635ba3b-Repo, Artifact `buun-llama-server-<sha>` → Release-Asset `buun-build-output.tar.gz`). CUDA 13.1, sm_120, `-DGGML_CUDA_FA=ON -DLLAMA_OPENSSL=ON`. Container-Image: Basis `aamsellem/buun-llama-cpp:87c351d2` (CUDA-13-Libs) + neuer Layer mit frischen Binaries (docker-export-Tar via Python, `ctr images import`). Image gepusht auf `ghcr.io/bayerhazard/buun-llama-cpp:d89f0aeb` (Package **public** — private Packages → AppManager-Install `registry_error`).

**Performance (sustained, 10-run avg, stabil):** repetitive 90.7-91.0 t/s (stdev 0.1), prose 59.4-60.4 t/s (stdev 1.6-2.8), Agentic-Multi-Turn 85-91 t/s, 93k-ctx 45.8 t/s, Prefill 875-1345 t/s. KV turbo4 (4.125 bpv), n_ctx 200192. **n_max=3 ist das Optimum** (n_max=4: repetitive 99.4 aber Longctx 93k bricht auf 33 t/s ein + Agentic schwächer).

**Aktuelle Flags:** `--ctx-size 200000 --cache-type-k turbo4 --cache-type-v turbo4 --spec-type draft-mtp --spec-draft-n-max 3 --flash-attn on --jinja --reasoning on --reasoning-budget 16384 --batch-size 2048 --ubatch-size 512 --threads 16 --parallel 1 --temp 0.6 --top-p 0.95 --top-k 20 --min-p 0.0` + `--mmproj <mmproj-F16.gguf> --mmproj-gpu-swap` (Vision). Modelle auf shared-Disk `/olares/share/ai/model/llms/` (GGUF + `.ok`-Marker; `.ok` ohne Datei = CrashLoop, GGUF aus HF-Cache verlinken).

**Fallen (destilliert):**

| Falle | Fix |
|---|---|
| `Compute error rolling back speculative tokens` (500, jeder Request) | **NICHT** `draft-mtp,ngram-cache` kombinieren (v26.08.4-Config) — nur `draft-mtp`. 38ae764 brauchte n_max 2, d89f0aeb (recursive-MTP-Fix) erlaubt n_max 3 |
| AppManager-Install `downloadFailed: failed to resolve reference mirrors.olares.com/library/buun-llama-cpp` | ghcr-Image wird bei `image: {{ .Values.image.repository }}:{{ .Values.image.tag }}` als YAML-Mapping fehlgeparst → **Image als ein String quoten**: `image: "{{ .Values.image.full }}"` (values: `full: ghcr.io/...:tag`). Zusätzlich stale chartrepo-Index (26.08.6.tgz ohne Datei) → Datei mit aktuellem Inhalt unter altem Versionsnamen wiederherstellen |
| Image local vorhanden aber AppManager-Pull failt | `crictl` nutzt `/run/containerd/containerd.sock` (toter Pfad!) — richtig: `/var/run/containerd/containerd.sock`. `crictl pull` registriert Images im CRI-Store (ctr-import reicht nicht) |
| App-State `downloadFailed` blockiert Uninstall | DB direkt: `user_application_states.state='running'` + `task_records.status=4` (PG `os_framework_market`, Postgres `citus-headless.os-platform:5432`, User `market_os_system`, Secret `market-pg-secrets`). Oder kompletten `user_applications`-Eintrag löschen → frischer Install |
| ghcr-Package private → `registry_error` | Package public machen (ghcr.io/bayerhazard/<pkg>, Settings → Visibility) |
| `-DGGML_CUDA_FA=ON` Docker-Build failt `undefined reference cuGetErrorString` beim llama-bench-Link | Nur `--target llama-server` bauen (bench braucht libcuda.so.1, fehlt im cuda-devel-Image) |
| `crictl images` leer | falscher Socket (s.o.) — mit `--runtime-endpoint unix:///var/run/containerd/containerd.sock` prüfen |

### 6.3 vLLM

| Falle | Fix |
|---|---|
| `unrecognized arguments: --task` → CrashLoop | `api_server.py` kennt `--task` nicht (nur `vllm serve`). Bei Seq-Cls-Modellen wird `/v1/rerank`/`/classify` auto-aktiviert |
| Rolling-Tag (`cu129-nightly`) nicht reproduzierbar | **SHA-Suffix-Tag** pinneN: `cu129-nightly-<git-sha>` (SHA aus `system_fingerprint` von /v1/chat/completions). Tag-Liste: Docker-Hub-API |
| `KeyError: weight_packed` (MoE-AWQ) | Image mit PR #40708 (cu129-nightly ab ~Mai 2026) |
| MM-Encoder-Cache frisst VRAM | `--limit-mm-per-prompt '{"image":{"count":1,"width":256,"height":256},"video":0}'` — **nie `image:0`** (Profiler reserviert dann Maximum) und immer Size-Hint (Pool +1 GiB) |
| `block_size (N) must be <= max_num_batched_tokens` | `MAX_NUM_BATCHED_TOKENS` ≥ Mamba block_size (4096 @ spec1, 4224 @ spec2) |
| `Unknown TurboQuant cache dtype: 'auto'` (hybride Modelle) | PR #49798-Hot-Patch im Chart-Wrapper (in KEINEM vLLM-Build gemerged) |
| TQ-4bit + Spec-Decode = 0% Acceptance | Nur TQ-**3bit** verwenden (18.1 KB/tok vs 22.6); fp8 = 39 KB/tok |
| fp8-KV: `estimated maximum model length is N` | MAX_MODEL_LEN direkt auf N setzen (kein Raten) |
| Probes werden bei Install mutiert (3×10s/1s) | Nach JEDER Install live patchen (startup 720/30/60, liveness 5/30/600) |
| args werden beim Helm-Rendern aus `.Values.env` gebrannt — ConfigMap-Edits wirkungslos für args | Deployment patchen (JSON-Patch auf args-Index) oder Chart ändern |
| kubectl JSON-Patch auf `args/<idx>` | Erst alle Indizes dumpen, dann **von hinten nach vorne** entfernen |

### 6.4 Olares market-backend / chartrepo / Cache

| Falle | Fix |
|---|---|
| **Values-Freeze bei `market upgrade`** (rendert neue Templates mit alter values.yaml) | Erst `market upgrade` versuchen; nur falls danach die alten Werte hängen: `uninstall + install` als Backup |
| **`raw_data`-Verklemmung** (chartrepo render-Fehler → market-DB bleibt auf alter Version; jede Install liefert die alte Version; Hydration meldet nur `existing`) | Diagnose: AM-CR `spec.config.Version` + `./charts/<app>/Chart.yaml` im app-service-Pod. **Einziger Dauerfix: Market-Source im UI entfernen + neu hinzufügen** (baut market-DB neu). Live-Patches sind nur flüchtig |
| **market-backend Startup-Dependency-Check** (verlangt chartrepo version < 0.3.0, meldet 1.0.0 → Endlos-Loop, Market komplett down) | Fake-chartrepo-Workaround: Fake-Pod (python HTTPServer, Port 82, liefert `{"data":{"version":"0.2.9",...}}`) → Service-Selector auf Fake umschalten → **echten chartrepo-Pod löschen** (Keep-Alive! conntrack klebt sonst am alten Pod) → Check besteht → sofort zurückschalten |
| `sync-app` HTTP 500 "render failed or timed out" | Meist **transienter Timeout** — chartrepo rendert trotzdem korrekt (RenderedPackage gespeichert), Katalog aktualisiert sich. Kein Handeln nötig |
| App in render-failed-Liste (kein Auto-Retry) | Version bumpen → Hash ändert sich → kompletter Re-Fetch |
| `hash comparison skipped` (cache_manager lädt hash ohne Daten) | App selbst bumpen (zuverlässig); Restart chartrepo → market-deployment |
| Cloudflare-Edge-Cache nach Deploy (Kanonikal-Domain zeigt minutenlang alte Daten) | Deployment-URL `https://<hash>.aimighty-market.pages.dev` zum Verifizieren nutzen; Cache settled nach ~1-2 min. `.wrangler/cache` löschen hilft NICHT (Edge) |
| Olares-Katalog zeigt entfernte Apps weiter | Source-Remove/Re-Add im UI (kein code-seitiger Weg) |
| `settings.title` leer / `source: "unknown"` im Application-CR (Settings-UI zeigt "-") | `kubectl patch application <cr> -n <ns> --type=merge -p '{"spec":{"settings":{"title":"<Titel>","source":"market"}}}'` |
| ghcr-Image-Tag ohne `v`-Präfix (Release-Workflow) | `image.tag` ohne v setzen; Tag-Liste via ghcr-Token-API prüfen |
| Chart-only-Fix ändert Hash nicht | Bei JEDEM Chart-Content-Fix Version bumpen |

### 6.5 Envoy-Sidecar — Runtime-Netzwerk

- Sidecar gate-t **allen** Ingress (auch in-Cluster-curl auf ClusterIP → 401 ohne Session). App-zu-App: über Entrance-URL `https://<app>.<user>.olares.com` (Olares-Middleware fügt Session hinzu).
- **ApiTimeout: 0** im ApplicationManager → envoy `timeout: 0s` (unlimited — sonst 15s und LLM-Responses werden abgeschnitten). Finale Werte: Route-Timeout 0s, Connect 120s, Idle 10s, ext_authz 0s / connect 0.250s.
- Sidecar-ConfigMap exakt vom funktionierenden Referenz-App übernehmen (eigene Timeout-Werte → envoy exit code 1).
- Pod braucht Label `app: <name>`, sonst fehlen Service-Endpoints.

---

## 7. Troubleshooting (Symptom → Fix)

| Symptom | Ursache | Fix |
|---|---|---|
| Store zeigt alte Version, API zeigt neue | `CHARTS`-Key ≠ `_apps.ts`-Version (404 → Fallback auf Cache) | Key umbenennen; `getChartByAppName()` baut `name-version.tgz` |
| Olares synct nicht (Hash matcht trotz neuem Deploy) | cache_manager-Bug | App selbst bumpen oder chartrepo→market-Deployment restarten |
| Chart-Download 500 (error 1101) | Base64 deploy-seitig korrupt | Chart extrahieren, mit `mtime=0` neu komprimieren, frisch base64-en |
| `context deadline exceeded` bei sync-app | chartrepo ausgelastet (3s-Timeout) | Version bumpen → Re-Fetch (transient) |
| `YAML parse error on ... ConfigMap` → Download failed | Block-Skalar-Einzug inkonsistent | Alle `\|`-Zeilen ≥ Initial-Einzug |
| App zeigt "running" statt "open" | `openMethod: window` fehlt | Entrance ergänzen, Version bumpen, deployen |
| `Unable to install app, Incompatible with your Olares` | Entrance-name/host ≠ metadata.name | Beides auf App-Namen |
| Chart validiert nicht (`invalid tar header`) | Doppelt-gezippt (Python `gzip.compress(tgz)`) | base64 aus rohem `helm package`-Output |
| `upgrade` schlägt fehl ("app is not installed") | Vorher uninstalliert, keine state-row | `market install` statt `upgrade` |
| Build failed: dangling comma in `_lib.ts` | CHARTS-Dict-Einfügung | Closing-Pattern prüfen (`,\n};` vs `\n};`) |
| Probes CrashLoop bei vLLM-Boot | Install mutiert Probes auf 3×10s/1s | Nach Install patchen (s. 6.3) |
| `settings.title` leer nach Reinstall | Install-Flow setzt Feld nicht immer | CR patchen (s. 6.4) |

---

## 8. Critical Rules

1. **`name` konsistent überall:** `_apps.ts` → `Chart.yaml` → `OlaresManifest.yaml` → Templates → K8s-Ressourcen. Chart-Ordner-Name = App-Name.
2. **Version konsistent an 5 Stellen** (s. 2.4) — #1 Fehlerquelle.
3. **`i18n["en-US"].metadata.title` + `spec.fullDescription`** erforderlich für Store-Anzeige.
4. **Chart-Key** `"<name>-<version>.tgz"` muss `chartName` entsprechen.
5. **Metadaten-only-Änderung = Version bumpen** (Hash ändert sich sonst nicht).
6. **Nie alten Base64 wiederverwenden** — immer frisch `base64 -i`.
7. **Aktualisierung via `market upgrade`; `uninstall + install` nur als Backup bei Problemen.**
8. **Basis = Live-Chart**, nicht Repo-HEAD (Repos lagen hinter Live).
9. **Nur `functions/` editieren** (`bayerhazard/aimighty-market`; altes `bayerhazard/aimighty` ist deprecated). Immer committen VOR dem Deploy.
10. **`node_modules/`, `.wrangler/` nie committen** (`.gitignore`).
11. **Titel-Muster `AIM <Modell> <Größe> <Aufgabe>`**, kein Engine-Suffix; K8s-Name/AppID nie umbenennen.
12. **Kategorien nur AI/Audio/Utilities** (+ Vision bei multimodalen LLMs), identisch in `_apps.ts` + Chart.
13. **Entrance-Titel = App-Titel**; `entrance.name`/`host` unverändert.
14. **Beschreibungen Englisch**, Template `**Model** / Inference Engine / Key Features / API / Resource Usage`.
15. **Version `YY.MM.<n>`** pro App (Monatsreset), Olares-Bezug in upgradeDescription + Dependencies-Pin.
16. **Custom Route ID nie im Chart** — nach Install via `settings apps domain set`.
17. **`values.yaml` keine doppelten Keys**; `olaresEnv`-Default nie vergessen.
18. **vLLM: `latest` nie mit `--task score`**; SHA-Tags pinneN.
19. **GPU-Apps: `CUDA_DEVICE_MEMORY_LIMIT_0` setzen; Probes nach Install patchen.**
20. **Rebase-Konflikte nicht manuell lösen** — `reset --hard origin/main` + deterministische Skript-Edits.
21. **Stale Chart-Keys in `_lib.ts` regelmäßig bereinigen** (nur aktuelle Versionen behalten).

---

## 9. Hardware & Frontend-Standard

**Olares One:** Intel Core Ultra 9 275HX · 96 GB RAM · NVIDIA RTX 5090 (Blackwell, 24 GB VRAM) — Apps darauf optimieren. Beide Nodes (olares + olares-worker) haben je eine 5090 (Exclusive bzw. MemorySlice).

**App-Frontends/Dashboards/GUIs — verbindlicher Designguide:** [`docs/design-guide.md`](docs/design-guide.md). Bei JEDER Erstellung von Apps, Dashboards, GUIs, HTML-Deliverables und Webseiten zwingend zu berücksichtigen. Kern: Hanseatenblau `#051729` + Gold `#caa960` (zwei Farben, keine dritte Familie) · Geist Sans/Geist Mono selbst gehostet · kein Verlauf, kein Schatten · keine festen Pixel, `--am-*`-Maße (Grundeinheit 4 px) · genau eine primäre Handlung je Ansicht · kein gesetzter Markenname · kein Nachladen von fremden Servern · WCAG 2.2 AA.

---

## 10. RAGFlow-Notizen (2026-08-06, ragflow-aimighty, v0.26.4)

### Live-Patches (im Deployment verankert — überleben Pod-Restarts, NICHT Reconciles/Updates!)

Nach `market upgrade`/`stop`/`resume`/Reconcile: **alle Patches neu anwenden** (Reihenfolge: Env → Ressourcen → Command-Override → Verify):

```bash
ssh olares@172.20.0.4
# 1. Env: kubectl -n ragflow-aimighty patch deployment ragflow --type=json -p='[{"op":"add","path":"/spec/template/spec/containers/0/env/-","value":{"name":"MAX_CONCURRENT_CHUNK_BUILDERS","value":"4"}}]'
# 2. Ressourcen (CPU 10, Memory 12Gi): kubectl -n ragflow-aimighty patch deployment ragflow --type=json -p='[{"op":"replace","path":"/spec/template/spec/containers/0/resources/limits/cpu","value":"10"},{"op":"replace","path":"/spec/template/spec/containers/0/resources/limits/memory","value":"12Gi"}]'
# 3. Command-Override (close_stale 30→3600, DOC_BULK_SIZE 4→50, batch_size 64→16) — base64 aus dem Live-Deployment args kopieren, sonst: kubectl get deploy ragflow -o jsonpath='{.spec.template.spec.containers[0].args}'
# 4. Verify: kubectl exec ... -c ragflow -- sh -c 'grep -c "close_stale(age=3600)" /ragflow/api/db/db_models.py; python3 -c "from common import settings; print(settings.DOC_BULK_SIZE)"'
```

| Patch | Zweck |
|---|---|
| `MAX_CONCURRENT_CHUNK_BUILDERS=4` | 4 Chunk-Builder parallel (~2× Ingest-Durchsatz) |
| CPU-Limit 10 / Memory 12 GiB | Chart-Default (4/10) erstickt Server bei Massen-Ingests → Probe-Timeouts |
| `close_stale(age=3600)` | DB-Connection-Issue: Olares-Patch killt Pool-Connections >30s (Healthz-Thread) → pymysql `InterfaceError(0,'')`-Spam. 1h = harmlos |
| `DOC_BULK_SIZE=50` | ES-Bulks 12× größer → Indexing ~13× schneller (Gewinn aus ANZAHL, Bulk-Dauer unverändert) |
| `batch_size=16` (Code) | 64er-Batches sind 8× langsamer (CPU-Attention); 16 = Optimum |

### Kern-Lektionen (RAG)

- **`EMBEDDING_BATCH_SIZE`/`DOC_BULK_SIZE`-Env sind WIRKUNGSLOS** (nur per Code-Patch änderbar); Embedder hat globalen Inferenz-Lock → Parallelität nur über Tasks (2 Pods = 2 Slots).
- **RAPTOR/GraphRAG = dominanter Engpass** (LLM-Preprocessing vor dem Embedding, ~9/16 der Task-Zeit) → für Massen-Ingests deaktivieren (`use_raptor: false`, `use_graphrag: false`).
- **Chunking:** `parent_child` + `enable_children` + `children_delimiter:"\n"` splittet JEDE Zeile in Mini-Chunks (Faktor 35 mehr Chunks bei zeilenbasierten Texten!). Empfehlung: `chunk_token_num=256` + `delimiter:"\n\n"` (ohne Backticks — Backticks = Absatz-Split, bläht Chunks) + parent_child AUS. Small-to-Big ist in RAGFlow v0.26-Refactored für txt de facto tot.
- **FIFO-Queue:** ein Groß-Doc blockiert alle anderen → Groß-Docs separat als Letztes, oder Cancel.
- Test-KBs `perf-test-noraptor`/`perf-test-rap` nur via UI löschbar.

---

## 11. OpenCode-Tooling-Hinweise

- **`edit`-Tool:** `filePath`, `oldString`, `newString` sind Pflicht — fehlt einer → `SchemaError(Missing key at ["filePath"])`, harter Fail. Bei Unsicherheit Datei vorher mit `Read` bestätigen (Context-Truncation schneidet Parameter weg).
- **`tool_choice: "required"`** funktioniert mit beellama 0.1.3-rc1 (und über LiteLLM-Gateway); `opencode.jsonc` mit `temperature: 0.1`, `output: 2048`, `topP: 0.9` erhöht Determinismus.
- LiteLLM-Key-Berechtigungen: Zugriff nur auf freigegebene Modelle (`['analyst','expert']`) — andere Modelle → 401 `key_model_access_denied`.
- **opencode-Modelle (litellm-Provider, `~/.config/opencode/opencode.jsonc`):** `Experte` (thinking an, `enable_thinking+preserve_thinking`) vs `Experte-Fast` (`id: "Experte"` + `enable_thinking:false`). Beide senden `model:"Experte"` an LiteLLM, unterscheiden sich nur im Body. **llama-server ignoriert `reasoning_effort`/`reasoning_budget` als Intensitäts-Steuerung** (gemessen: 512/4096/16384 identisch, low/high identisch) — der einzige wirksame Hebel ist `enable_thinking` (an/aus); ohne Body-Override greift die LiteLLM-Model-Config (`enable_thinking:true`, `reasoning_effort:medium` → lange Thinking-Ketten, effektiv ~30-40 t/s sichtbar). `Experte-Fast` = volle sichtbare t/s (~52-55). Auswahl via `/models` oder `variant_cycle`.
