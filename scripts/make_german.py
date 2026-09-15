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
    "A colour change also switches lights on": "Farbwechsel schaltet Lampen auch ein",
    "Off by default: changing a room's colour adjusts the light that is there rather than lighting the room. Turn this on to match how Home Assistant's own light groups behave.": "Standardmäßig aus: Ein Farbwechsel passt das vorhandene Licht an, statt den Raum zu beleuchten. Einschalten, um sich wie die Lichtgruppen von Home Assistant zu verhalten.",
    "When the zone is switched back on, light only the lights that were on when it was switched off.": "Beim Wiedereinschalten nur die Lampen einschalten, die beim Ausschalten an waren.",
    "How one brightness value is derived from the lights that are on.": "Wie aus den eingeschalteten Lampen ein Helligkeitswert gebildet wird.",
    "Pick at least one light.": "Mindestens eine Lampe auswählen.",
    "One of these lights already belongs to another zone. A light can only be in one zone.": "Eine dieser Lampen gehört bereits zu einem anderen Raum. Eine Lampe kann nur in einem Raum sein.",
    "Zone updated.": "Raum aktualisiert.",
    # --- zone adaptive ----------------------------------------------------
    "Adaptive lighting": "Adaptives Licht",
    "Override the global defaults": "Globale Standardwerte überschreiben",
    "Start following the sun's brightness": "Helligkeit der Sonne folgen",
    "Start following the sun's colour": "Farbe der Sonne folgen",
    "Brightness and colour are separate switches, so a room can keep warming through the evening while its brightness stays where you put it.": "Helligkeit und Farbe sind getrennte Schalter — ein Raum kann also abends weiter wärmer werden, während seine Helligkeit dort bleibt, wo sie eingestellt wurde.",
    "Adaptive brightness": "Adaptive Helligkeit",
    "Adaptive colour": "Adaptive Farbe",
    "Switch the room off once it is empty": "Den Raum ausschalten, sobald er leer ist",
    "'Switch the room off' waits until the room is empty before darkening it, and only when the helper above turns on \u2014 the zone's own night switch always dims rather than darkens.": "„Den Raum ausschalten“ wartet, bis der Raum leer ist, und gilt nur, wenn der Helfer oben angeht \u2014 der Nachtschalter des Raums dimmt immer, statt zu verdunkeln.",
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


