# Olares-Learnings — Apps für Olares OS entwickeln, paketieren, betreiben

> Stand: 15. September 2026 · Olares 1.12.6 auf einer Einzelknoten-Box (k3s)
> Gesammelt aus zwei Produkten (Insilo, Beacon), einem fremden Katalog
> (AImighty-Markt, Marcs Playbook) und den offiziellen `olares-*`-Skills.
> **Produktneutral**: `<app>` = App-Name, `<nutzer>` = Olares-Nutzer,
> `<zone>` = z. B. `kaivostudio.olares.de`, `<box>` = IP der Box.

**Wie belastbar ist was?**

- ohne Markierung: auf einer echten Box gemessen oder durch eine Ablehnung
  mit wörtlicher Fehlermeldung belegt
- **[ungeprüft]**: steht in Doku oder Playbook, von uns nicht nachgemessen
- **[Widerspruch]**: Quellen oder Messungen widersprechen sich — beide Seiten
  stehen da

Grundregel aus drei teuren Fehlern: **Eine Beschreibung ist kein Beleg.**
Nicht der Commit-Message, dem Kommentar oder der Häufigkeit einer Fundstelle
glauben — die ausführende Datei lesen und den laufenden Zustand abfragen.
Ein Exit-Code sagt nicht, ob ein Aufruf angekommen ist; eine grüne Kachel
nicht, welche Images laufen.

---

## 1. Das Plattformmodell in zwei Minuten

- Olares ist **Kubernetes (k3s) auf einem Knoten**. Jede App wird **pro
  Nutzer** installiert, in den Namespace `<app>-<nutzer>`.
  Systemdienste: `os-framework` (app-service, Markt), `os-platform`
  (Postgres/Citus, Redis/KVRocks, MinIO), `user-system-<nutzer>` (Auth).
- Olares stellt bereit, was man **nicht** selbst baut: Authentifizierung
  (Envoy-Sidecar + Auth-Dienst `beclab/auth`, Authelia-artig), Postgres,
  KVRocks (Redis-API, auf Platte), TLS, Reverse Proxy, Backup (Velero,
  unterhalb der Datenbank).
- Von außen erreichbar ist nur, was als **Entrance** im Manifest steht.
  Keine `hostNetwork`, kein `NodePort`, kein `LoadBalancer`, keine
  ClusterRole-Bindings — nur `ClusterIP`.
- **Eine Deinstallation löscht die App-Datenbank**, `Data/<app>`
  (`/app/data`) überlebt. `appCache` wird immer gelöscht, `Data` nur mit
  `market uninstall --delete-data`.
- **Ein Upgrade friert die Werte der Erstinstallation ein** (Kapitel 6).
  Das ist die folgenreichste Regel der ganzen Plattform.
- Eine App für ein ganzes Team in *einem* Datenbestand gibt es nicht:
  jedes Olares-Konto bekommt seine eigene Installation. Eine *shared app*
  hat keinen Entrance und keine URL. Wer ein Team in einer Datenbank will,
  baut eine eigene Anmeldung (Kapitel 3.6).

---

## 2. Paketierung: OlaresManifest (v3) und Helm-Chart

### 2.1 Namen und Versionen

- **Ein Name überall**: `^[a-z0-9]{1,30}$` (keine Bindestriche), und
  Ordnername = `Chart.yaml.name` = `metadata.name` = `metadata.appid`.
  `entrances[].name` = `metadata.name`, `entrances[].host` = Name des
  Service = Name des Frontend-Deployments. Sonst: „Incompatible with your
  Olares".
- **Nie umbenennen.** Die App-ID ist `md5(<app>)[:8]` und steckt in der URL.
  Eine Umbenennung ist eine Neuinstallation: neuer Namespace, neue Datenbank,
  neue Adresse. Umzug: Abzug nach `Data/<neu>` kopieren (uid 1000), neu
  installieren, Bestand nachmessen, **erst dann** die alte App deinstallieren.
- **Version an vier Stellen gleich**: `Chart.yaml` `version` und
  `appVersion`, Manifest `metadata.version` und `spec.versionName`.
  Beleg: HTTP 400 „must same".
- **Zwei Manifeste**: `OlaresManifest.yaml` im Chart (Installation) und eine
  identische Kopie im Repo-Root (Store). Die Kopie driftet, wenn nichts sie
  prüft — ein CI-Guard vergleicht beide.
- **Die erste Zeile von `upgradeDescription` nennt die ausgelieferte
  Version.** Release-Skripte heben die Felder, nicht den Text.
- **Strikte SemVer, keine führende Null.** `26.09.81` scheitert an
  `olares-cli market upgrade` (Masterminds, `invalid target version`);
  `helm lint` warnt nur. Aus so einer Version kommt man nur mit
  Deinstallation heraus.
- **Jede Änderung braucht eine neue Version**, auch reine Beschreibung oder
  Kategorie: der Katalog-Hash entsteht aus `ID:name:version` (Kapitel 9).

### 2.2 Manifest v3

```yaml
olaresManifest.version: '0.12.0'
apiVersion: 'v3'
workloadReplicas: ...        # Pflicht, oberste Ebene
options:
  dependencies:
    - name: olares
      version: '>=1.12.6-0'  # unter v3 exakt so
```

- **Olares-Pin passt zur Manifest-Generation.** v3: genau `>=1.12.6-0`
  (`must restrict to >=1.12.6-0 for apiVersion=v3`; das `-0` lässt Tages-
  und RC-Builds zu). v1 verlangte ein geschlossenes Intervall
  (`>=1.12.3-0,<1.12.6`) — und sperrte damit ausgerechnet die Version aus,
  auf der die Box lief. Die konkrete Grenze nennt die Fehlermeldung.
  [Widerspruch] Ein älterer Playbook-Eintrag nennt für v2 „HTTP 403
  incompatible"; `olares-cli chart lint` meldete v2 als OK. Nach v3 migrieren
  und die Frage ist erledigt.
- **Replikas nie fest ins Template.** app-service rendert die Installation
  mit 0 und skaliert hoch; Anhalten setzt 0. Beleg: `replicas must reference
  .Values.workloads.<name>.replicaCount`. Namen mit Bindestrich erreicht die
  Punktsyntax nicht:
  `replicas: {{ (index .Values.workloads "<app>-backend").replicaCount }}`
  plus Default in `values.yaml`. `workloads` kommt bei jedem Markt-Upgrade
  frisch aus `workloadReplicas`.
- **`permission.provider` ist ab 0.12.0 unzulässig** — und hat vorher auch
  nie einen Weg zu einer fremden App geöffnet (Kapitel 8).
- **Die Zeichenkette `OLARES_USER_` in Chart-Dateien.** Der Prüfer durchsucht
  den reinen Dateitext, auch Kommentare (`must not use OLARES_USER_* names`).
  Weg für Nutzer-Variablen: `envs:` im Manifest mit `valueFrom`, im Template
  `.Values.olaresEnv.<X>`, Default `olaresEnv: {<X>: ""}` in `values.yaml`.
  [Widerspruch] Der offizielle Skill verbietet nur app-lokale `envName` mit
  dem Präfix und nennt `valueFrom.envName: OLARES_USER_*` im Manifest den
  vorgesehenen Weg — ob der Textprüfer das dort toleriert, vor der Einreichung
  mit `chart lint` prüfen.
- **`options.runAsUser` als String `"1000"`**, nicht als Bool (`failed to
  parse response JSON: invalid character 'i' in literal false`).
- **`options.apiTimeout: 0`**, sonst kappt der Envoy-Sidecar jede Antwort nach
  15 s (auch negative Werte = 15 s). Wichtig für alles, was synchron auf ein
  Sprachmodell wartet.
- **`options.allowedOutboundPorts`** öffnet ausgehende Ports per
  NetworkPolicy (z. B. `[443]` für Modell-Downloads, `993` für IMAP).
- **`spec.runAsInternal: true` nicht verwenden** — ein Studio-Merkmal
  (Admin-Trust). Beleg: Frontend `Init:CrashLoopBackOff`, „services aren't
  ready in 20s".
- **Ressourcen**: entweder flach (`requiredCpu`, `limitedMemory`, …) oder
  `spec.resources[]`, nie beides. Die **Summe aller Container einschließlich
  Init-Containern** muss in `required*`/`limited*` passen. Beleg: HTTP 400
  `sum of container resources.limits.cpu (13) must be <= limitedCpu (6000m)`.
  Requests über der Node-Kapazität ⇒ `Unschedulable`.
