"""Build translations/de.json from the English strings.

A mapping rather than a parallel file, so a string added to strings.json and
forgotten here fails loudly instead of silently shipping English. Duplicated
strings -- the add and reconfigure steps mirror each other -- translate once.

    .venv/bin/python scripts/make_german.py
"""

from __future__ import annotations

import json
import pathlib

COMPONENT = pathlib.Path(__file__).parents[1] / "custom_components" / "better_lighting"

DE: dict[str, str] = {
    # --- integration ------------------------------------------------------
    "Better Lighting": "Better Lighting",
    "These are the house-wide defaults. Every zone inherits them, and any zone or individual light can override them later.": "Dies sind die hausweiten Standardwerte. Jeder Raum übernimmt sie, und jeder Raum oder jede einzelne Lampe kann sie später überschreiben.",
    "Better Lighting is already set up. Add zones, scenes and controllers from its entry.": "Better Lighting ist bereits eingerichtet. Räume, Szenen und Schalter werden über den vorhandenen Eintrag hinzugefügt.",
    "This value is not valid.": "Dieser Wert ist ungültig.",
    "Global defaults": "Globale Standardwerte",
    "Changing these re-applies to every zone that has not overridden them.": "Änderungen wirken sich auf jeden Raum aus, der sie nicht überschrieben hat.",
    # --- hub options ------------------------------------------------------
    "Update interval": "Aktualisierungsintervall",
    "Transition": "Übergangsdauer",
    "Minimum brightness": "Minimale Helligkeit",
    "Maximum brightness": "Maximale Helligkeit",
    "Warmest colour temperature": "Wärmste Farbtemperatur",
    "Coldest colour temperature": "Kälteste Farbtemperatur",
    "Brightness curve": "Helligkeitsverlauf",
    "How often lights are re-adapted while in adaptive mode.": "Wie oft die Lampen im adaptiven Modus nachgeführt werden.",
    "Brightness at the darkest point of the day. This shapes the curve; individual lights clamp on top of it.": "Helligkeit zum dunkelsten Zeitpunkt des Tages. Das formt den Verlauf; einzelne Lampen begrenzen ihn zusätzlich.",
    "Brightness at the brightest point of the day.": "Helligkeit zum hellsten Zeitpunkt des Tages.",
    "'Sun' holds maximum all day and ramps only at night. 'Tanh' fades smoothly around sunrise and sunset, starting before the sun is down.": "„Sonne“ bleibt tagsüber auf Maximum und regelt nur nachts herunter. „Weich“ blendet rund um Sonnenauf- und -untergang gleichmäßig über und beginnt, bevor die Sonne weg ist.",
    "Advanced": "Erweitert",
    "Transition when turning on": "Übergangsdauer beim Einschalten",
    "Transition when changing scene": "Übergangsdauer beim Szenenwechsel",
    "Fade duration on the dark side": "Dauer des Übergangs zur dunklen Seite",
    "Fade duration on the light side": "Dauer des Übergangs zur hellen Seite",
    "Sunrise offset": "Versatz zum Sonnenaufgang",
    "Sunset offset": "Versatz zum Sonnenuntergang",
    "Night brightness": "Helligkeit im Nachtmodus",
    "Night colour temperature": "Farbtemperatur im Nachtmodus",
    "Prefer RGB over colour temperature": "RGB gegenüber Farbtemperatur bevorzugen",
    "Detect manual changes": "Manuelle Änderungen erkennen",
    "Forget a manual change after": "Manuelle Änderung vergessen nach",
    "Send brightness and colour separately": "Helligkeit und Farbe getrennt senden",
    "Delay between split commands": "Wartezeit zwischen getrennten Befehlen",
    "Intercept calls made directly to member lights": "Aufrufe direkt an einzelne Lampen abfangen",
    "When someone changes a light by hand, stop adapting it until it is switched off again or the timer below expires.": "Wenn jemand eine Lampe von Hand ändert, wird sie nicht mehr nachgeführt, bis sie wieder ausgeschaltet wird oder die Zeit unten abläuft.",
    "Some bulbs drop the colour when brightness arrives in the same command. Enable this only for those.": "Manche Leuchtmittel verlieren die Farbe, wenn die Helligkeit im selben Befehl kommt. Nur für solche aktivieren.",
    "Only needed if your switches are wired to the individual bulbs instead of the zone. Falls back automatically if unavailable.": "Nur nötig, wenn die Schalter direkt auf einzelne Leuchtmittel statt auf den Raum wirken. Fällt automatisch zurück, wenn nicht verfügbar.",
    # --- zone -------------------------------------------------------------
    "Zone": "Raum",
    "Add zone": "Raum hinzufügen",
    "Reconfigure zone": "Raum neu konfigurieren",
    "A zone is one room's lights, controlled together. Each light can belong to only one zone.": "Ein Raum sind die gemeinsam gesteuerten Lampen eines Zimmers. Jede Lampe kann nur zu einem Raum gehören.",
    "Name": "Name",
    "Lights": "Lampen",
    "Icon": "Symbol",
    "Area": "Bereich",
    "The lights in this room. A light may belong to only one zone, so that manual changes and scene control are never ambiguous.": "Die Lampen in diesem Zimmer. Eine Lampe darf nur zu einem Raum gehören, damit manuelle Änderungen und Szenensteuerung immer eindeutig bleiben.",
    "Group behaviour": "Gruppenverhalten",
    "Only report on when every light is on": "Nur als „an“ melden, wenn jede Lampe an ist",
    "Hide the individual lights": "Einzelne Lampen ausblenden",
    "Remember which lights were on": "Merken, welche Lampen an waren",
    "Reported brightness": "Gemeldete Helligkeit",
    "Expand nested light groups": "Verschachtelte Lichtgruppen auflösen",
    "When the zone is switched back on, light only the lights that were on when it was switched off.": "Beim Wiedereinschalten nur die Lampen einschalten, die beim Ausschalten an waren.",
    "How one brightness value is derived from the lights that are on.": "Wie aus den eingeschalteten Lampen ein Helligkeitswert gebildet wird.",
    "Pick at least one light.": "Mindestens eine Lampe auswählen.",
    "One of these lights already belongs to another zone. A light can only be in one zone.": "Eine dieser Lampen gehört bereits zu einem anderen Raum. Eine Lampe kann nur in einem Raum sein.",
    "Zone updated.": "Raum aktualisiert.",
    # --- zone adaptive ----------------------------------------------------
    "Adaptive lighting": "Adaptives Licht",
    "Override the global defaults": "Globale Standardwerte überschreiben",
    "Start with adaptive lighting on": "Mit eingeschaltetem adaptivem Licht starten",
    "Leave off to follow the global defaults. Everything below is ignored until you turn this on.": "Ausgeschaltet lassen, um den globalen Standardwerten zu folgen. Alles Weitere wird ignoriert, solange dies aus ist.",
    # --- zone night -------------------------------------------------------
    "Night mode": "Nachtmodus",
    "Night mode follows": "Nachtmodus folgt",
    "What night mode does": "Was der Nachtmodus tut",
    "Night transition": "Übergangsdauer im Nachtmodus",
    "Ignore presence while in night mode": "Anwesenheit im Nachtmodus ignorieren",
    "Night scene": "Nachtszene",
    "An existing helper or sensor that is on when the house is asleep. This zone's night switch mirrors it.": "Ein vorhandener Helfer oder Sensor, der an ist, wenn das Haus schläft. Der Nachtschalter dieses Raums spiegelt ihn.",
    "For bedrooms: stop a presence sensor turning the light on during the night.": "Für Schlafzimmer: verhindert, dass ein Anwesenheitssensor nachts das Licht einschaltet.",
    "Used when 'What night mode does' is set to apply a scene.": "Wird verwendet, wenn „Was der Nachtmodus tut“ auf eine Szene eingestellt ist.",
    # --- zone power -------------------------------------------------------
    "Switching back on": "Wiedereinschalten",
    "After the room is switched off": "Nachdem der Raum ausgeschaltet wurde",
    "Forget the last scene after": "Letzte Szene vergessen nach",
    "So a room left off overnight starts the next morning in adaptive rather than in last night's dinner scene.": "Damit ein über Nacht ausgeschalteter Raum am nächsten Morgen adaptiv startet und nicht in der Abendessen-Szene von gestern.",
    # --- zone presence ----------------------------------------------------
    "Presence": "Anwesenheit",
    "Occupancy sensor": "Anwesenheitssensor",
    "Consider the room empty after": "Raum als leer betrachten nach",
    "When somebody walks in": "Wenn jemand hereinkommt",
    "Scene to apply": "Anzuwendende Szene",
    "When the room empties": "Wenn der Raum leer wird",
    "Only when these covers are closed": "Nur wenn diese Rollläden geschlossen sind",
    "Cover condition": "Rollladen-Bedingung",
    "Only act on a room that is off": "Nur auf einen ausgeschalteten Raum wirken",
    "Leave a hand-set room alone": "Von Hand eingestellten Raum in Ruhe lassen",
    "An unreadable cover blocks": "Nicht auslesbarer Rollladen blockiert",
    "Occupancy sensors flicker. Waiting avoids switching a room off while somebody is still in it.": "Anwesenheitssensoren flackern. Die Wartezeit verhindert, dass ein Raum ausgeschaltet wird, während noch jemand darin ist.",
    "So the lights only come on when the blinds are down and the room is actually dark. Closing a cover while somebody is already in the room counts too.": "Damit das Licht nur angeht, wenn die Rollläden unten und der Raum wirklich dunkel ist. Ein Rollladen, der geschlossen wird, während jemand schon im Raum ist, zählt ebenfalls.",
    "Walking into a room you already lit by hand should not restyle it.": "Wer einen bereits von Hand beleuchteten Raum betritt, soll ihn nicht umgestellt vorfinden.",
    "Switching off behind somebody who set the room by hand is the rudest possible reading of an empty room.": "Hinter jemandem auszuschalten, der den Raum von Hand eingestellt hat, ist die unfreundlichste Auslegung eines leeren Raums.",
    "The point of the gate is 'it is dark in here', and a cover we cannot read is not evidence of that.": "Die Bedingung steht für „hier ist es dunkel“, und ein nicht auslesbarer Rollladen ist dafür kein Beleg.",
    # --- zone insect ------------------------------------------------------
    "Open windows": "Offene Fenster",
    "Door and window sensors": "Tür- und Fenstersensoren",
    "Only if the room is already lit": "Nur wenn der Raum bereits beleuchtet ist",
    "Wait before reacting to an open window": "Wartezeit vor Reaktion auf ein offenes Fenster",
    "Wait before reacting to a closed window": "Wartezeit vor Reaktion auf ein geschlossenes Fenster",
    "A switch press overrides it": "Ein Tastendruck setzt ihn außer Kraft",
    "Usually something deep amber, which attracts far fewer insects than white light.": "Üblicherweise ein tiefes Bernstein, das deutlich weniger Insekten anzieht als weißes Licht.",
    "An open window is no reason to light a dark room.": "Ein offenes Fenster ist kein Grund, einen dunklen Raum zu beleuchten.",
    "Stops a slamming window flickering the room.": "Verhindert, dass ein zuschlagendes Fenster den Raum flackern lässt.",
}

