"""Clips bei fal.ai erzeugen lassen.

ACHTUNG ZUM SCOPE: CLAUDE.md schliesst Video-Generierung aus v0 aus. Das hier
ist eine bewusste Erweiterung darueber hinaus, auf ausdrueckliche Ansage.
Der Assembler bleibt davon unberuehrt -- der Schnitt ist weiterhin Arithmetik,
generiert werden nur die einzelnen Einstellungen.

Drei Entscheidungen, die den Unterschied machen
-----------------------------------------------
**Gepollt, nicht zurueckgerufen.** fal kann das Ergebnis per Webhook
schicken. Fuer move ist das nicht nutzbar: der Envoy-Sidecar faengt jeden
eingehenden TCP-Verkehr ab, ein Rueckruf von aussen endet in
401 ext_authz_denied. `subscribe` pollt selbst und blockiert, bis das
Ergebnis da ist -- dieselbe Einschraenkung, die schon das Polling auf der
Job-Tabelle erzwingt.

**Zwischengespeichert, sonst kostet ein Pod-Neustart doppelt.** hostPath
erzwingt `strategy: Recreate`; bei einem Update wird der Pod beendet, und
`reset_stale_running` legt den laufenden Job zurueck in die Warteschlange.
Ohne Zwischenspeicher wuerde derselbe Job danach alle Einstellungen neu
generieren -- und neu abgerechnet. Der Schluessel ist ein Hash ueber Modell,
Prompt, Laenge und Format; liegt die Datei schon da, wird sie genommen.

**Das Modell ist kein fest verdrahteter Name.** Welche Video-Modelle es bei
fal gibt und wie ihre Ausgabe aussieht, konnte ich nicht nachsehen -- fal.ai
und docs.fal.ai sind vom Egress-Proxy gesperrt. Das Modell kommt deshalb aus
`MOVE_FAL_MODEL` bzw. je Job, und die Antwort wird tolerant nach einer URL
durchsucht. Findet sich keine, scheitert der Job MIT der Antwort im Text --
statt zu raten.

Der Schluessel
--------------
`FAL_KEY` aus der Umgebung, so wie fal-client es selbst erwartet. Er steht an
keiner Stelle im Repo und wird nirgends geloggt: `_ohne_geheimnis` raeumt ihn
aus jeder Meldung, bevor sie in die Job-Zeile wandert.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

LOG = logging.getLogger(__name__)

DEFAULT_MODEL = "fal-ai/ltx-video"
DEFAULT_TIMEOUT_S = 900
DOWNLOAD_TIMEOUT_S = 300
BLOCK = 1024 * 1024

# Schluessel, unter denen fal-Modelle ihr Ergebnis ablegen. Tolerant, weil
# die Modelle sich unterscheiden und die Doku hier nicht erreichbar war.
URL_KANDIDATEN = ("video", "output", "file", "result")


class FalFehler(RuntimeError):
    """Eine Generierung ist gescheitert."""


class FalClient(Protocol):
    """Nur das, was hier gebraucht wird -- damit ein Doppel einspringen kann."""

    def subscribe(self, application: str, arguments: dict[str, Any], **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class Anfrage:
    """Was fuer eine Einstellung erzeugt werden soll."""

    prompt: str
    sekunden: float
    breite: int
    height: int
    model: str = DEFAULT_MODEL
    # Modellspezifisches, das durchgereicht wird, ohne dass dieses Modul es
    # kennen muss.
    extra: tuple[tuple[str, Any], ...] = ()

    def argumente(self) -> dict[str, Any]:
        args: dict[str, Any] = {
            "prompt": self.prompt,
            "duration": round(self.sekunden, 2),
        }
        args.update(dict(self.extra))
        return args

    def schluessel(self) -> str:
        """Stabiler Hash. Gleiche Anfrage, gleiche Datei, keine neue Rechnung."""
        roh = json.dumps(
            {
                "model": self.model,
                "prompt": self.prompt,
                "sekunden": round(self.sekunden, 3),
                "breite": self.breite,
                "hoehe": self.height,
                "extra": sorted(self.extra),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:16]


def fal_key() -> str:
    schluessel = os.environ.get("FAL_KEY", "").strip()
    if not schluessel:
        raise FalFehler(
            "FAL_KEY ist nicht gesetzt. Der Worker liest ihn aus der Umgebung; "
            "im Chart kommt er ueber .Values.olaresEnv.FAL_KEY dorthin und "
            "steht an keiner Stelle im Repo."
        )
    return schluessel


def fingerabdruck(schluessel: str) -> str:
    """Erkennbar machen, WELCHER Schluessel liegt -- ohne ihn zu verraten.

    Nach docs/olares-learnings.md 13: "Statt 'hinterlegt' einen
    Fingerabdruck in der Form zeigen, die die Anbieter selbst verwenden
    (`tvly-d...EL01`: Anfang 6, Ende 4; unter 16 Zeichen nur die Laenge).
    Nur fuer API-Schluessel, nie fuer Passwoerter."

    Der Grund ist diagnostisch: "kein Schluessel" und "falscher Schluessel"
    sehen am anderen Ende gleich aus. Wer einen Schluessel rotiert und
    danach 401 bekommt, will wissen, ob der neue ueberhaupt angekommen ist.
    """
    schluessel = schluessel.strip()
    if not schluessel:
        return "(leer)"
    if len(schluessel) < 16:
        # Zu kurz, um etwas zu zeigen, ohne zu viel zu zeigen.
        return f"({len(schluessel)} Zeichen)"
    return f"{schluessel[:6]}...{schluessel[-4:]}"


def _ohne_geheimnis(text: str) -> str:
    """Raeumt den Schluessel aus einer Meldung.

    Fehlermeldungen wandern in `render_job.error` und damit in die
    Oberflaeche. Ein Schluessel, der einmal dort steht, steht dort dauerhaft.
    """
    schluessel = os.environ.get("FAL_KEY", "").strip()
    if schluessel and schluessel in text:
        text = text.replace(schluessel, "<FAL_KEY>")
    return text


def ist_web_url(wert: Any) -> bool:
    """Nur http und https.

    Die Antwort kommt von aussen. Ohne diese Pruefung wuerde eine URL wie
    `file:///etc/passwd` in der Antwort dazu fuehren, dass der Worker eine
    lokale Datei liest und als erzeugten Clip ablegt. Jeder Kandidat laeuft
    deshalb hier durch, auch die verschachtelten.
    """
    if not isinstance(wert, str):
        return False
    return wert.startswith("http://") or wert.startswith("https://")


def video_url_aus(antwort: Any) -> str:
    """Sucht die Ergebnis-URL in der Antwort.

    Bewusst tolerant, was den Schluessel angeht: verschiedene fal-Modelle
    legen das Ergebnis unter verschiedenen Namen ab, und die Doku war nicht
    erreichbar. Beim SCHEMA wird nicht toleriert -- siehe `ist_web_url`.

    Findet sich nichts, scheitert es MIT der Antwort im Text; ein geratener
    Schluessel waere schlimmer.
    """
    if ist_web_url(antwort):
        return antwort

    if isinstance(antwort, dict):
        kandidaten: list[Any] = []
        for name in URL_KANDIDATEN:
            wert = antwort.get(name)
            if isinstance(wert, dict):
                kandidaten.append(wert.get("url"))
            elif isinstance(wert, list) and wert:
                erstes = wert[0]
                kandidaten.append(erstes.get("url") if isinstance(erstes, dict) else erstes)
            else:
                kandidaten.append(wert)
        kandidaten.append(antwort.get("url"))

        for kandidat in kandidaten:
            if ist_web_url(kandidat):
                return kandidat

    raise FalFehler(
        "In der Antwort von fal steckt keine Video-URL. Gesucht wurde unter "
        f"{list(URL_KANDIDATEN)} und 'url'. Antwort: "
        f"{_ohne_geheimnis(json.dumps(antwort, ensure_ascii=False)[:800])}"
    )


def herunterladen(url: str, ziel: Path, timeout_s: int = DOWNLOAD_TIMEOUT_S) -> int:
    """Laedt das Ergebnis nach `ziel`.

    Erst nach `.teil`, dann umbenennen -- ein abgebrochener Download darf
    keine halbe Datei hinterlassen, die aussieht wie eine ganze. Dieselbe
    Regel wie im Upload-Pfad des Web.
    """
    ziel.parent.mkdir(parents=True, exist_ok=True)
    teil = ziel.with_suffix(ziel.suffix + ".teil")
    bytes_gesamt = 0
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as antwort:  # noqa: S310
            with teil.open("wb") as datei:
                while True:
                    stueck = antwort.read(BLOCK)
                    if not stueck:
                        break
                    datei.write(stueck)
                    bytes_gesamt += len(stueck)
        if bytes_gesamt == 0:
            raise FalFehler(f"Der Download von {url} war leer")
        teil.rename(ziel)
    except Exception as exc:
        teil.unlink(missing_ok=True)
        if isinstance(exc, FalFehler):
            raise
        raise FalFehler(f"Download von {url} gescheitert: {_ohne_geheimnis(str(exc))}") from exc
    return bytes_gesamt


@dataclass
class Generator:
    """Erzeugt Clips und legt sie im Zwischenspeicher ab."""

    cache_dir: Path
    timeout_s: int = DEFAULT_TIMEOUT_S
    client: FalClient | None = field(default=None, repr=False)

    def _client(self) -> FalClient:
        if self.client is not None:
            return self.client
        # Erst hier importieren: der Kern des Workers laeuft ohne fal-client,
        # genau wie ohne librosa.
        try:
            import fal_client  # noqa: PLC0415
        except ImportError as exc:
            raise FalFehler(
                "fal-client fehlt. Installieren mit: "
                "pip install -r worker/requirements.txt"
            ) from exc
        fal_key()  # frueh scheitern, wenn kein Schluessel da ist
        return fal_client  # type: ignore[return-value]

    def pfad_fuer(self, anfrage: Anfrage) -> Path:
        return Path(self.cache_dir) / f"{anfrage.schluessel()}.mp4"

    def erzeuge(self, anfrage: Anfrage) -> Path:
        """Liefert den Pfad zum Clip. Erzeugt ihn nur, wenn er fehlt."""
        ziel = self.pfad_fuer(anfrage)
        if ziel.is_file() and ziel.stat().st_size > 0:
            LOG.info(
                "Clip aus dem Zwischenspeicher",
                extra={"schluessel": anfrage.schluessel(), "datei": str(ziel)},
            )
            return ziel

        client = self._client()
        LOG.info(
            "erzeuge Clip bei fal",
            extra={
                "model": anfrage.model,
                "sekunden": anfrage.sekunden,
                "schluessel": anfrage.schluessel(),
                # Der Prompt gehoert ins Log, der Schluessel nicht.
                "prompt": anfrage.prompt[:120],
            },
        )

        try:
            antwort = client.subscribe(
                anfrage.model,
                anfrage.argumente(),
                client_timeout=self.timeout_s,
            )
        except FalFehler:
            raise
        except Exception as exc:
            raise FalFehler(
                f"fal-Aufruf fuer {anfrage.model} gescheitert: "
                f"{type(exc).__name__}: {_ohne_geheimnis(str(exc))}"
            ) from exc

        url = video_url_aus(antwort)
        bytes_gesamt = herunterladen(url, ziel)
        LOG.info(
            "Clip erzeugt",
            extra={"datei": str(ziel), "bytes": bytes_gesamt, "model": anfrage.model},
        )
        return ziel


def default_model() -> str:
    return os.environ.get("MOVE_FAL_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