- **Pflichtfelder**: `name`, `appid`, `title`, `version`, `icon`,
  `requiredDisk` (`^(?:\d+(?:\.\d+)?)[kKMGTP]?i?$`), `supportArch`.
  `chart lint` verlangt kein `appid`, `market upload` lehnt ohne ab.
- **Entrance mit Web-Oberfläche: `openMethod: window`**, sonst zeigt die
  Kachel „running" und ein Klick öffnet nichts.
- **Umgebungsvariablen in der UI änderbar** nur mit `editable: true` — in
  beiden Manifesten.
- **Icon und Bilder** müssen öffentlich mit HTTP 200 erreichbar sein (privates
  Repo ⇒ `downloadFailed`). `featuredImage`/`promoteImage` nicht aufs Icon
  zeigen lassen. Kategorien hängen vom Markt ab.
- **Manifest-Felder, die nicht tun, was man denkt**:
  `options.appScope.clusterScoped: false` setzt kein `ns-owner`-Label;
  `authLevel: public` verhindert den `check-auth`-Init nicht.

### 2.3 Helm-Chart-Regeln

- **`metadata.name` literal**, nie `{{ .Release.Name }}`. Label `app: <name>`
  setzen, sonst fehlen die Service-Endpoints.
- **Kein `.Files.Get/Glob/AsConfig`.** Olares' Renderer kennt `.Files` nicht:
  `chart repo service returned error status 500: can't evaluate field Files in
  type *hydrationfn.TemplateData`. Inhalte (z. B. SQL) per Generator-Skript
  als Literal-Block in eine ConfigMap schreiben — und im CI prüfen, dass die
  ConfigMap zur Quelle passt.
- **Keine Helm-Hooks, die Postgres/Redis brauchen.** Das Namespace-Label
  `bytetrade.io/ns-owner` kommt erst **nach** erfolgreichem `helm install`,
  und bis dahin sperrt die NetworkPolicy die Middleware. Beleg: `failed
  post-install: timed out waiting for the condition`. Migrationen deshalb als
  **Init-Container mit Wiederholschleife** (z. B. 60 × 5 s).
- **app-service parst Templates als rohes YAML, bevor Helm rendert.** Doppelte
  Schlüssel in `values.yaml` brechen den Sync; Block-Skalare (`|`) sauber
  einrücken; `{{ }}` außerhalb von Block-Skalaren quoten.
- **`image:` als ein gequoteter String.** Als Mapping gelesen ⇒
  `failed to resolve reference mirrors.olares.com/library/…`.
- **Nur `ClusterIP`-Services.**
- **Kein PodDisruptionBudget bei `replicas: 1`** auf einem Knoten
  (`minAvailable: 1` ⇒ `kubectl drain` hängt).
- **Den gepackten Tarball linten, nicht den Ordner** — der Prüfer verlangt
  Ordnername = Chart-Name:
  `olares-cli chart lint dist/<app>-X.Y.Z.tgz --with-rbac --with-security-context`
- **Nur einmal gzippen**, base64 direkt aus `helm package`
  (sonst `invalid tar header`).
- **Safari entpackt `.tgz` beim Download zu `.tar`** ⇒ „unexpected EOF". Mit
  `gh release download … --clobber` holen.
- **`values.yaml` ist öffentlich.** Keine Schlüssel, keine Default-Secrets,
  keine Adresse eines Dienstes, die nur auf einer Box stimmt. Die gerenderten
  Templates liegen auf der Box base64-kodiert und sind auslesbar.
- **Secrets im Chart**: `randAlphaNum` erzeugt bei jedem Render neu. Beide
  Seiten (z. B. Frontend und Backend) aus demselben Secret lesen und per
  Checksum-Annotation gemeinsam neu starten, sonst 401 nach einem einzelnen
  Neustart. Stabil über Upgrades: `lookup` auf das vorhandene Secret, sonst
  `randAlphaNum` (offizieller Skill).

### 2.4 Die Guards, die sich bewährt haben (`check-chart.sh`)

Jede Zeile entstand aus einer echten Ablehnung. Nach einer Ablehnung **erst
den Guard schreiben, dann den Fix.**

| Guard | Warum |
|---|---|
| Versionsgleichlauf (Chart, Manifest, `versionName`) | 400 „must same" |
| Root-Manifest = Chart-Manifest | Store liest das Root-Manifest |
| `upgradeDescription` nennt die Version | Text veraltet still |
| `olares-cli chart package` + `chart lint` auf das Paket | dieselbe Prüfung wie der Store |
| Neue Top-Level-Werteschlüssel nie direkt dereferenziert | eingefrorene Werte ⇒ `nil pointer` |
| Olares-Pin passend zur Manifest-Generation | 400 bei falschem Intervall |
| hostPath ⇒ `strategy: Recreate` | 400 „can not enable rolling update with hostpath" |
| Summe der Ressourcen ≤ Manifest-Budget | 400 beim Markt |
| Root-Container nur aus beclab-Images | 400 „non-beclab image … root-equivalent" |
| Kein `.Files.*`, keine `helm.sh/hook`, kein `runAsInternal` | siehe oben |
| Image-Tag aus `.Chart.AppVersion`, kein Pin in `values.yaml` | eingefrorene Werte |
| `helm template` mit Stub **und** mit Werten ohne neue userspace-Schlüssel ⇒ kein leerer `hostPath.path` | Bestandsboxen haben die Schlüssel nicht |
| Pflichtfelder + Namensregex | Linter |

---

## 3. Authentifizierung, Envoy-Sidecar, Entrances

### 3.1 Wer einen Sidecar bekommt

- **Jeder Pod mit Entrance** bekommt einen Envoy-Sidecar
  (`olares-envoy-sidecar`, Pod 2/2) und Init-Container (`check-auth`,
  `render-envoy-config`, `olares-sidecar-init`). Pods ohne Entrance bekommen
  nichts davon.
- **Der Sidecar fängt jeden eingehenden TCP-Verkehr ab, auch clusterinternen**
  (`iptables -A PROXY_INBOUND -p tcp -j PROXY_IN_REDIRECT`). Beleg: vom
  eigenen Frontend-Pod auf `http://<app>-backend:8000` ⇒ 401
  `ext_authz_denied`, mit und ohne `X-Bfl-User`.
- **Folgerung, das Standardmuster**: Nur das Frontend bekommt einen Entrance.
  Das Backend hat keinen und wird vom Frontend **serverseitig** über
  `http://<app>-backend:<port>` angesprochen. Der Browser ruft nur die eigene
  Origin (`/api/*`) auf. Eine API-Entrance direkt aus dem Browser endet in
  Auth-302 ⇒ CORS-Block.
- **Braucht eine App einen zweiten, öffentlichen Eingang** (Webhooks,
  Einladungslinks), bekommt er ein **eigenes Deployment**, nie den
  Backend-Pod. Beleg (Beacon 0.1.10): Entrance am Backend ⇒ der Sidecar wies
  die Aufrufe des eigenen Frontends an `/api` mit 401 ab; 0.1.12 mit eigenem
  Deployment `<app>-links` behoben. Faustregel: ein Entrance zeigt nur auf
  Pods, die sonst niemand im Cluster aufruft.

### 3.2 Identität: `X-Bfl-User` und `Remote-User`

- **Der Sidecar setzt `X-Bfl-User` nicht.** Gemessen über den Admin-Port
  (`config_dump`): kein `request_headers_to_add`; `allowed_upstream_headers`
  kennt nur `authorization`, `proxy-authorization`, `remote-*`, `authelia-*`.
  `x-bfl-user` steht nur unter dem, was Envoy **an** den Auth-Dienst schickt.
- **Nach oben kommt `Remote-User`** (und `Remote-Groups`, z. B.
  `owner,lldap_admin`). Das ist die verlässliche Identität.
- [Widerspruch] Ein Chart-Kommentar in Beacon und der offizielle Skill nennen
  `X-Bfl-User` „gateway-injected". Möglich, dass ein Gateway **vor** dem
  Sidecar ihn setzt; am Pod angekommen ist er in unseren Messungen nicht
  verlässlich.
