#!/usr/bin/env python3
"""Aus dem Manifest den Eintrag fuer die AImighty Market Source erzeugen.

Aufruf: markteintrag.py <app> <version> <dist-ordner>

Die Form ist gegen das LIVE-`functions/_apps.ts` des Markts gelesen, nicht
gegen die Doku -- an zwei Stellen weichen sie voneinander ab:

  - `title` und `description` sind `Record<string, string>` mit dem Schluessel
    `en`. Die API-Referenz in AGENTS.md nennt `en-US`; live steht ueberall
    `en`.
  - `permission` und `middleware` sind LEERE Listen, bei allen 21 Apps. Die
    echten Berechtigungen stehen im Chart-Manifest. `permission: appData` im
    _apps.ts-Eintrag zu wiederholen waere eine zweite Quelle fuer dieselbe
    Aussage.

Werte werden gelesen, nicht umgerechnet. Die goldene Regel des Markts lautet:
`_apps.ts`, Chart-Manifest und Root-Manifest tragen IDENTISCHE Werte. `2000m`
bleibt deshalb `2000m`, auch wenn die meisten Eintraege dort `2` schreiben --
beide Formen kommen live vor (`100m` und `500m` ebenfalls).

Erzeugt wird nichts, was nicht im Manifest steht. Einzige Ausnahme:
`requiredGpu: "0"` und `spec.type: "app"`, die jeder Eintrag dort traegt und
die im Manifest kein Gegenstueck haben.
"""

import hashlib
import pathlib
import sys

import yaml


def ts(wert):
    """Einen Python-Wert als TypeScript-Literal schreiben."""
    if isinstance(wert, bool):
        return "true" if wert else "false"
    if isinstance(wert, (int, float)):
        return str(wert)
    if isinstance(wert, list):
        return "[" + ", ".join(ts(x) for x in wert) + "]"
    if isinstance(wert, dict):
        return "{ " + ", ".join(f"{k}: {ts(v)}" for k, v in wert.items()) + " }"
    text = str(wert).replace("\\", "\\\\").replace('"', '\\"')
    if "\n" in text:
        raise ValueError(f"Zeilenumbruch in einem einzeiligen Feld: {wert!r}")
    return f'"{text}"'


def vorlage(wert):
    """Mehrzeiligen Text als TS-Template-Literal schreiben.

    Backtick und ${ muessen geschuetzt werden. Ohne das wird aus einer
    Beschreibung, die zufaellig ein ${...} enthaelt, ausfuehrbarer Code im
    Worker der Market Source.
    """
    text = str(wert).rstrip("\n")
    text = text.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    return "`" + text + "`"


def main() -> int:
    app, version, dist = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])

    m = yaml.safe_load(pathlib.Path("OlaresManifest.yaml").read_text())
    meta, spec = m["metadata"], m["spec"]

    if len(m["entrances"]) != 1:
        # Ein zweiter Entrance aendert die Adresse des ersten. Das Chart hat
        # genau einen; erzeugt der Generator hier mehr, stimmt etwas nicht.
        raise ValueError(f"erwartet war genau ein Entrance, gefunden: {len(m['entrances'])}")
    eingang = m["entrances"][0]

    if str(meta["version"]) != version:
        raise ValueError(
            f"Chart.yaml sagt {version}, das Manifest sagt {meta['version']}"
        )

    env_zeilen = []
    for e in m.get("envs") or []:
        felder = {"envName": e["envName"], "required": e.get("required", False)}
        if "default" in e:
            felder["default"] = e["default"]
        for k in ("type", "editable", "applyOnChange", "description"):
            if k in e:
                felder[k] = e[k]
        env_zeilen.append("        " + ts(felder) + ",")

    eingang_ts = ts(
        {
            "name": eingang["name"],
            "title": {"en": eingang["title"]},
            "port": eingang["port"],
            "host": eingang["host"],
            "authLevel": eingang["authLevel"],
            "openMethod": eingang["openMethod"],
        }
    )

    envs_block = ""
    if env_zeilen:
        envs_block = "\n      envs: [\n" + "\n".join(env_zeilen) + "\n      ],"

    block = f"""  {{
    metadata: {{
      name: {ts(meta["name"])},
      version: {ts(str(meta["version"]))},
      icon: {ts(meta["icon"])},
      title: {ts({"en": meta["title"]})},
      description: {ts({"en": meta["description"]})},
      fullDescription:
        {vorlage(spec["fullDescription"])},
      upgradeDescription:
        {vorlage(spec["upgradeDescription"])},
      categories: {ts(meta["categories"])},
      developer: {ts(spec["developer"])},
      website: {ts(spec["website"])},
      sourceCode: {ts(spec["sourceCode"])},
      supportArch: {ts(spec["supportArch"])},
      requiredCpu: {ts(spec["requiredCpu"])},
      requiredMemory: {ts(spec["requiredMemory"])},
      requiredDisk: {ts(spec["requiredDisk"])},
      requiredGpu: "0",
      limitedCpu: {ts(spec["limitedCpu"])},
      limitedMemory: {ts(spec["limitedMemory"])},
      apiTimeout: {ts(m["options"]["apiTimeout"])},
    }},
    spec: {{
      type: "app",
      entrance: [
        {eingang_ts},
      ],
      permission: [],
      middleware: [],
      options: {{ resources: {{ cpu: {ts(spec["requiredCpu"])}, memory: {ts(spec["requiredMemory"])}, disk: {ts(spec["requiredDisk"])} }} }},{envs_block}
    }},
  }},
"""

    (dist / "_apps-eintrag.ts").write_text(block)

    schluessel = f"{app}-{version}.tgz"
    b64 = (dist / f"{schluessel}.base64").read_text().strip()
    (dist / "_lib-eintrag.txt").write_text(f'  "{schluessel}": "{b64}",\n')

    print("  + _apps-eintrag.ts   Block fuer functions/_apps.ts")
    print("  + _lib-eintrag.txt   Zeile fuer das CHARTS-Dict in functions/_lib.ts")
    print(f"  + Tabellenschluessel  \"{schluessel}\"")
    print(f"  + appID = md5(name)[:8]  {hashlib.md5(app.encode()).hexdigest()[:8]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