DE |= {
    # --- scene ------------------------------------------------------------
    "Scene": "Szene",
    "Add scene": "Szene hinzufügen",
    "Reconfigure scene": "Szene neu konfigurieren",
    "Scene updated.": "Szene aktualisiert.",
    "A scene is a recipe, not a room. Define it once and apply it anywhere. Choose carefully what it takes over: whatever it leaves alone keeps following the sun.": "Eine Szene ist ein Rezept, kein Raum. Einmal definieren und überall anwenden. Gut überlegen, was sie übernimmt: Was sie in Ruhe lässt, folgt weiter der Sonne.",
    "This scene sets": "Diese Szene setzt",
    "Brightness": "Helligkeit",
    "Colour given as": "Farbe angegeben als",
    "Offer this scene in": "Szene anbieten in",
    "'Brightness' pins the brightness and lets the colour keep tracking the sun. 'Colour' does the opposite. 'Both' fixes the look entirely. 'Neither' only chooses which lights are on.": "„Helligkeit“ legt die Helligkeit fest und lässt die Farbe weiter der Sonne folgen. „Farbe“ macht es umgekehrt. „Beides“ legt das Aussehen vollständig fest. „Weder noch“ wählt nur, welche Lampen an sind.",
    "The next step asks for the colour itself. Choose 'none' for a scene that only sets brightness.": "Der nächste Schritt fragt nach der Farbe selbst. „Keine Farbe“ wählen für eine Szene, die nur die Helligkeit setzt.",
    "Leave empty for a scene the whole house uses, such as a night scene. Naming one room keeps it out of every other room's list, and puts the room in front of its name so a Reading scene in the living room and one in the bedroom can both simply be called Reading.": "Leer lassen für eine Szene, die das ganze Haus nutzt, etwa eine Nachtszene. Wird ein Raum angegeben, erscheint die Szene in keiner anderen Raumliste und trägt den Raum vor ihrem Namen — so können eine Lese-Szene im Wohnzimmer und eine im Schlafzimmer beide einfach „Lesen“ heißen.",
    "Behaviour": "Verhalten",
    "Only affect lights that are already on": "Nur Lampen beeinflussen, die bereits an sind",
    "Ignore presence sensors while active": "Anwesenheitssensoren ignorieren, solange aktiv",
    "Lights this scene does not mention": "Lampen, die diese Szene nicht nennt",
    "If a light cannot show this colour": "Wenn eine Lampe diese Farbe nicht darstellen kann",
    "Adjust the room without lighting it up. Lights that are off stay off.": "Den Raum anpassen, ohne ihn zu erhellen. Ausgeschaltete Lampen bleiben aus.",
    "'Adaptive' keeps them following the sun, which is usually what you want.": "„Adaptiv“ lässt sie weiter der Sonne folgen, was meist gewünscht ist.",
    "'Adaptive' leaves it on its normal white, which reads honestly as 'that bulb cannot do colour'.": "„Adaptiv“ belässt sie bei ihrem normalen Weiß, was ehrlich zeigt: Dieses Leuchtmittel kann keine Farbe.",
    "Scene colour": "Szenenfarbe",
    "Pick the colour for this scene.": "Farbe für diese Szene auswählen.",
    "Colour temperature": "Farbtemperatur",
    "Colour": "Farbe",
    "Colour name": "Farbname",
    "A CSS colour name, such as 'coral' or 'warmwhite'.": "Ein CSS-Farbname, etwa „coral“ oder „warmwhite“.",
    # --- scene per-light --------------------------------------------------
    "Individual lights": "Einzelne Lampen",
    "Named lights:\n\n{lights}": "Benannte Lampen:\n\n{lights}",
    "Anything you do not name here takes the scene's own brightness and colour. Name a light to give it different values, switch it off while the rest of the room is lit, or leave it untouched.": "Alles, was hier nicht benannt wird, übernimmt Helligkeit und Farbe der Szene. Eine Lampe benennen, um ihr eigene Werte zu geben, sie auszuschalten, während der Rest des Raums leuchtet, oder sie unberührt zu lassen.",
    "Name a light": "Lampe benennen",
    "Stop naming a light": "Lampe nicht mehr benennen",
    "Done": "Fertig",
    "Light": "Lampe",
    "What happens to it": "Was mit ihr geschieht",
    "Colour for this light": "Farbe für diese Lampe",
    "Colour for {light}": "Farbe für {light}",
    "Leave empty to use the scene's own brightness.": "Leer lassen, um die Helligkeit der Szene zu verwenden.",
    "'Leave the colour to the sun' lets this light take the scene's brightness while its colour keeps adapting — useful for a desk lamp in a film scene. You are asked for the colour itself next.": "„Farbe der Sonne überlassen“ lässt diese Lampe die Helligkeit der Szene übernehmen, während ihre Farbe weiter nachgeführt wird — nützlich für eine Schreibtischlampe in einer Filmszene. Nach der Farbe selbst wird als Nächstes gefragt.",
    "Warm white channel": "Warmweiß-Kanal",
    "Cold white channel": "Kaltweiß-Kanal",
    "RGBWW strips have white LEDs behind the colour ones. Leave both at zero for a pure colour; raise the warm channel to sit a strip against a wooden floor.": "RGBWW-Streifen haben weiße LEDs hinter den farbigen. Beide auf null lassen für eine reine Farbe; den Warmkanal anheben, damit ein Streifen zu einem Holzboden passt.",
    "Choose a light.": "Eine Lampe auswählen.",
    # --- light profile ----------------------------------------------------
    "Light profile": "Lampenabgleich",
    "Add light calibration": "Lampenabgleich hinzufügen",
    "Reconfigure light calibration": "Lampenabgleich neu konfigurieren",
    "Calibrate a light": "Lampe abgleichen",
    "Calibration updated.": "Abgleich aktualisiert.",
    "This light already has a calibration.": "Für diese Lampe gibt es bereits einen Abgleich.",
    "Trim one light so it matches its neighbours. Offsets are a calibration and are applied first; the minimum and maximum are an operating limit and are applied afterwards, so a calibration can never push a light past a limit you set.": "Eine einzelne Lampe so anpassen, dass sie zu ihren Nachbarn passt. Versätze sind ein Abgleich und werden zuerst angewendet; Minimum und Maximum sind eine Betriebsgrenze und werden danach angewendet — ein Abgleich kann eine Lampe also nie über eine gesetzte Grenze hinaus treiben.",
    "Enabled": "Aktiviert",
    "Brightness offset": "Helligkeitsversatz",
    "Colour temperature offset": "Farbtemperaturversatz",
    "Percentage points added to whatever the curve asks for. Use a negative value for a light that reads too bright.": "Prozentpunkte, die auf den Wert des Verlaufs addiert werden. Negativer Wert für eine Lampe, die zu hell wirkt.",
    "Kelvin added to the curve's colour temperature. Negative is warmer. Applied to colour-only bulbs too.": "Kelvin, die zur Farbtemperatur des Verlaufs addiert werden. Negativ ist wärmer. Gilt auch für reine Farbleuchtmittel.",
    "An absolute floor for this light, not a shift of the zone's range. A light that flickers below 15% should be set to 15.": "Eine absolute Untergrenze für diese Lampe, keine Verschiebung des Raumbereichs. Eine Lampe, die unter 15 % flackert, wird auf 15 gesetzt.",
    "Coldest allowed colour temperature": "Kälteste erlaubte Farbtemperatur",
    "Warmest allowed colour temperature": "Wärmste erlaubte Farbtemperatur",
    "Brightness multiplier": "Helligkeitsfaktor",
    "Respect the light's own limits": "Eigene Grenzen der Lampe beachten",
    "Adapt brightness": "Helligkeit nachführen",
    "Adapt colour": "Farbe nachführen",
}


# ---------------------------------------------------------------------------
# INCOMPLETE. The controller, mode, services, selector, entity and issue
# sections still need translating. `main()` refuses to write a file until
# every string is covered, so a partial mapping cannot ship as half-English.
# ---------------------------------------------------------------------------


def _translate(node: object) -> object:
    if isinstance(node, dict):
        return {key: _translate(value) for key, value in node.items()}
    return DE[node]


def _missing(node: object, out: set[str]) -> None:
    if isinstance(node, dict):
        for value in node.values():
            _missing(value, out)
    elif node not in DE:
        out.add(node)


def main() -> None:
    english = json.loads((COMPONENT / "strings.json").read_text())

    absent: set[str] = set()
    _missing(english, absent)
    if absent:
        print(f"{len(absent)} string(s) still untranslated, for example:")
        for value in sorted(absent)[:15]:
            print(f"  {value!r}")
        print("\nNothing written: a partial translation would ship as half-English.")
        raise SystemExit(1)

    target = COMPONENT / "translations" / "de.json"
    target.write_text(
        json.dumps(_translate(english), indent=2, ensure_ascii=False) + "\n"
    )
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
