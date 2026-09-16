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

**Das Modell ist kein fest verdrahteter Name.** Es kommt je Einstellung aus
dem Job, sonst aus `MOVE_FAL_MODEL` bzw. `MOVE_FAL_BILD_MODEL`, sonst aus den
Vorgaben unten. Die Antwort wird tolerant nach einer URL durchsucht; findet
sich keine, scheitert der Job MIT der Antwort im Text -- statt zu raten.

Was hier belegt ist und was nicht
---------------------------------
`fal.ai`, `docs.fal.ai` und der Mintlify-Spiegel sind vom Egress-Proxy
gesperrt (`curl: (56) CONNECT tunnel failed, response 403`, WebFetch
`EGRESS_BLOCKED`). Der Suchindex und `pypi.org` kommen dagegen durch, und
damit ist ein Teil der frueheren Annahmen jetzt gemessen:

    GEMESSEN am Quelltext von fal-client 1.0.1 -- derselben Version, die
    worker/requirements.txt festnagelt, nicht einer aehnlichen:
      * subscribe(application, arguments, *, ..., start_timeout=None,
        client_timeout=None). `arguments` POSITIONAL_OR_KEYWORD,
        `client_timeout` KEYWORD_ONLY.
      * Der Schluessel wird LAZY geholt: `SyncClient._auth` ist eine
        cached_property. `import fal_client` ohne FAL_KEY laeuft durch --
        nachgemessen, und wichtig, weil FAL_KEY im Manifest optional ist.
      * Neben FAL_KEY akzeptiert der Client FAL_KEY_ID + FAL_KEY_SECRET.

    GEMESSEN an den Modellseiten (ueber die Suche, nicht ueber den Abruf):
      * image_url und seed sind echte Argumentnamen.
      * fal-ai/ltx-video existiert und ist TEXT-zu-Video.
      * duration taucht bei einem Modell als Zeichenkette "8" auf.

    NICHT gemessen: das vollstaendige Schema irgendeines Video-Modells, und
    ob ein Bild-zu-Video-Modell das Referenzbild als ersten Bildinhalt nimmt
    oder nur als Stilvorgabe. Dafuer braeuchte es einen echten Aufruf.

Der Schluessel
--------------
`FAL_KEY` aus der Umgebung, so wie fal-client es selbst liest. Er steht an
keiner Stelle im Repo und wird nirgends geloggt: `_ohne_geheimnis` raeumt ihn
aus jeder Meldung, bevor sie in die Job-Zeile wandert.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

LOG = logging.getLogger(__name__)

# Das Textmodell. GEMESSEN existent: fal.ai/models/fal-ai/ltx-video ist eine
# echte Endpunkt-ID -- und sie ist TEXT-zu-Video. Das ist der Punkt, an dem
# die Wahl des Modells nicht kosmetisch ist: ein Text-zu-Video-Endpunkt hat
# keinen Parameter fuer ein Referenzbild. Wer eine Figur mit Bild an dieses
# Modell schickt, bekommt entweder einen Fehler oder -- schlimmer -- ein
# Video, in dem das Bild einfach keine Rolle gespielt hat. Bezahlt, und die
# Figur sieht in jeder Einstellung anders aus.
DEFAULT_MODEL = "fal-ai/ltx-video"

# Deshalb ein zweites Modell fuer den Bildpfad, und die Wahl faellt danach,
# OB ein Referenzbild vorliegt (siehe modell_fuer). GEMESSEN: der Endpunkt
# fal-ai/ltx-2/image-to-video verlangt genau zwei Argumente, image_url und
# prompt -- die beiden, die move ohnehin schickt.
DEFAULT_BILD_MODEL = "fal-ai/ltx-2/image-to-video"

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


# Unter welchem Argumentnamen ein Modell sein Referenzbild erwartet.
#
# BELEGT an zwei unabhaengigen Modellschemata: fal-ai/ltx-2/image-to-video
# nennt image_url als eines seiner zwei Pflichtargumente, und das Beispiel zu
# bytedance/seedance-2.0/image-to-video schickt image_url ebenfalls. Der
# Ueberschreiber bleibt trotzdem, denn belegt ist der Name fuer diese Modelle,
# nicht fuer jedes.
BILD_ARGUMENT = "image_url"