DE |= {
    # --- light switch -----------------------------------------------------
    "Light switch": "Lichtschalter",
    "Add light switch": "Lichtschalter hinzufügen",
    "Reconfigure light switch": "Lichtschalter neu konfigurieren",
    "Light switch updated.": "Lichtschalter aktualisiert.",
    "A switch, and the order it cycles through. Two switches in one room can carry different orders, so the one by the oven can reach Cooking first while the one by the door does something else.": "Ein Schalter und die Reihenfolge, die er durchschaltet. Zwei Schalter in einem Raum können unterschiedliche Reihenfolgen haben — der am Herd erreicht also zuerst „Kochen“, während der an der Tür etwas anderes tut.",
    "Room": "Raum",
    "How this switch is heard": "Wie dieser Schalter erkannt wird",
    "Switch entity": "Schalter-Entität",
    "Handle plain on/off of this room's light": "Einfaches Ein/Aus des Raumlichts übernehmen",
    "'Watch an entity' is the only option that can tell which switch was pressed, which is what lets two switches in one room cycle differently.": "„Entität beobachten“ ist die einzige Option, die erkennen kann, welcher Schalter gedrückt wurde — nur so können zwei Schalter in einem Raum unterschiedlich durchschalten.",
    "The entity your switch publishes to. Button devices usually appear as an event entity; others use a sensor or binary sensor.": "Die Entität, auf die der Schalter meldet. Tastergeräte erscheinen meist als Event-Entität, andere als Sensor oder binärer Sensor.",
    "Plain light.turn_on on the room's light is attributed to this switch. Only one switch per room can do this, because Home Assistant cannot say which switch sent it.": "Ein einfaches light.turn_on auf das Raumlicht wird diesem Schalter zugeordnet. Das kann nur ein Schalter pro Raum, da Home Assistant nicht erkennen kann, welcher Schalter es gesendet hat.",
    "Pick the entity your switch publishes to.": "Die Entität auswählen, auf die der Schalter meldet.",
    "Where adaptive sits in the cycle": "Position von „Adaptiv“ in der Abfolge",
    "End the cycle by switching off": "Abfolge mit Ausschalten beenden",
    "Wrap back to the start": "Am Ende zum Anfang zurückspringen",
    "If the room is in something not on this list": "Wenn der Raum in etwas ist, das nicht auf dieser Liste steht",
    "Double press": "Doppeldruck",
    "Long press": "Langer Druck",
    "Values that count as a press": "Werte, die als Druck gelten",
    "Values that count as a double press": "Werte, die als Doppeldruck gelten",
    "Values that count as a long press": "Werte, die als langer Druck gelten",
    "Attribute carrying the press": "Attribut, das den Druck enthält",
    "Ignore repeats faster than": "Wiederholungen ignorieren schneller als",
    "Collapse taps within": "Tastendrücke zusammenfassen innerhalb",
    "'Restart' begins this switch's own list from the top, which lands on adaptive and is usually what you want.": "„Neu beginnen“ startet die eigene Liste dieses Schalters von vorn, landet also auf „Adaptiv“ — meist das gewünschte Verhalten.",
    "Defaults cover most Zigbee and Z-Wave buttons. Add your device's own words if it uses different ones.": "Die Vorgaben decken die meisten Zigbee- und Z-Wave-Taster ab. Eigene Begriffe ergänzen, falls das Gerät andere verwendet.",
    "Event entities keep the interesting value in an attribute rather than the state. Leave as event_type unless your device differs.": "Event-Entitäten führen den relevanten Wert in einem Attribut statt im Zustand. Auf event_type belassen, sofern das Gerät nichts anderes verwendet.",
    "Three quick taps move three places in one go, rather than flashing through the two in between.": "Drei schnelle Tastendrücke springen drei Schritte auf einmal, statt durch die beiden dazwischen zu blitzen.",
    "Cycle order": "Abfolge",
    "Current order:\n\n{order}": "Aktuelle Abfolge:\n\n{order}",
    "Add a scene": "Szene hinzufügen",
    "Move a scene": "Szene verschieben",
    "Remove a scene": "Szene entfernen",
    "New position": "Neue Position",
    # --- cross-zone mode --------------------------------------------------
    "Cross-zone mode": "Raumübergreifender Modus",
    "Add mode": "Modus hinzufügen",
    "Reconfigure mode": "Modus neu konfigurieren",
    "Mode updated.": "Modus aktualisiert.",
    "Mode": "Modus",
    "A mode spans several rooms and has named states an automation moves between -- playing, paused, credits. The next step defines what each room does in each state.": "Ein Modus umfasst mehrere Räume und hat benannte Zustände, zwischen denen eine Automatisierung wechselt — etwa „playing“, „paused“, „credits“. Der nächste Schritt legt fest, was jeder Raum in welchem Zustand tut.",
    "States": "Zustände",
    "The states your automation will select. 'off' always exists and ends the session.": "Die Zustände, die die Automatisierung auswählt. „off“ existiert immer und beendet die Sitzung.",
    "Remember the rooms when the mode starts": "Räume beim Start des Modus merken",
    "When the mode ends": "Wenn der Modus endet",
    "Rooms the user took back": "Räume, die zurückgenommen wurden",
    "Give up on a waiting instruction after": "Wartende Anweisung aufgeben nach",
    "The default relights only what was on beforehand, at whatever the curve says now.": "Standardmäßig wird nur wieder eingeschaltet, was vorher an war — mit dem Wert, den der Verlauf jetzt vorgibt.",
    "A room that never empties -- a stuck sensor, say -- must not leave an instruction armed forever.": "Ein Raum, der nie leer wird — etwa wegen eines hängenden Sensors — darf eine Anweisung nicht endlos scharf lassen.",
    "Rules": "Regeln",
    "Current rules:\n\n{rules}": "Aktuelle Regeln:\n\n{rules}",
    "Add a rule": "Regel hinzufügen",
    "Remove a rule": "Regel entfernen",
    "Rule": "Regel",
    "In these states": "In diesen Zuständen",
    "These rooms": "Diese Räume",
    "Do this": "Das tun",
    "Do not darken an occupied room": "Belegten Raum nicht verdunkeln",
    "Wait until the room empties": "Warten, bis der Raum leer ist",
    "If somebody walks in": "Wenn jemand hereinkommt",
    "When they leave again": "Wenn sie wieder gehen",
    "The instruction waits rather than being dropped, and is re-checked when the room finally empties.": "Die Anweisung wartet, statt verworfen zu werden, und wird erneut geprüft, sobald der Raum leer ist.",
    "A mode needs at least one state.": "Ein Modus braucht mindestens einen Zustand.",
    "Choose the scene to apply.": "Die anzuwendende Szene auswählen.",
    # --- entities ---------------------------------------------------------
    "Adaptive": "Adaptiv",
    "Night": "Nacht",
    "Enabled": "Aktiviert",
    "Mode ": "Modus ",
    "State": "Zustand",
    "Cycle": "Durchschalten",
    "Cycle back": "Zurückschalten",
    "Back to adaptive": "Zurück zu adaptiv",
    "Clear manual override": "Manuelle Änderung aufheben",
    "Clear": "Beenden",
    "Press": "Druck",
    # --- services ---------------------------------------------------------
    "Act as though a light switch in this room was pressed.": "So handeln, als wäre ein Lichtschalter in diesem Raum gedrückt worden.",
    "Room name or id.": "Raumname oder -ID.",
    "Switch": "Schalter",
    "Which switch's order to use. Defaults to the room's.": "Wessen Reihenfolge verwendet wird. Standard ist die des Raums.",
    "Kind": "Art",
    "press, double_press or long_press.": "press, double_press oder long_press.",
    "Move this room along its cycle.": "Diesen Raum in seiner Abfolge weiterschalten.",
    "Whose order to follow.": "Wessen Reihenfolge gefolgt wird.",
    "Direction": "Richtung",
    "next or previous.": "next oder previous.",
    "Return to adaptive": "Zurück zu adaptiv",
    "Return a room to adaptive lighting, clearing any scene or manual override.": "Einen Raum auf adaptives Licht zurücksetzen und dabei Szene und manuelle Änderungen aufheben.",
    "Activate scene": "Szene aktivieren",
    "Apply a scene to a room.": "Eine Szene auf einen Raum anwenden.",
    "Scene name or id.": "Szenenname oder -ID.",
    "Hand lights back to the adaptive engine.": "Lampen wieder der adaptiven Steuerung überlassen.",
    "Limit to these lights. Defaults to all in the room.": "Auf diese Lampen beschränken. Standard sind alle im Raum.",
    "Set mode state": "Moduszustand setzen",
    "Move a cross-zone mode to one of its states.": "Einen raumübergreifenden Modus in einen seiner Zustände versetzen.",
    "Mode name or id.": "Modusname oder -ID.",
    "One of the mode's states, or off.": "Einer der Zustände des Modus oder „off“.",
    "End mode": "Modus beenden",
    "End a mode's session and put the rooms back.": "Die Sitzung eines Modus beenden und die Räume zurücksetzen.",
    "Restore": "Wiederherstellen",
    "Put the rooms back.": "Die Räume zurücksetzen.",
    "Rejoin mode": "Modus wieder beitreten",
    "Hand a room the user took back to the mode again.": "Einen zurückgenommenen Raum wieder dem Modus überlassen.",
    # --- selector options -------------------------------------------------
    "Sun (flat by day, ramps at night)": "Sonne (tagsüber konstant, nachts abfallend)",
    "Smooth (fades around sunrise and sunset)": "Weich (blendet um Sonnenauf- und -untergang über)",
    "Average": "Durchschnitt",
    "Median": "Median",
    "Brightest light": "Hellste Lampe",
    "Dimmest light": "Dunkelste Lampe",
    "Dim and warm (minimum settings)": "Dimmen und wärmen (Minimalwerte)",
    "Apply a night scene": "Eine Nachtszene anwenden",
    "Do nothing": "Nichts tun",
    "Brightness and colour": "Helligkeit und Farbe",
    "Brightness only (colour keeps adapting)": "Nur Helligkeit (Farbe bleibt adaptiv)",
    "Colour only (brightness keeps adapting)": "Nur Farbe (Helligkeit bleibt adaptiv)",
    "Neither (only which lights are on)": "Weder noch (nur welche Lampen an sind)",
    "Colour picker": "Farbwähler",
    "No colour": "Keine Farbe",
    "Keep following the sun": "Weiter der Sonne folgen",
    "Switch them off": "Sie ausschalten",
    "Leave them exactly as they are": "Sie genau so lassen, wie sie sind",
    "Leave it on its adaptive white": "Bei ihrem adaptiven Weiß belassen",
    "Use the closest white": "Nächstliegendes Weiß verwenden",
    "Do not change its colour": "Ihre Farbe nicht ändern",
    "Watch an entity": "Entität beobachten",
    "Only the better_lighting.press service": "Nur der Dienst better_lighting.press",
    "Plain on/off of the room's light": "Einfaches Ein/Aus des Raumlichts",
    "Nothing": "Nichts",
    "Next in the cycle": "Nächstes in der Abfolge",
    "Previous in the cycle": "Vorheriges in der Abfolge",
    "Switch the room off": "Den Raum ausschalten",
    "Toggle night mode": "Nachtmodus umschalten",
    "First": "Zuerst",
    "Last": "Zuletzt",
    "Not in the cycle": "Nicht in der Abfolge",
    "Start this switch's list from the top": "Die Liste dieses Schalters von vorn beginnen",
    "Continue where this switch left off": "Dort fortfahren, wo dieser Schalter aufgehört hat",
    "Return to adaptive ": "Zurück zu adaptiv ",
    "Resume the last scene": "Letzte Szene fortsetzen",
    "Apply a scene": "Eine Szene anwenden",
    "Leave it alone": "In Ruhe lassen",
    "Return it to adaptive": "Auf adaptiv zurücksetzen",
    "Relight what was on, adaptively": "Adaptiv wieder einschalten, was an war",
    "Replay exactly what was showing": "Genau das wiederherstellen, was zu sehen war",
    "Leave the lights alone": "Die Lampen in Ruhe lassen",
    "Leave it as the user left it": "So lassen, wie es hinterlassen wurde",
    "Restore it anyway": "Trotzdem wiederherstellen",
    "Switch it off again": "Wieder ausschalten",
    "Leave it on": "Angeschaltet lassen",
    "Re-apply the mode's rule": "Die Regel des Modus erneut anwenden",
    "Only when every cover is closed": "Nur wenn jeder Rollladen geschlossen ist",
    "When any cover is closed": "Wenn irgendein Rollladen geschlossen ist",
    "Ignore the covers": "Rollläden ignorieren",
    "Whatever switching the room on would do": "Was auch immer das Einschalten des Raums täte",
    "A specific scene": "Eine bestimmte Szene",
    "Apply settings to it": "Einstellungen darauf anwenden",
    "Switch it off": "Sie ausschalten",
    "Leave it exactly as it is": "Genau so lassen, wie sie ist",
    "Use the scene's colour": "Die Farbe der Szene verwenden",
    "Leave the colour to the sun": "Farbe der Sonne überlassen",
    "Colour picker plus white channels (RGBWW)": "Farbwähler plus Weißkanäle (RGBWW)",
    # --- repair issues ----------------------------------------------------
    "Better Lighting refers to a scene that no longer exists": "Better Lighting verweist auf eine Szene, die nicht mehr existiert",
    "{holder} refers to {what} that has been deleted. Home Assistant cannot prevent a scene being removed while something still uses it, so the reference is simply skipped -- a switch with a deleted scene in its list quietly has a shorter cycle. Reconfigure the object above to clear this.": "{holder} verweist auf {what}, das gelöscht wurde. Home Assistant kann das Löschen einer Szene nicht verhindern, während sie noch verwendet wird, daher wird der Verweis einfach übersprungen — ein Schalter mit einer gelöschten Szene in seiner Liste hat dann stillschweigend eine kürzere Abfolge. Zum Beheben das oben genannte Objekt neu konfigurieren.",
    "Better Lighting refers to a room that no longer exists": "Better Lighting verweist auf einen Raum, der nicht mehr existiert",
    "{holder} refers to {what} that has been deleted, so it no longer does anything. Reconfigure or remove it to clear this.": "{holder} verweist auf {what}, das gelöscht wurde, und tut daher nichts mehr. Zum Beheben neu konfigurieren oder entfernen.",
    "A light belongs to two Better Lighting rooms": "Eine Lampe gehört zu zwei Better-Lighting-Räumen",
    "{light} is listed in both {first} and {second}. A light may belong to only one room -- that is what keeps manual changes, scene control and switch presses unambiguous. Remove it from one of them.": "{light} ist sowohl in {first} als auch in {second} aufgeführt. Eine Lampe darf nur zu einem Raum gehören — nur so bleiben manuelle Änderungen, Szenensteuerung und Tastendrücke eindeutig. Aus einem der beiden entfernen.",
    # --- scene per-light menu --------------------------------------------
    "Named lights:\n\n{lights}\n\nAnything you do not name here takes the scene's own brightness and colour. Name a light to give it different values, switch it off while the rest of the room is lit, or leave it untouched.": "Benannte Lampen:\n\n{lights}\n\nAlles, was hier nicht benannt wird, übernimmt Helligkeit und Farbe der Szene. Eine Lampe benennen, um ihr eigene Werte zu geben, sie auszuschalten, während der Rest des Raums leuchtet, oder sie unberührt zu lassen.",
}


# `main()` refuses to write a file unless every string is covered, so adding a
# string to strings.json and forgetting it here fails loudly rather than
# shipping a half-English translation.


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