- **Muster**: Der Next.js-Server übernimmt `Remote-User` und überschreibt damit
  serverseitig, was der Browser als `X-Bfl-User` behauptet. Lokal ohne Envoy
  ersetzt ein `DEV_USER` den fehlenden Kopf — **auf der Box bleibt der leer**,
  dort ist ein fehlender Kopf ein Fehler.
- Der Auth-Dienst ist `beclab/auth`, kein Standard-Authelia: Verhalten messen,
  nicht aus der Authelia-Doku übernehmen. Ohne Sitzung: 302 auf `/api/verify/`.

### 3.3 Dienste ohne Entrance sind offen

- **Ein Pod ohne Entrance ist für jeden Pod im Cluster erreichbar**, und
  `X-Bfl-User` ist dort frei behauptbar: „wer das Backend erreichte, war, wer
  er zu sein behauptete".
- **Fix**: gemeinsames Geheimnis aus einem vom Chart erzeugten Secret. Der
  Frontend-Server hängt es serverseitig an; das Backend lehnt ohne ab.
  Health-Endpunkte bleiben frei (Kubelet-Probes haben kein Geheimnis).
  Nachweis aus einem fremden Pod: 401.
- **Env-Namen exakt abgleichen.** pydantic-settings las `INTERNAL_TOKEN`, der
  Chart setzte `INSILO_INTERNAL_TOKEN` — der Torwächter war offen, und nichts
  meldete es. Ein Test, der Chart-Name und Code-Name zusammenhält.

### 3.4 authLevel

- **Gemessen** (Beacon): `internal` ⇒ ohne Box-Sitzung 302 zur Anmeldung;
  `public` ⇒ 200 ohne Sitzung.
- [Widerspruch] Die offizielle Tabelle sagt: `private` nur Eigentümer,
  `public` jeder angemeldete Nutzer, `internal` nur clusterintern. Marcs
  Playbook: `internal` LAN ohne, extern mit Auth. Vor einer Entscheidung auf
  der eigenen Box messen.
- **Bei `public` ist `X-Bfl-User` aus dem Internet fälschbar**
  (`curl -H 'X-Bfl-User: <nutzer>'` ist dann der Eigentümer). Vor `public`
  eine eigene Anmeldung einschalten, die den Kopf ignoriert — erst der Modus,
  dann der Entrance.
- **authLevel lässt sich per Klick ändern** (Settings › Applications › App ›
  Authentication level), wirkt sofort und am Manifest vorbei. Den wirksamen
  Stand an der Box lesen:
  `kubectl get applications.app.bytetrade.io <ns>-<app> -o jsonpath='{range .spec.entrances[*]}{.name}{"  "}{.authLevel}{"\n"}{end}'`
  Ab 1.12.7 überlebt so eine Änderung Upgrades; auf 1.12.6 kann ein Upgrade
  den Chart-Wert zurückschreiben [ungeprüft].
- Keine eigene 2FA-Unterregel pro App im Manifest; Pfadregeln bei Bedarf per
  `olares-cli settings apps policy set --sub-policy "uri=/api,policy=public"`.

### 3.5 Adressen und Entrances

- **Adresse: `https://<md5(app)[:8]><index>.<nutzer>.<zone>`.** `<app>.<nutzer>.<zone>`
  gibt es nur für Systemapps — stundenlang am falschen Host gemessen (421).
  Im Chart `.Values.domain.<entrance>` verwenden, live mit
  `olares-cli settings apps list` (Spalte URL) nachsehen, nicht selbst
  ausrechnen. Merkbarer Name nachträglich:
  `olares-cli settings apps domain set <app> <entrance> --third-level <id>`
  (nicht im Chart setzen; eine Deinstallation löscht ihn).
- **Ein zweiter Entrance ändert die Adresse des ersten** (ohne Index ⇒ mit
  Index).
- **Neue Entrances und Policies kommen nur über den Markt auf die Box.**
  `helm upgrade` liest das Manifest nicht neu; erst das Markt-Upgrade schreibt
  `spec.entrances`/`settings.policy` und injiziert den Sidecar. Abgleich
  Manifest ↔ Application-Objekt per Skript lohnt sich.
- **`status.entranceStatuses` bleibt nach einem Upgrade unvollständig.** Er
  wird nur beim ersten Anlegen gefüllt (`application_controller.go`,
  `createApplication`); `updateApplication` schreibt `spec.entrances`, fasst
  den Status nicht an. Für die Erreichbarkeit ohne Belang (gemessen: Eintrag
  entfernt ⇒ 200, gesetzt ⇒ 200), der neue Entrance fehlt nur in der
  Statusanzeige. Nicht als Fehlerursache jagen.
- Ein öffentlicher Entrance ohne Root-Route antwortet mit 404 aus der App —
  Erreichbarkeit an `/health` messen.
- Hinter dem Next-Proxy steht im `Host` der interne Dienst: Herkunft über
  `X-Forwarded-Host` prüfen, `Secure`-Kekse an `X-Forwarded-Proto` hängen.

### 3.6 Eigene Anmeldung (wenn ein Team eine Datenbank teilen soll)

- Server-seitige Sitzung, argon2id, Keks `HttpOnly`/`SameSite=Lax`/`Secure`.
- **Die Erstinstallation braucht eine Ausnahme**: auf einer leeren Datenbank
  gibt es keinen Nutzer, kein Passwort, keine Einladung ⇒ ohne Ausnahme „401
  auf alles, dauerhaft". Solange niemand ein Passwort hat, gilt der
  Olares-Kopf. Einen Test, der Nutzer/Orgs/Rollen wirklich leer räumt —
  „auf meiner Box läuft es" beweist nichts.