# Dasselbe fuer den Seed. BELEGT am Beispiel zu fal-ai/flux/dev, das
# seed: 6252023 neben dem Prompt schickt.
SEED_ARGUMENT = "seed"

# Und fuer die Laenge. Hier ist die Lage anders als bei den beiden oben: der
# NAME ist belegt (duration), der TYP ist es nicht einheitlich. Das Beispiel
# zu seedance schickt "8" als ZEICHENKETTE, andere Modelle nehmen eine Zahl,
# und manche nur einen festen Satz von Werten. Deshalb drei Schalter statt
# einer Annahme -- alle drei ohne neues Image umstellbar:
#
#   MOVE_FAL_DAUER_ARGUMENT   anderer Name
#   MOVE_FAL_DAUER_TEXT=1     als Zeichenkette senden, wie bei seedance
#   MOVE_FAL_DAUER_AUS=1      gar nicht senden, fuer Modelle mit fester Laenge
#
# Der Wert selbst geht IMMER als ganze Sekunde, aufgerundet: der Assembler
# lehnt einen Clip ab, der kuerzer ist als seine Einstellung (check_clips),
# und dann waere der Aufruf bezahlt und der Job trotzdem gescheitert.
DAUER_ARGUMENT = "duration"


def bild_argument() -> str:
    return os.environ.get("MOVE_FAL_BILD_ARGUMENT", "").strip() or BILD_ARGUMENT


def seed_argument() -> str:
    return os.environ.get("MOVE_FAL_SEED_ARGUMENT", "").strip() or SEED_ARGUMENT


def dauer_argument() -> str:
    return os.environ.get("MOVE_FAL_DAUER_ARGUMENT", "").strip() or DAUER_ARGUMENT


def dauer_als_text() -> bool:
    return os.environ.get("MOVE_FAL_DAUER_TEXT", "").strip() == "1"


def dauer_senden() -> bool:
    return os.environ.get("MOVE_FAL_DAUER_AUS", "").strip() != "1"


