# Design-Guide — AImighty-Standard, verbindlich für move

> **Herkunft.** Die Werte stammen aus dem gelieferten
> `InSilo_Design-Paket` (`tokens/globals.css`), abgeleitet aus den
> AImighty-Markenregeln. Übernommen aus
> `ska1walker/insilo:frontend/app/globals.css` — der Stelle, an der das
> Paket in Betrieb ist. **Wer einen Wert ändert, ändert ihn dort, nicht
> hier und nicht am Bauteil.**
>
> Diese Datei ersetzt den Platzhalter, dem moves Oberfläche bis jetzt
> gefolgt ist. Der Abgleich hat vier falsche Farbwerte zutage gefördert,
> siehe Abschnitt „Was korrigiert wurde".

## Die zwei Farbfamilien

Hanseatenblau trägt die Fläche, Gold zeichnet aus. **Keine dritte
Familie.**

### Hanseatenblau, Farbton 249,9

| Token | Wert | |
|---|---|---|
| `--am-blau-25` | `#f5f9fc` | |
| `--am-blau-50` | `#eff4f9` | |
| `--am-blau-100` | `#e3eaf3` | |
| `--am-blau-200` | `#cfdbe7` | Text sekundär im Dunkelmodus |
| `--am-blau-300` | `#afc0d2` | Text gedämpft im Dunkelmodus |
| `--am-blau-400` | `#819bb7` | Text deaktiviert |
| `--am-blau-500` | `#587898` | Wendepunkt — in **beiden** Modi lesbar |
| `--am-blau-600` | `#335578` | |
| `--am-blau-700` | `#142e47` | Rand, Trennlinie im Dunkelmodus |
| `--am-blau-800` | `#0a2238` | Fläche 1 (Karte, Tafel) im Dunkelmodus |
| `--am-blau-900` | `#051729` | Grundfläche |
| `--am-blau-950` | `#010c1a` | |

### Gold, Farbton 85,7

| Token | Wert | |
|---|---|---|
| `--am-gold-200` | `#ebddbd` | Auswahlfläche im Hellmodus |
| `--am-gold-300` | `#dfc896` | |
| `--am-gold-400` | `#d7ba7b` | Handlung hover im Dunkelmodus |
| `--am-gold-500` | `#caa960` | **das eine Markengold** |
| `--am-gold-600` | `#b7974e` | Handlung aktiv im Dunkelmodus |
| `--am-gold-700` | `#a4843a` | |
| `--am-gold-800` | `#8b6c1f` | Gold als Text **auf Weiß**, 4,93:1 |
| `--am-gold-900` | `#71560f` | Fokusring im Dunkelmodus |

`--am-gold-800` ist für Text auf Weiß gerechnet. Auf dunkler Fläche kommt
es nur auf 3,3:1 und fällt unter die Lesbarkeitsschwelle — im Dunkelmodus
gehört dort ein hellerer Ton hin.

## Die Rollenumkehr

**Im Hellmodus handelt Blau, Gold zeichnet aus. Im Dunkelmodus handelt
Gold** — Blau auf Blau trägt nicht. Das ist die dokumentierte Ausnahme,
und sie steckt vollständig in den Token; Bauteile merken davon nichts.

| Halbsemantisch | Hell | Dunkel |
|---|---|---|
| `--am-seite` | `#ffffff` | `--am-blau-900` |
| `--am-flaeche-1` | `--am-blau-25` | `--am-blau-800` |
| `--am-flaeche-2` | `--am-blau-50` | `--am-blau-700` |
| `--am-rand` | `--am-blau-200` | `--am-blau-700` |
| `--am-text-primaer` | `--am-blau-900` | `#ffffff` |
| `--am-text-sekundaer` | `--am-blau-600` | `--am-blau-200` |
| `--am-text-gedaempft` | `--am-blau-500` | `--am-blau-300` |
| `--am-handlung-ruhend` | `#002f56` | `--am-gold-500` |
| `--am-handlung-hover` | `#104472` | `--am-gold-400` |
| `--am-handlung-aktiv` | `#002140` | `--am-gold-600` |
| `--am-handlung-text` | `#ffffff` | `--am-blau-900` |
| `--am-fokus-ring` | `#002f56` | `--am-gold-900` |

## Raum

Grundeinheit **4 px**, mit Dichte-Multiplikator: weit 1,1 · normal 1,0 ·
kompakt 0,9. Vorgabe ist „weit", auf Berührung greift „normal".