- Auf einer frischen Box ist **kein SMTP** eingerichtet: Zugang und Rücksetzen
  dürfen nicht an Mail hängen. Passwörter nicht in Olares-Umgebungsvariablen
  (die sind „shared settings for your apps"); Rettungsweg über eine Code-Datei
  im App-Datenordner.
- **Mehrere Boxen**: Kennungen unterscheiden sich je Box. Jede Seite, die eine
  Identität setzt (Einladung, Rücksetzen), nennt Kennung, Herkunfts-Box und ob
  etwas ersetzt wird — ein Link hätte sonst das Passwort eines Kontos auf einer
  **fremden** Box gesetzt. In Handybreite prüfen (die Adresszeile ist dort
  abgeschnitten).
- Nach einer Neuinstallation zeigen Browser-Kekse auf gelöschte Datensätze:
  maschinenlesbar melden und den Keks wegräumen.

---

## 4. Middleware: Postgres, KVRocks, MinIO

### 4.1 Injizierte Werte

- Deklaration im Manifest unter `middleware:`; Olares injiziert zur Laufzeit:
  - `.Values.postgres.host/port/username/password`,
    `.Values.postgres.databases.<name>` (Map)
  - `.Values.redis.host/port/password/namespace` (Einzelstrings)
  - `.Values.userspace.appData/appCache/appCommon/userData`,
    `.Values.bfl.username`, `.Values.user.zone`, `.Values.cluster.arch`,
    `.Values.domain.<entrance>`, `.Values.olaresEnv.*`
- **Die Form gegen die Box prüfen, nicht raten.** Eine falsche Form zeigt sich
  nur als `downloadFailed` ohne Retry; der Grund steht in
  `kubectl logs -n os-framework app-service-0`. Ohne `middleware:`-Block:
  `nil pointer .Values.postgres.host`.
- **`middleware.redis` ohne `password:`-Feld.** `password: auto` wird wörtlich
  als Passwort „auto" injiziert. Ohne Feld erzeugt Olares ein Secret.
- `middleware.postgres` mit `username` und `databases[].extensions`
  (z. B. `vector`, `pg_trgm`, `pgcrypto`, `uuid-ossp`).
- Für `helm lint`/`template` lokal eine **Stub-Values-Datei** — und zusätzlich
  eine Variante **ohne** später hinzugekommene Schlüssel (Kapitel 6).
- Postgres ist ein Citus-Image; die Version wird mal als 16, mal als 17
  genannt [Widerspruch] — mit `select version()` nachsehen.
- `OLARES_ZONE` liegt in jedem Pod. Wer damit „eigene Box" erkennt, vergleicht
  an einer Punktgrenze (sonst gilt `boese<nutzer>.olares.de` als eigene).

### 4.2 Datenbank-Praxis

- **Migrationen im App-Image mit einem echten Treiber**, eine Datei pro
  `execute`. `psql -f` hing gegen Citus bei DDL mit vielen Statements
  stundenlang ohne Ausgabe (Ursache nur vermutet); asyncpg: < 2 s.
- **Ein Init-Migrationsrunner ohne Buchführung führt jede Datei bei jedem
  Start aus.** Jede Migration idempotent (`if not exists`, `do $$ … if exists
  … $$`) — auch dann, wenn eine spätere ihre Voraussetzung entfernt (Beleg:
  `column "…" does not exist` ⇒ CrashLoop). Ein Seed mit
  `on conflict do update` überschreibt eine Migration Sekundenbruchteile
  später: **was der Seed besitzt, gehört in den Seed.**
- **Variante „Duplicate heißt schon angewandt"** (Beacon): jede Datei ein
  `execute()` = eine implizite Transaktion; `DuplicateTable/Object/Column`
  oder `UniqueViolation` ⇒ zurückgerollt, Datei gilt als erledigt. Zwei
  Folgen, die man kennen muss:
  - **Eine veröffentlichte Migrationsdatei nie erweitern.** Wirft ihr erster
    Befehl beim zweiten Lauf `DuplicateObject` (z. B. `create type`), rollt
    alles dahinter mit zurück — ein nachgetragener Befehl kommt auf keiner
    Bestandsbox je an, auf einer frischen schon. Neue Änderung = neue Datei.
  - **Datenumzüge in Migrationen müssen ebenfalls wiederholbar sein** (sie
    laufen bei jedem Start, sobald die Datei nicht an einem Duplicate
    scheitert): so formulieren, dass der zweite Lauf nichts mehr findet.
- **`helm rollback` nimmt Migrationen nicht zurück.** Rückweg-SQL bereithalten.
- **Row Level Security**:
  - Der App-Nutzer ist kein Superuser, hat kein `bypassrls`. Die
    Tabelleneigentümerin umgeht RLS ohne `force`, **Superuser umgehen RLS auch
    mit `force`.** Lokal deshalb mit einer Nicht-Superuser-Rolle entwickeln und
    migrieren, die auch Eigentümerin ist — wie auf der Box.
  - `force row level security` liefert **leere Ergebnisse statt Fehlern**:
    Health-Checks, Hintergrundjobs, Schlüsselsuche, Start-Prüfungen sehen
    nichts. Folgen gemessen: falscher Health-Status, abgewiesener gültiger
    API-Key, bei jedem Start eine weitere Default-Zeile (16 Dubletten).
    Expliziten Dienstkontext setzen und per Test nach kontextlosen Zugriffen
    suchen. Zählungen nur mit Nutzerkontext.
  - **Das gilt auch für die Migration selbst.** Die Migrationsrolle ist auf
    der Box Eigentümerin, aber kein Superuser — unter `force` sieht ein
    `insert … select` oder `delete` in einer Migration **null Zeilen und
    meldet Erfolg**. Lokal als Superuser getestet, fällt das nie auf. Muster
    (Beacon 0030): im Umzug `disable row level security` auf allen beteiligten
    Tabellen, verschieben, dann `enable` **und** `force` wieder setzen — alles
    in derselben Datei, also derselben Transaktion. Test: Zeilen säen, den
    Umzugsabschnitt als App-Rolle ausführen, Ergebnis zählen.
  - Tests gegen echtes Postgres, nicht gegen Attrappen — RLS gibt es nur dort.
- **Mit Connection-Pool**: `pg_advisory_xact_lock` statt Session-Lock,
  `set local` statt `set` (sonst klebt der Zustand an der Pool-Verbindung).
- **Sicherung**: `pg_dump` als App-Nutzer scheitert unter `force`
  (`query would be affected by row-level security policy`); `PGOPTIONS` hilft
  nicht, `--enable-row-security` sichert still nur Sichtbares. Als Superuser:
  `kubectl exec -n os-platform citus-0 -- pg_dump -U olares -d <db> --no-owner --no-acl > abzug.sql`
  Eine Sicherheitsmaßnahme ändert die Betriebsabläufe um sie herum — die
  Sicherung war vier Tage kaputt, unbemerkt. Nach so einer Änderung Backup
  **und** Restore einmal durchspielen.

### 4.3 Die Datenbank überlebt keine Deinstallation

- Nach Deinstallation + Installation ist die Datenbank neu (neue IDs), Dateien
  in `/app/data` sind noch da — und hängen, wenn ihr Pfad eine alte ID trägt.
- **Muster**: Die App schreibt einen Abzug ihrer Einrichtung (IDs, Einstellungen,
  bei Bedarf den ganzen Bestand) nach `/app/data` (0600, enthält Zugangsdaten)
  und spielt ihn beim ersten Start in eine **leere** Datenbank zurück — nur in
  die erste Organisation, nichts überschreiben. Ein Test, der bricht, sobald
  eine neue Tabelle oder Spalte weder im Abzug steht noch ausdrücklich
  ausgenommen ist. Sitzungen und Anmeldebremsen gehören nicht hinein.
- Menschen den Ordner als „Data › <app>" in der Dateien-App nennen, nicht
  `/app/data`.
- **Verschlüsselte Zugangsdaten: Schlüssel und Kryptotext leben getrennt.**
  Liegt der Schlüssel als Datei in `/app/data` und der Kryptotext in der
  Datenbank, können beide auseinanderlaufen — gelöschter Datenordner,
  zurückgespielte Datenbank einer anderen Installation, zweite Box. Dann
  steht in der Spalte weiter etwas, das sich nicht öffnen lässt. Beleg
  (Beacon bis 0.9.0): Die Maske fragte „ist die Spalte gefüllt?" und zeigte
  „hinterlegt", der Dienst bekam ein leeres Geheimnis ⇒ 401, zwei Tage beim
  falschen Schlüssel gesucht. **Drei Zustände unterscheiden** — nichts da,
  da und lesbar, da und verloren — und „verloren" laut anzeigen. Abzug und
  Schlüsseldatei gehören in dieselbe Sicherung.

### 4.4 MinIO

- **Praktisch nicht nutzbar**: `tapr-s3-svc.os-platform:4568` ist per
  NetworkPolicy gesperrt, es gibt kein dokumentiertes `middleware.minio`, die
  Zugangsdaten liegen in undokumentierten Secrets (Upload ⇒ HTTP 500).
  Dateien auf hostPath (`appData`) ablegen.

---

## 5. Storage und Rechte

- **Erlaubte Orte**: `/app/data` (`permission.appData`, dauerhaft, überlebt
  Deinstallation), `/app/cache` (`appCache`, gilt als flüchtig), `/app/Home`
  (Nutzerdateien), `appCommon` (gemeinsamer Ordner mehrerer Apps). Auf der Box:
  `/olares/userspaces/<nutzer>/Data/<app>/`.
- **hostPath aus `.Values.userspace.*`** mit `type: DirectoryOrCreate`.
- **`.Values.userspace.appData` ist der Host-Pfad**, nicht der Pfad im
  Container. Im Code immer den `mountPath` (`/app/data`) verwenden — ein Abzug
  schrieb sonst nie, und der Fehler wurde still geschluckt.
- **hostPath gehört root** und überdeckt das `chown` aus dem Dockerfile
  (`PermissionError`). `fsGroup` greift bei hostPath nicht. Fix: Init-Container
  mit `runAsUser: 0`, Hauptcontainer `runAsUser: 1000`.
- **Root-Container nur aus beclab-Images**: `non-beclab image "busybox:1.36"
  runs with root-equivalent securityContext` ⇒
  `docker.io/beclab/aboveos-busybox:1.37.0`.
- **`chown` nur auf den eigenen Unterordner.** Der offizielle Skill nennt
  `chown -R` zur Laufzeit eine rote Linie. **Den gemeinsamen `appCommon`-Ordner
  nie auf oberster Ebene chownen** — er gehört allen Apps mit dieser
  Berechtigung. Alles dort ist für jede dieser Apps lesbar.
- **hostPath erzwingt `strategy: Recreate`** (400 „can not enable rolling
  update with hostpath"; ohne `strategy` gilt RollingUpdate). Folge: ein
  kaputtes Update ist echte Downtime — das Sicherheitsnetz alter Pods fehlt.
  Alternative: PVC.
- **`userspace.<x>` gibt es nur für Rechte, die bei der Installation angefordert
  wurden** (Kapitel 6). Mount, Umgebungsvariable und `mkdir` für eine neue
  Berechtigung immer bedingt:
  `{{- if (.Values.userspace).appCommon }} … {{- end }}`
- Das Arbeitsverzeichnis des Images ist nicht beschreibbar; Zustandsdateien
  (z. B. Celery-Beat) nach `/app/cache`.

---

## 6. Upgrades und eingefrorene Werte

**Die Regel:** Ein Markt-Upgrade spielt die **bei der Installation**
gespeicherten Werte zurück und übernimmt die Vorgaben des neuen Charts nicht.
Chart-Metadaten (`.Chart.AppVersion`) kommen dagegen frisch an.

- **Belegt, mit Erfolgsmeldung**: Kachel, `helm history` und alle Health-Checks
  meldeten die neue Version, die Pods liefen mit den alten Images; im
  Release-Secret stand `tag: <alt>`, obwohl das veröffentlichte Chart
  byte-identisch `tag: <neu>` trug.
- **Fix Image-Tag**:
  ```yaml
  image: "ghcr.io/…/<app>-backend:{{ .Values.images.backend.tag | default .Chart.AppVersion }}"
  ```
  und `tag: ""` in `values.yaml`. Ein CI-Guard verbietet einen Pin.
- **Neue Werteschlüssel fehlen** auf Bestandsinstallationen: `nil pointer
  evaluating interface {}.baseUrl` (Markt), `index of untyped nil`
  (`--reuse-values`). Nie direkt dereferenzieren:
  `{{ (default (dict) .Values.neu).feld | default "" }}` oder
  `(.Values.neu).feld`. `dig` geht nicht (`.Values` ist `chartutil.Values`).
  `helm template -f values.yaml` mischt die Defaults ein und **kann den Fehler
  nicht zeigen** — statisch prüfen.
- **Neue Berechtigungen** (`userspace.appCommon` & Co.) sind ebenfalls neue
  Werteschlüssel. Beleg: Das Update rendert `hostPath.path:` leer, die API
  lehnt ab: `spec.template.spec.volumes[2].hostPath.path: Required value`.
  Offen: ob ein Markt-Update eine neu angeforderte Berechtigung nachträglich
  einträgt [ungeprüft].
- **Boolesche neue Schlüssel mit `hasKey` lesen.** `| default true` macht aus
  einem gesetzten `false` wieder `true`.
- **Schalter, die auf laufenden Boxen sicher ankommen müssen**, als Literal ins
  Template schreiben, nicht in `values.yaml`.
- **Manuelles Ausrollen per Helm** (Helm 3.9 auf der Box, kein
  `--reset-then-reuse-values`):
  ```bash
  helm upgrade <app> /tmp/<app>-X.Y.Z.tgz -n <app>-<nutzer> --reuse-values \
    --set images.frontend.tag= --set images.backend.tag= \
    --set workloads.<app>.replicaCount=1 --set workloads.<app>-backend.replicaCount=1
  ```
  Alte `--set`-Pins kommen über `--reuse-values` zurück und müssen einmal
  geleert werden (`helm get values <app> -n <ns>` zeigt sie).
- **Ein Chart-Upload im Markt** führt `helm upgrade` aus, aber bei gleichem
  Render-Ergebnis bleiben die Pods stehen; „Open" heißt installiert, nicht
  aktuell. Ein `helm upgrade` am Markt vorbei funktioniert, der Markt zeigt dann
  aber die alte Version.
- **Live-`kubectl patch`** überlebt Pod-Neustarts, aber kein Upgrade,
  Stop/Resume oder Reconcile. Einen gepatchten ApplicationManager-Status setzt
  der Controller zurück.
- **Ein Upgrade einer angehaltenen App** endet wieder in `stopped` (offizieller
  Skill).
- **Nach jedem Ausrollen messen, was läuft** — die Helm-Meldung reicht nicht:
  ```bash
  kubectl get pods -n <app>-<nutzer> \
    -o custom-columns='N:.metadata.name,I:.spec.containers[*].image,R:.status.containerStatuses[0].ready'
  ```
- **Vor jedem Ausrollen gegen die echte API trocken prüfen** — ändert nichts:
  ```bash
  helm get values <app> -n <ns> -a -o yaml > /tmp/w.yaml
  helm template <app> /tmp/<app>-X.Y.Z.tgz -n <ns> -f /tmp/w.yaml > /tmp/r.yaml
  kubectl apply --dry-run=server -f /tmp/r.yaml -n <ns>
  rm /tmp/w.yaml /tmp/r.yaml   # enthalten Secrets im Klartext
  ```
  Der Stub-Render hätte den leeren hostPath nie gezeigt; die echten Werte schon.

---

## 7. Images und Releases

- **GHCR-Pakete sind standardmäßig privat** ⇒ Olares kann sie nicht ziehen
  (`registry_error`). Pro Paket auf Public stellen. Tags ohne `v`.
- **Vor jeder Listung prüfen, dass jeder Image-Tag existiert** — ein Katalog
  listete eine Version zwei Tage lang, alle Images gaben 404.
- **Chart-Version und Image-Tag getrennt denken**, aber CI prüfen: ein Build,
  der die Image-Version vom Git-Tag ableitet, baut auch bei „nur Chart" und
  taggt falsch. Ein `decide`-Schritt, der im Zweifel baut.
- **Image-Größe**: CPU-Torch zuerst installieren
  (`pip install --index-url https://download.pytorch.org/whl/cpu torch`),
  sonst zieht `sentence-transformers` CUDA mit (2,8 GB ⇒ 403 MB).
  `node:alpine` hat schon uid 1000 (`deluser node` vor dem eigenen Nutzer);
  `npm ci --omit=dev` nimmt Build-Werkzeuge wie Tailwind mit weg.
- **Image-Cache auf der Box**: `crictl` braucht
  `/var/run/containerd/containerd.sock`; nur `crictl pull` registriert im
  CRI-Store (`ctr import` reicht nicht); bei CrashLoop mit veraltetem Image
  `crictl rmi` + `rollout restart`.
- **Reihenfolge**: Images bauen ⇒ auf der eigenen Box installieren und
  `running` messen ⇒ **erst dann** in einen Katalog. Eine App, die nie
  `running` erreicht hat, gehört in keinen Markt.

---

## 8. Netzwerk und Zugriff zwischen Apps

- **NetworkPolicy `user-system-np`** lässt nur Namespaces mit
  `bytetrade.io/ns-owner=<nutzer>`, `ns-type=system` oder `ns-shared="true"` an
  die System-Middleware. Das Label setzt BFL im Installationsablauf
  (Application-CR). **Manuelles Labeln entfernt ein Webhook in unter einer
  Sekunde**, einen NetworkPolicy-Patch baut der Reconciler zurück.
  Erfolgszeichen: `app-np` statt `others-np`.
  [Widerspruch] Frühe Notizen nannten den Upload-Pfad eine Sackgasse (kein CR,
  kein Label); spätere Installationen per `olares-cli market upload` +
  `install` liefen. Neue Entrances/Policies kamen trotzdem nur über den Markt.
- **Pod ⇒ Service einer anderen Nutzer-App**: Zeitüberschreitung
  (NetworkPolicy). **Pod ⇒ Envoy-geschützte App**: 400 `cannot get user name
  from header` bzw. 401. Ein Weg über `permission.provider` war nie belegt und
  ist unter v3 unzulässig.
- **Pod ⇒ öffentliche Entrance-URL einer anderen App funktioniert.** Muster für
  Webhooks zwischen Apps: Empfänger mit `public`-Entrance, Signatur (HMAC über
  den rohen Body) ist das Tor. Ein `internal`-Entrance leitet auf die Anmeldung
  um. Alternative: selbst abholen — ausgehend ist alles offen.
- **Adressen fremder Apps nie als Vorgabe ausliefern.** Die URL hängt an der
  App-ID der Installation; jede Vorgabe ist auf einer anderen Box falsch. Den
  Nutzer eintragen lassen und „nicht eingerichtet" sauber anzeigen.
- **Shared Apps** sind per Service-DNS erreichbar
  (`<svc>.<app>-shared.svc.cluster.local`, finden mit
  `kubectl get svc -A | grep <name>`). Mit `type: application` in den
  Abhängigkeiten injiziert Olares `.Values.svcs.<svc>_host/_ports`
  [ungeprüft].
- **Zum Messen am Envoy vorbei** (vom Mac aus, eine ssh-Sitzung, sonst stirbt
  der Forward mit ihr):
  ```bash
  ssh -f -L 4002:127.0.0.1:4002 olares@<box> \
    'KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl port-forward -n <ns> pod/<pod> 4002:4000 --address 127.0.0.1'
  ```
  **Ein Port-Forward umgeht Envoy und Auth** — Messungen darüber sagen nichts
  über Größen- oder Zeitlimits der Eingangsschicht. Beim Aufräumen nicht
  `pkill -f "port-forward …"` über ssh: das Muster trifft die eigene
  ssh-Sitzung.
- **Beobachtet an Box-Diensten**: Das LiteLLM-Modell „chat" denkt vor der
  Antwort (mit `max_tokens` 1800 kam nur das Nachdenken an ⇒ 6000–16000
  geben); nicht freigegebene Modellnamen ⇒ 401 „key not allowed". Speaches:
  Stimmen stehen unter `GET /v1/models`, `/v1/audio/speech/voices` gibt 404,
  Modelle laden per `POST /v1/models/{id}` minutenlang. SearXNG: Parameter
  `engines` weckt abgeschaltete Anbieter, `formats: [html, json]` muss aktiv
  sein, die Box-IP wird mit der Zeit gesperrt.
- **SearXNG auf der Box, Anbieter einzeln gemessen (15.9.2026)**: Bing
  antwortet; DuckDuckGo, Brave, Startpage, Karmasearch, Mojeek und Qwant sind
  gesperrt; Google liefert leer. Yandex bewusst nicht mitfragen. Ohne
  `engines` gelten die Vorgaben der Instanz, und die wandern mit jeder neuen
  Sperre ins Leere — „ging mal, geht nicht mehr" ohne Codeänderung. Deshalb
  eine **ausdrückliche
  Anbieterliste** mitschicken und bei leerem Ergebnis sagen, dass die
  Anbieter sperren, statt „nichts gefunden". Messen pro Anbieter:
  `…/search?q=<x>&format=json&engines=<name>` ⇒ `unresponsive_engines` lesen.
  Für verlässlichen Betrieb ist ein Such-API-Dienst (Tavily, Brave) die
  robustere Wahl; SearXNG als kostenloser Rückfall.

---

## 9. Markt und Distribution

### 9.1 Drei Wege

| Weg | Sichtbarkeit | Wofür |
|---|---|---|
| Upload (`olares-cli market upload`, UI „Upload custom app package") | eine Box | Entwicklung; registriert die Version, ersetzt kein Deployment |
| Eigene **Market Source** (z. B. Cloudflare Pages), Box pollt alle 5 min | jede Box, die die Quelle einträgt | Kundenauslieferung, privat, voller Installationsablauf |
| PR an `beclab/apps` (GitBot, `owners`/`submitter`) | weltweit | öffentliche Produkte, Tage bis Wochen |

- `market upgrade -s upload` erlaubt dieselbe Version (überschreibt);
  `upload`/`delete` gehen immer in die Quelle `upload`.
- Quellen stehen unter **Market → Settings → Market source**, nicht in
  `olares-cli market --help` (dort nur eingebaute Namen).

### 9.2 Market-Source-API

```
GET  /api/v1/appstore/hash        → Katalog-Hash, Basis der Sync-Entscheidung
GET  /api/v1/appstore/info        → alle Apps (id = md5(name)[:8])
POST /api/v1/applications/info    → Details, Body {"app_ids":["<id>"]}
GET  /api/v1/applications/<n>/chart → Chart als gzip
```

- **Hash = MD5 über sortierte `ID:name:version`.** Titel, Kategorie,
  Beschreibung ändern ihn nicht ⇒ Olares synchronisiert nicht ⇒ **Version
  heben.** Ein getauschtes Chart unter derselben Nummer holt eine schon
  synchronisierte Box nie.
- **Detail-Pflichtfelder**: `title`, `i18n["en-US"].metadata.title`,
  `i18n["en-US"].spec.fullDescription`, `chartName` = `<name>-<version>.tgz` =
  Schlüssel in der Chart-Tabelle. Passt der Schlüssel nicht ⇒ 404, der Store
  zeigt die alte Version.
- **base64 jedes Mal frisch** aus dem Paket (`base64 -i x.tgz | tr -d '\n'`),
  sonst Chart 500 / Cloudflare error 1101.
- **Lokal beweisen, bevor etwas rausgeht** — alle vier Endpunkte:
  ```bash
  npx wrangler pages dev functions --port 8788
  # appstore/info listet die App · applications/info trägt chartName + i18n
  # chart liefert 200 und ist sha256-gleich mit dem Paket · hash hat sich bewegt
  ```
- **Vor dem Merge den Live-Katalog gegen den Branch vergleichen** (Name +
  Version jeder App). Ein Deploy aus dem Repo ersetzt, was jemand direkt
  aufgespielt hat. Maßstab ist die Git-Historie, nicht der Live-Moment.
  Python-`urllib` bekommt von Cloudflare 403, `curl` geht. Die Edge braucht
  1–2 Minuten; ein 404 direkt nach dem Deploy heißt nichts.
- **Veraltete eigene PRs schließen** — gemergt würden sie die Version
  zurücksetzen.
- **Nach dem Merge ist nichts ausgerollt.** Kette: Edge 1–2 min ⇒ Box pollt
  (bis 5 min) ⇒ Update erscheint ⇒ **ein Mensch drückt „Upgrade"** ⇒ Pods
  nachmessen. Live-Katalog-Chart per sha256 gegen das Paket vergleichen, bevor
  jemand drückt. „Im Markt" und „auf der Box" getrennt melden — auf Kais Box
  lief 0.9.6, während der Markt 0.9.9 trug.
- Mit `gh pr create` im Fork-Checkout: `--head <branch>`, nicht
  `--head <owner>:<branch>` (⇒ „No commits between").
- Cloudflare-Auto-Deploy war bei Marc bewusst aus (die Website wurde einmal
  überschrieben); bei uns löst der Merge „Deploy to Cloudflare Pages" aus —
  vorher klären, was gilt.

### 9.3 Bekannte Katalog-Störungen

- **`render failed or timed out` (500)** nach dem Abgleich ist meist ein
  Timeout (chartrepo-Frist ~3 s [ungeprüft]). Auf „Saved version history" im
  `chartrepo-deployment` warten, Updates-Seite neu laden; sonst Version heben.
  Gescheiterte Apps landen in einer render-failed-Liste ohne Retry.
- **`raw_data` klemmt / entfernte Apps bleiben sichtbar**: einziger dauerhafter
  Fix ist Market Source entfernen, 5 s warten, neu hinzufügen. Der Sync-Knopf
  leert den Cache nicht.
- **`hash comparison skipped`**: Version heben oder chartrepo, dann market neu
  starten.
- **`downloadFailed`** ist ein Sammelfehler (Render, Linter, Icon-404,
  Image-Resolve) und kann die Deinstallation blockieren.
- **Markt ohne UI** (z. B. wenn „Remove" nicht löscht): Port-Forward auf
  `os-framework/market-deployment` (Container `appstore-backend`, 8080) mit
  Kopf `X-Bfl-User`; `PUT /app-store/api/v2/apps/<app>/upgrade`,
  `DELETE /app-store/api/v2/local-apps/delete`.

---

## 10. Betrieb und Debugging auf der Box

```bash
ssh olares@<box>
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

# Warum installiert/startet es nicht? Die UI sagt nur downloadFailed/stopped.
kubectl logs -n os-framework app-service-0 --tail=2000 | grep -iE "<app>|error"
kubectl describe applicationmanager <app>-<nutzer>-<app>
kubectl get events -n <app>-<nutzer> --sort-by=.lastTimestamp
kubectl get ns <app>-<nutzer> --show-labels        # ns-owner da?
kubectl get networkpolicy -n <app>-<nutzer>        # app-np oder others-np?

# Init-Container und Sidecar
kubectl logs -n <ns> deploy/<app>-backend -c <init-name>
kubectl logs -n <ns> <pod> -c olares-envoy-sidecar  # zeigt die Köpfe

# Envoy-Konfiguration (distroless, kein exec)
kubectl port-forward -n <ns> <pod> 15000:15000 & curl localhost:15000/config_dump

# Welche Werte hat Helm wirklich gespeichert?
kubectl get secret -n <ns> sh.helm.release.v1.<app>.v<N> -o jsonpath='{.data.release}' \
  | base64 -d | base64 -d | gunzip

# Speicher eines Pods (keine Metrics API auf der Box)
kubectl exec -n <ns> <pod> -c <c> -- sh -c 'grep VmRSS /proc/1/status; cat /sys/fs/cgroup/memory.current'
```

- **Hochgeladene Charts** liegen unter
  `/olares/userdata/Cache/chartrepo/v2/<nutzer>/upload/<app>-<ver>/`.
- **Nach einem Box-Neustart** ist das Frontend vor dem Backend da; der
  Next-Proxy antwortet selbst mit 500, das Backend-Log bleibt leer. Clients
  sollten 5xx wiederholen.
- **Oberflächenfehler** (Browser-Ausnahmen) an einen Backend-Endpunkt melden
  und im Pod-Log lesen — sonst sieht sie niemand.
- **Kubelet-Probes fluten das Access-Log**: eine Probe alle 5 s × 2 Worker ließ
  nur ~16 Minuten Verlauf. Erfolgreiche `GET /health` aus dem Access-Log
  filtern (Fehler auf `/health` sichtbar lassen).
- **Health-Checks prüfen den wirksamen Zustand**, nicht Umgebungsvariablen oder
  Defaults.
- **Vor jedem Ausrollen**: DB-Abzug als Superuser (Kapitel 4.2), Render mit den
  echten Werten + `--dry-run=server` (Kapitel 6), `helm rollback <rev>`
  bereithalten, danach Images, Health und Init-Logs nachmessen.
- **Gerenderte Dateien mit Secrets** (`/tmp/r.yaml`) nach der Prüfung löschen.

---

## 11. GPU und Ressourcen

- **GPU-Time-Slicing reserviert Slots für andere Apps.** `nvidia-smi` zeigte
  22,5/24,5 GB belegt bei 324 MB realer Nutzung ⇒ `Unschedulable` bzw.
  „insufficient vram". Ausweg CPU-Inferenz (`int8`, ~5× langsamer bei Whisper).
- **`CUDA_DEVICE_MEMORY_LIMIT_0=<n>m` setzen**; HAMi ignoriert
  `nvidia.com/gpumem` (`cudaMalloc failed: out of memory` bei leerer GPU)
  [Marcs Umgebung].
- GPU-Bindung: `insufficient GPUBindings … bound=0` ⇒ deinstallieren +
  installieren; `compute resource is not enough` ⇒ GPU belegt,
  `olares-cli settings compute list`.
- Lange Modell-Starts: großzügige `startupProbe` (z. B. 720 × 30 s); Readiness
  mit `initialDelaySeconds` ≥ 60.
- **Pod-Limits ernst nehmen**: Ein Frontend mit 1 GiB lief bei einem
  600-MB-Upload auf 798 MB (Kapitel 12).

---

## 12. Web-Frontend (Next.js) hinter Olares

- **Browser ruft nur die eigene Origin**; Next.js reicht `/api/*` serverseitig
  ans Backend (kein CORS, kein zweiter Auth-Hop, keine API-Entrance).
- **`NEXT_PUBLIC_*` und `rewrites()`-Ziele werden beim Build eingebrannt.** Das
  Standalone-Image kennt zur Laufzeit keine neuen Rewrites. Im Dockerfile
  `NEXT_PUBLIC_API_URL` leer lassen, das Backend-Ziel auf den Cluster-Namen
  setzen (ohne diese Zeile lief ein Release gegen `localhost` ⇒ jede API-Anfrage
  500). Das Standalone-Image lauscht nur auf `$HOSTNAME` (`0.0.0.0` setzen).
- **Middleware und große Rümpfe**: Passt die Middleware auf einen Pfad, klont
  Next.js 15.5 den Request-Body und beendet **beide** Ströme bei
  `experimental.middlewareClientMaxBodySize` (Vorgabe 10 MB) — auch den, der
  danach weitergereicht wird. Beleg: `Request body exceeded 10MB`, Backend
  bekommt einen abgeschnittenen Multipart ⇒ 400/500. Die Weiterleitung aus
  `rewrites()` klont ebenfalls. Die Grenze auf 500 MB heben scheiterte
  trotzdem (Multipart-Rahmen hob die Datei darüber) und ließ 480 MB belegt.
  **Fix**: den Upload-Pfad aus dem Matcher nehmen und in einem eigenen
  Route Handler streamen; Kopfzeilen (Geheimnis, Identität) aus derselben
  Funktion wie die Middleware.
- **Ausgehend nicht mit `fetch` streamen.** Gemessen, 500 MB gegen eine schnelle
  Senke: Web-Strom ⇒ `fetch` (undici) +457–526 MB RSS;
  `Readable.fromWeb(request.body)` ⇒ `stream/promises.pipeline` ⇒ `http.request`
  +57–85 MB. Im Standalone-Server: zwei gleichzeitige 500-MB-Uploads +70 MB; auf
  der Box 600 MB ⇒ Spitze 110 MB statt 798 MB. `http.request` hat kein eigenes
  Zeitlimit — `setTimeout` für Ruhe setzen; einen dauerhaften `error`-Listener
  anhängen (ein zweiter Socket-Fehler ohne Listener beendet den Prozess).
  undici lehnt Anfragen mit `Expect`-Kopf ab — Hop-by-Hop-Köpfe (RFC 9110 §7.6.1)
  vor dem Weiterreichen entfernen.
- **Node beendet Anfragen nach `server.requestTimeout` = 300 s**, und Next setzt
  keinen eigenen Wert. Beleg: Upload mit 30 kB/s ⇒ HTTP 408 nach 329 s. Eine
  lange Aufnahme über ein schwaches Mobilnetz scheitert so bei jedem Versuch.
  **Fix**: beim Start per `node --require ./server-zeitlimit.cjs server.js`
  `http.createServer` umhüllen und `requestTimeout` setzen (z. B. 2 h); danach
  derselbe Upload ⇒ 201 nach 342 s. (Ungeklärt: ein Limit von 5 s griff durch
  Next bei einem 51-s-Upload nicht — nur den Fall > 300 s direkt messen.)
- **Kein `pages/`-Verzeichnis nebenbei anlegen**: es ändert App-weit die Typen
  (`useParams`, `usePathname` werden nullbar) und brach den Build in fremden
  Dateien.
- **Browser-Uploads auf Telefonen**: Aufnahmen stückweise in IndexedDB sichern,
  bis der Server bestätigt; mit einer Kennung pro Aufnahme senden, damit eine
  Wiederholung nichts doppelt anlegt; Wake Lock während Aufnahme **und**
  Senden (nach `visibilitychange` neu anfordern); Sendefortschritt nur per
  `XMLHttpRequest` (fetch meldet ihn nicht). `navigator.storage.persist()`
  fragt in Firefox nach.
- **React 19 / Chrome 152**: `useEffect(() => el.scrollIntoView())` ohne Block
  gibt ein Promise zurück, React ruft es als Aufräumfunktion ⇒ `TypeError: u is
  not a function`. Effekte immer mit Block.
- Hydrierungsfehler durch Zufall beim Rendern (Server ≠ Browser): erst nach dem
  Einhängen würfeln.

---

## 13. Backend und Worker

- **pydantic-settings und Chart-Umgebung**: ohne `env_prefix` wurde eine
  Variable still ignoriert (Modell fiel auf `tiny` zurück); Kubernetes injiziert
  `<SVC>_PORT=tcp://…` und kollidiert mit Feldern wie `port`. `env_prefix` bzw.
  `validation_alias` setzen und einen Test, der Chart- und Code-Namen verbindet.
- **Celery**: `-A app.worker:celery_app` mit vollem Pfad; Beat eingebettet
  (`--beat`, Zustand in `/app/cache`) nur bei genau einem Replikat; KVRocks
  trägt als Broker.
- **uvicorn**: erfolgreiche Probe-Zeilen per Logging-Filter auf
  `uvicorn.access` unterdrücken (uvicorn richtet Logging vor dem App-Import ein,
  auch pro Worker).
- **FastAPI/Starlette**: das ganze Multipart-Formular wird gelesen (in eine
  Temporärdatei gespoolt), **bevor** der Handler läuft — Größenlimits greifen
  erst danach; Dateien mit `run_in_threadpool` stückweise schreiben statt
  `await file.read()`; alles prüfen, **bevor** geschrieben wird. Eine im
  Endpunkt gesetzte ContextVar ist in der Middleware nicht sichtbar — `scope`
  benutzen.
- **Still geschluckte Fehler verbergen Plattformfehler.** Mindestens mit
  Metadaten loggen. Zwei Fehler können übereinanderliegen; den zweiten sieht
  man erst ohne den ersten (Beispiel: passlib 1.7.4 ist mit bcrypt ≥ 4.1
  unverträglich, auf der Box lief 5.0.0).
- **Ein API-Schlüssel gehört zu seiner Adresse.** Auf der Box trägt der Nutzer
  Endpunkte selbst ein (LiteLLM, SearXNG, Tavily, Speaches …) und wechselt
  sie. Steht der Schlüssel in einer Spalte, die den Dienst nicht kennt, geht
  nach einem Wechsel der alte Schlüssel an den neuen Dienst ⇒ 401, und die
  Maske zeigt weiter „hinterlegt". Regel (Beacon 0.9.7): zeigt die Adresse auf
  einen **anderen Rechnernamen** und kommt in derselben Anfrage kein neues
  Geheimnis mit, wird das alte verworfen. Rechnername vergleichen, nicht die
  ganze Adresse (ein Pfad-Tippfehler soll nichts wegwerfen).
- **Welcher Schlüssel liegt da?** Statt „hinterlegt" einen Fingerabdruck in
  der Form zeigen, die die Anbieter selbst verwenden (`tvly-d…EL01`: Anfang
  6, Ende 4; unter 16 Zeichen nur die Länge). Nur für API-Schlüssel, nie für
  Passwörter.
- **Fehlermeldungen externer Dienste mit Dienst und Adresse** ausgeben
  („Tavily unter https://api.tavily.com/search lehnt den Schlüssel ab (401)").
  Auf einer Box mit selbst eingetragenen Endpunkten ist „der Endpunkt hat
  abgelehnt" nicht diagnostizierbar; ohne lesbaren Schlüssel gar nicht erst
  anfragen — ein leerer Bearer sieht am anderen Ende aus wie ein falscher.
- Kleinere Fallen: ein leerer `Bearer `-Kopf lässt httpx abbrechen („Illegal
  header value"); Sprachmodelle verpacken JSON trotz `response_format` in
  Code-Fences; asyncpg liefert JSONB als String.

---

## 14. Lokale Entwicklung, die der Box ähnelt

- Postgres lokal mit einer **Nicht-Superuser-Rolle**, die auch die Migrationen
  ausführt (Eigentümerin wie auf der Box). Migrationen laufen lokal nicht
  automatisch.
- `DEV_USER` statt Envoy; auf der Box leer.
- Backend aus seinem Ordner starten, wenn `.env` relativ gelesen wird (sonst
  zeigt `APP_DATA_DIR=/app/data` auf einen schreibgeschützten Pfad).
- Ersatzdienste: `values-olares-stub.yaml` für `helm template`,
  `wrangler pages dev` für die Market Source, eine Attrappe für das Backend
  (zählt Bytes, protokolliert Felder, kann ablehnen oder langsam lesen) für
  Upload-Messungen, `node .next/standalone/server.js` statt `next dev` für
  Messungen wie im Pod.
- Olares-CLI: `npx @olares/cli@latest install`, npm-Präfix auf
  `~/.npm-global` statt `sudo`; `olares-cli profile login` (Browser, TOTP) macht
  der Mensch selbst.
- **Repos unter `~/Documents` (iCloud)** erzeugen „ 2"-Doppel
  (`routes.d 2.ts`), die `tsc` brechen; vor Commits nach `* 2.*` suchen.
- **Headless-Chrome-Tests**: verwaiste Browser auf dem Debug-Port verfälschen
  Ergebnisse; vorher `pkill -9 -f "user-data-dir=…"`.

---

## 15. Checklisten

### Vor einem Release

- [ ] `check-chart.sh` grün (Kapitel 2.4), `chart lint` auf das Paket
- [ ] Neue Werteschlüssel/Berechtigungen bedingt gelesen (`(.Values.x).y`,
      `hasKey`, `if (.Values.userspace).appCommon`)
- [ ] Migrationen idempotent; Seed besitzt, was er schreibt
- [ ] Keine veröffentlichte Migrationsdatei erweitert; Datenumzüge unter
      `force` mit `disable`/`enable`+`force` und als App-Rolle getestet
- [ ] Gegen eine **wirklich leere** Datenbank installiert (Erstinstallation)
- [ ] `upgradeDescription` beschreibt diese Version
- [ ] Image-Tags existieren und sind öffentlich

### Ausrollen auf eine Box

- [ ] DB-Abzug als Superuser (`citus-0`, `-U olares`)
- [ ] Render mit `helm get values` der Box + `kubectl apply --dry-run=server`
- [ ] `helm upgrade --reuse-values` mit geleerten Tag-Pins und
      `workloads.*.replicaCount`
- [ ] Pods: Images, Ready, Neustarts; Init-/Migrations-Logs; Health
- [ ] Die Funktion, die die Version rechtfertigt, **gegen die laufende App**
      messen, nicht gegen den Diff; gerenderte Dateien löschen

### In den Markt

- [ ] Nur nach `running` auf einer echten Box
- [ ] Version gehoben (Hash!), base64 frisch, `chartName` = Chart-Schlüssel
- [ ] Vier Endpunkte lokal bewiesen, Chart sha256-gleich
- [ ] Live-Katalog gegen Branch verglichen, alte PRs geschlossen
- [ ] Nach dem Deploy live nachgemessen (Version, Anzahl Apps, Chart-Bytes, Hash)
- [ ] Upgrade auf der Box gedrückt und Pods gemessen — erst dann „ausgerollt"

---

## 16. Offene Fragen und Widersprüche

1. Trägt ein Markt-Update eine **neu angeforderte Berechtigung** nachträglich
   in die Werte ein?
2. **Größen- und Zeitlimits der Eingangsschicht** (Envoy/`beclab/auth`) — alle
   Upload-Messungen liefen per Port-Forward daran vorbei. Belegt ist nur, dass
   mindestens 10 MB durchkamen.
3. **authLevel-Semantik**: Messung (`internal` ⇒ Login-302, `public` ⇒ 200 ohne
   Sitzung) gegen offizielle Tabelle und Playbook.
4. **Wer setzt `X-Bfl-User`?** Am Pod nicht verlässlich; ein Gateway vor dem
   Sidecar ist möglich.
5. **Upload-Pfad**: früh als Sackgasse beschrieben (kein CR, kein `ns-owner`),
   später funktionierend — die Ursache der frühen Fehlschläge ist nicht sauber
   getrennt.
6. `OLARES_USER_*` im Manifest-`valueFrom`: vom Textprüfer toleriert?
7. Postgres-Version (16 oder 17, Citus) je Olares-Release.
8. Warum `psql -f` gegen Citus hängt; warum `runAsInternal` den `check-auth`
   bricht; warum ein `requestTimeout` von 5 s durch Next nicht griff.
9. Lehnt Olares hohe `limitedCpu`-Werte auf kleineren Boxen ab?
10. Weg zu MinIO (`middleware.minio`, Zugangsdaten für `tapr-s3-svc`) —
    unerforscht.
11. Auf einer Box mit mehreren Konten wird Inhaber einer App-internen
    Organisation, wer die App zuerst öffnet — eine Reihenfolge-, keine
    Berechtigungsfrage.

---

**Quellen im Detail**: `docs/HANDOFF.md` (datierte Einträge, §6–§7g),
`docs/OLARES_DEEP_DIVE.md`, `docs/MARKET_SOURCE_PLAYBOOK.md`,
`docs/DEPLOYMENT.md`, `scripts/check-chart.sh`, `scripts/release.sh`,
`olares/templates/*`, `.claude/skills/olares-release/SKILL.md`,
Beacon `docs/BETRIEB.md`, die Olares-Erinnerungen dieses Projekts und die
offiziellen `olares-*`-Skills (`npx @olares/cli@latest install`).