@dataclass(frozen=True)
class Anfrage:
    """Was fuer eine Einstellung erzeugt werden soll.

    KONSISTENZ UEBER MEHRERE EINSTELLUNGEN entsteht hier aus drei Dingen, die
    zusammen wirken. Einzeln taugt keines davon:

        referenz_bild  der starke Hebel. Dasselbe Bild in jeder Einstellung,
                       ueber ein Bild-zu-Video-Modell. Ohne das bleibt es beim
                       Zufall, den ein Textmodell je Aufruf neu wuerfelt.
        prompt         traegt die Beschreibung der Figur schon mit; der
                       Aufrufer setzt sie davor.
        seed           dieselbe Zahl je Figur. Hilft bei manchen Modellen,
                       schadet bei keinem.

    LoRA-Training bleibt draussen -- CLAUDE.md schliesst es aus, und es
    braeuchte GPU-Zeit und Trainingsdaten.
    """

    prompt: str
    sekunden: float
    breite: int
    height: int
    model: str = DEFAULT_MODEL
    # Bild im Dateisystem. Wird vor dem Aufruf zu fal hochgeladen, weil die
    # Modelle eine URL erwarten und keine Datei.
    referenz_bild: Path | None = None
    seed: int | None = None
    # Modellspezifisches, das durchgereicht wird, ohne dass dieses Modul es
    # kennen muss.
    extra: tuple[tuple[str, Any], ...] = ()

    def ganze_sekunden(self) -> int:
        """Laenge als ganze Sekunde, AUFGERUNDET.

        Warum nicht `round`: `round(1.996, 2)` ergibt 2.0, und 2.0 s sind
        4 ms kuerzer als die Einstellung, die der Clip fuellen soll. Der
        Assembler laesst das nicht durch (check_clips vergleicht gegen
        `plan.source_ms`), also waere der Aufruf bezahlt und der Job
        gescheitert. Aufrunden kostet nichts: laengeres Material schneidet
        der Assembler ohnehin auf die Einstellung.
        """
        return max(1, math.ceil(self.sekunden - 1e-9))

    def argumente(self, bild_url: str = "") -> dict[str, Any]:
        args: dict[str, Any] = {"prompt": self.prompt}
        if dauer_senden():
            dauer = self.ganze_sekunden()
            args[dauer_argument()] = str(dauer) if dauer_als_text() else dauer
        if bild_url:
            args[bild_argument()] = bild_url
        if self.seed is not None:
            args[seed_argument()] = int(self.seed)
        # extra zuletzt: wer ein Argument bewusst je Job setzt, soll die
        # Vorgaben hier ueberschreiben koennen und nicht umgekehrt.
        args.update(dict(self.extra))
        return args

    def schluessel(self) -> str:
        """Stabiler Hash. Gleiche Anfrage, gleiche Datei, keine neue Rechnung.

        Referenzbild und Seed gehen MIT ein, und zwar das Bild ueber seinen
        Inhalt, nicht ueber seinen Pfad. Sonst liefe zwei Figuren mit
        demselben Prompt derselbe Zwischenspeicher-Eintrag zu -- und eine
        Figur bekaeme das Gesicht der anderen, ohne dass etwas scheitert.
        """
        roh = json.dumps(
            {
                "model": self.model,
                "prompt": self.prompt,
                "sekunden": round(self.sekunden, 3),
                "breite": self.breite,
                "hoehe": self.height,
                "referenz": self._bild_hash(),
                "seed": self.seed,
                "extra": sorted(self.extra),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:16]

    def _bild_hash(self) -> str:
        if self.referenz_bild is None:
            return ""
        pfad = Path(self.referenz_bild)
        if not pfad.is_file():
            raise FalFehler(f"Das Referenzbild fehlt: {pfad}")
        h = hashlib.sha256()
        with pfad.open("rb") as datei:
            for stueck in iter(lambda: datei.read(BLOCK), b""):
                h.update(stueck)
        return h.hexdigest()[:16]


def fal_key() -> str:
    """Prueft frueh, dass ueberhaupt ein Schluessel da ist.

    Der Client holt seine Anmeldedaten lazy (cached_property `_auth`) und
    wuerde erst beim ersten Aufruf mit MissingCredentialsError scheitern --
    also mitten im Job statt davor.

    Das Paar FAL_KEY_ID + FAL_KEY_SECRET zaehlt mit, weil fal-client es in
    `_resolve_env_or_colab_auth` genauso akzeptiert. Wer es gesetzt hat, soll
    hier nicht faelschlich "kein Schluessel" lesen.
    """
    schluessel = os.environ.get("FAL_KEY", "").strip()
    if schluessel:
        return schluessel

    kennung = os.environ.get("FAL_KEY_ID", "").strip()
    geheim = os.environ.get("FAL_KEY_SECRET", "").strip()
    if kennung and geheim:
        return f"{kennung}:{geheim}"

    raise FalFehler(
        "FAL_KEY ist nicht gesetzt. Der Worker liest ihn aus der Umgebung; "
        "im Chart kommt er ueber .Values.olaresEnv.FAL_KEY dorthin und "
        "steht an keiner Stelle im Repo. Alternativ akzeptiert fal-client "
        "FAL_KEY_ID zusammen mit FAL_KEY_SECRET."
    )


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

    FAL_KEY_SECRET geht mit, seit `fal_key` das Paar akzeptiert -- sonst
    haette diese Funktion eine Luecke genau in dem Pfad, den sie absichern
    soll. Laengster Wert zuerst, damit das Ersetzen des kuerzeren nicht den
    laengeren zerschneidet und einen Rest stehen laesst.
    """
    geheimnisse = {
        name: os.environ.get(name, "").strip()
        for name in ("FAL_KEY", "FAL_KEY_SECRET")
    }
    for name, wert in sorted(geheimnisse.items(), key=lambda p: -len(p[1])):
        if wert and wert in text:
            text = text.replace(wert, f"<{name}>")
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


class FalClient(Protocol):  # noqa: F811 - erweitert die Zusage oben
    """Nur das, was hier gebraucht wird -- damit ein Doppel einspringen kann.

    `upload_file` kommt hinzu, weil ein Referenzbild als URL an das Modell
    geht und nicht als Datei.
    """

    def subscribe(self, application: str, arguments: dict[str, Any], **kwargs: Any) -> Any: ...

    def upload_file(self, path: str) -> str: ...


@dataclass
class Generator:
    """Erzeugt Clips und legt sie im Zwischenspeicher ab."""

    cache_dir: Path
    timeout_s: int = DEFAULT_TIMEOUT_S
    client: FalClient | None = field(default=None, repr=False)
    # Bild-Pfad -> URL bei fal. Ein Referenzbild wird je Job EINMAL
    # hochgeladen, nicht je Einstellung: bei acht Einstellungen mit derselben
    # Figur waeren das acht identische Uploads.
    _bild_urls: dict[str, str] = field(default_factory=dict, repr=False)

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

    def bild_url(self, pfad: Path) -> str:
        """Laedt ein Referenzbild zu fal und gibt die URL zurueck.

        Gemerkt je Pfad, damit dieselbe Figur in acht Einstellungen einen
        Upload kostet und nicht acht.
        """
        schluessel = str(pfad)
        gemerkt = self._bild_urls.get(schluessel)
        if gemerkt:
            return gemerkt

        if not pfad.is_file():
            raise FalFehler(f"Das Referenzbild fehlt: {pfad}")

        client = self._client()
        hochladen = getattr(client, "upload_file", None)
        if hochladen is None:
            raise FalFehler(
                "Der fal-Client kennt kein upload_file. Ohne das kann ein "
                "Referenzbild nicht uebergeben werden -- eine Figur mit Bild "
                "ist damit nicht erzeugbar. fal-client aktualisieren."
            )
        try:
            url = hochladen(str(pfad))
        except Exception as exc:
            raise FalFehler(
                f"Referenzbild {pfad.name} konnte nicht zu fal geladen werden: "
                f"{type(exc).__name__}: {_ohne_geheimnis(str(exc))}"
            ) from exc

        if not ist_web_url(url):
            raise FalFehler(
                f"fal hat fuer {pfad.name} keine http-URL geliefert, sondern "
                f"{_ohne_geheimnis(str(url))[:200]!r}"
            )
        self._bild_urls[schluessel] = url
        LOG.info("Referenzbild hochgeladen", extra={"datei": pfad.name})
        return url

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

        # Erst das Bild, dann der bezahlte Aufruf. Scheitert der Upload,
        # soll er scheitern, BEVOR etwas abgerechnet wird.
        url = self.bild_url(Path(anfrage.referenz_bild)) if anfrage.referenz_bild else ""

        LOG.info(
            "erzeuge Clip bei fal",
            extra={
                "model": anfrage.model,
                "sekunden": anfrage.sekunden,
                "schluessel": anfrage.schluessel(),
                # Der Prompt gehoert ins Log, der Schluessel nicht.
                "prompt": anfrage.prompt[:120],
                "mit_referenzbild": bool(url),
                "seed": anfrage.seed,
            },
        )

        try:
            # `arguments` als Schluesselwort und `client_timeout` als
            # Schluesselwort -- beides gegen die echte Signatur von
            # fal-client 1.0.1 geprueft, nicht gegen die Doku:
            #   subscribe(application, arguments, *, ..., client_timeout=None)
            # `arguments` ist POSITIONAL_OR_KEYWORD, `client_timeout` ist
            # KEYWORD_ONLY. Der Name steht damit fest und ein Dreher in der
            # Reihenfolge kann nicht mehr durchrutschen.
            antwort = client.subscribe(
                anfrage.model,
                arguments=anfrage.argumente(url),
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


def default_bild_model() -> str:
    return (
        os.environ.get("MOVE_FAL_BILD_MODEL", DEFAULT_BILD_MODEL).strip()
        or DEFAULT_BILD_MODEL
    )


def modell_fuer(mit_bild: bool) -> str:
    """Welches Modell eine Einstellung bekommt.

    MOVE_FAL_MODEL gilt bewusst NICHT fuer den Bildpfad. Das sieht zunaechst
    inkonsequent aus, ist aber der Punkt: wer MOVE_FAL_MODEL auf ein
    Text-zu-Video-Modell setzt, wuerde damit sonst jede Figur mit Referenzbild
    still entwerten -- der Endpunkt kennt image_url nicht. Der Bildpfad hat
    deshalb seinen eigenen Schalter, MOVE_FAL_BILD_MODEL.

    Je Job schlaegt das Modellfeld der Einstellung ohnehin beides.
    """
    return default_bild_model() if mit_bild else default_model()