```css
--am-einheit: 0.25rem;
--am-skalierung: 1.1;
--am-raum-N: calc(var(--am-einheit) * N * var(--am-skalierung));
```

Stufen: 1 · 2 · 3 · 4 · 6 · 8 · 12 · 16. Ein Hebel, keine zweite
Wertetabelle. **Keine festen Pixelwerte.**

## Zielgrößen

| | |
|---|---|
| Zeiger | 2.5rem (40 px) |
| Berührung | 2.75rem (44 px), **ohne Ausnahme** |
| Abstand | 0.5rem, bei Berührung 0.75rem |

## Schrift

Geist Sans und Geist Mono, **selbst gehostet**. Kein Google-CDN, auch
nicht zur Bauzeit — bei einem Produkt, das Datensouveränität verspricht,
wäre ein Fremdabruf beim Bauen die falsche Fußnote.

## Weiteres

- Kein Verlauf, kein Schatten.
- Randstärken: ruhend 1px, betont 2px.
- Bewegung nur als dokumentierte Ausnahme: kurz 120 ms, lang 200 ms.
- Genau **eine primäre Handlung je Ansicht**.
- Kein Nachladen von fremden Servern.
- WCAG 2.2 AA. Farbe allein trägt keine Information (1.4.1).

## Was move davon übernimmt — und was bewusst nicht

move ist eine dunkle Ein-Seiten-Oberfläche. Der Standard ist größer als
das, und die Auslassungen sind Entscheidungen, keine Lücken:

| | move |
|---|---|
| Farbwerte, beide Rampen | **vollständig übernommen** |
| Rollenumkehr, Dunkelmodus | übernommen — Gold handelt |
| Hellmodus | **nicht gebaut.** Es gibt keinen Umschalter; `color-scheme: dark` |
| Dichte-Hebel | vorhanden, fest auf `1` (normal). Ein Wert, eine Stelle |
| Zustandsfarben (`--am-erfolg` …) | **nicht benutzt.** CLAUDE.md verbietet eine dritte Farbfamilie und ist für move die strengere Regel. Ein Job-Zustand steht als **Wort** da, nicht als Farbfleck |
| Radien | nicht benutzt; move zeichnet rechtwinklig |

## Was korrigiert wurde

Der Abgleich gegen das Paket hat vier Werte gefunden, die moves
`globals.css` frei erfunden hatte — sie stammten aus der Zusammenfassung
im Platzhalter, nicht aus der Quelle:

| Token | war | ist |
|---|---|---|
| `--am-blau-800` (Blockfläche) | `#0a2138` | `#0a2238` |
| `--am-blau-700` (Rand) | `#14304a` | `#142e47` |
| Gold hover | `#e2cc96` | `#d7ba7b` (`--am-gold-400`) |
| Text primär | `#dde5ed` | `#ffffff` |
| Text gedämpft | `#95a8bb` | `#afc0d2` (`--am-blau-300`) |

Im Browser zurückgelesen, nicht in der Quelle nachgesehen:
`rgb(5 23 41)` Grundfläche, `rgb(10 34 56)` Block, `rgb(20 46 71)` Rand,
`rgb(202 169 96)` primäre Handlung, Knopfhöhe 50,8 px.

### Eine Abweichung, die bleibt: der Fokusring

Das Paket setzt im Dunkelmodus `--am-fokus-ring` auf `--am-gold-900`
(`#71560f`). Gemessen gegen unsere Flächen:

| | Kontrast |
|---|---|
| gold-900 gegen Grundfläche `#051729` | **2,62:1** |
| gold-900 gegen Blockfläche `#0a2238` | **2,34:1** |
| gold-500 gegen Grundfläche | 8,06:1 |
| gold-500 gegen Blockfläche | 7,20:1 |

**WCAG 2.4.11 verlangt für den Fokusindikator 3:1 gegen die
Nachbarfarbe**, und dieser Guide verlangt AA. Der Wert widerspricht sich
also selbst, sobald er auf diesen Flächen liegt. move nimmt `gold-500`.

Das ist kein Freibrief: es ist die einzige Stelle, an der move einen
gelieferten Wert überstimmt, und sie steht mit Messung im Code. Marc
gehört es gesagt — auf insilos Flächen kann dasselbe Problem liegen.
