# Better Lighting — Benutzerhandbuch

Wie man ein Haus einrichtet, in der Reihenfolge, die funktioniert: erst ein
Raum, dann die Dinge, die ihn selbstständig machen. Außer dem ersten Schritt
ist jeder optional. Nichts davon braucht YAML.

---

## Bevor es losgeht

- **Home Assistant 2026.1 oder neuer.**
- **Deine Lampen funktionieren bereits in Home Assistant.** Diese Integration
  steuert vorhandene Licht-Entitäten; sie spricht nicht selbst mit Leuchten.
- **Die Integration `recorder`**, falls die Anwesenheitssimulation
  wiedergeben soll, was dein Haus tatsächlich getan hat. Alles andere
  funktioniert auch ohne.
- **Entscheide nichts im Voraus.** Jede Einstellung lässt sich später ändern,
  und keine Änderung verliert deine Szenen.

---

## Schritt 1 — Die Integration hinzufügen

**Einstellungen → Geräte & Dienste → Integration hinzufügen → Better
Lighting.**

Du wirst nach hausweiten Vorgaben gefragt: wie hell und wie warm deine
Lampen am dunkelsten und am hellsten Punkt des Tages sein sollen. Die
vorausgefüllten Werte passen für die meisten Häuser. Das ist die *Kurve*:
was „am dunkelsten“ und „am hellsten“ hier bedeuten.

Du bekommst einen Eintrag **Better Lighting** und einen Punkt **Better
Lighting** in der Seitenleiste. Fast alles Weitere passiert in diesem Panel.

> Es gibt immer nur einen Eintrag. Alles andere liegt darin.

---

## Schritt 2 — Der erste Raum

**Panel → Räume → Raum hinzufügen.** Name, Symbol, Lampen.

Mehr ist nicht nötig. Du hast jetzt:

| Entität | Wofür |
|---|---|
| `light.<raum>` | Der Raum als eine Lampe. Dimmen dimmt den Raum *relativ*: eine Lampe auf 20 % und ein Spot auf 80 % gehen beide herunter und behalten ihren Abstand. |
| `select.<raum>_scene` | Welche Szene der Raum zeigt, oder *Adaptiv*. |
| `switch.<raum>_adaptive` | Ob die Sonne diesen Raum steuert. |
| `switch.<raum>_night` | Nachtmodus für diesen Raum. |
| Schaltflächen | Nächste Szene, vorherige Szene, zurück zu adaptiv, manuelle Steuerung vergessen. |

Schalte den Raum ein. Er folgt ab sofort der Sonne, ohne weitere
Einrichtung.

### Eine Lampe, ein Raum

Eine Lampe gehört zu **genau einem Raum**, und Speichern wird verweigert,
wenn zwei Räume dieselbe Leuchte beanspruchen. Das ist keine Einschränkung,
um die man herumarbeiten müsste — es ist genau das, was die Integration ohne
Raten wissen lässt, wem eine Leuchte gehört, wenn jemand einen Schalter
drückt.

Gehört eine Leuchte wirklich zu zwei Bereichen, mach daraus einen Raum und
nutze **Zonen** (Schritt 10) für die Teile.

---

## Schritt 3 — Die Kurve ansehen

**Panel → Diagnose.** Raum auswählen. Du bekommst den Tag gezeichnet: eine
Linie für die Helligkeit, dahinter ein Band in der Farbtemperatur des
jeweiligen Moments, dazu Marken für Sonnenaufgang, Sonnenuntergang und jetzt.
Darunter steht, was jede Lampe *in dieser Sekunde* bekäme, nach den
Korrekturen und Grenzen dieses Raums.

Das ist der schnellste Weg, „abends fühlt es sich zu hell an“ zu beantworten.
Adaptive Einstellungen des Raums ändern, wieder hinschauen.

### Welche Einstellung gewinnt

Drei Ebenen, jede engt die darüber ein:

1. **Hausweite Vorgaben** — die Form des Tages für das ganze Haus.
2. **Raum-Überschreibungen** — dieser Raum ist dunkler, oder wärmer, oder
   beginnt früher zu verblassen.
3. **Lampenkalibrierung** — diese *Leuchte* misst zu niedrig, oder darf nie
   unter 15 %.

Minimum und Maximum von Haus und Raum definieren die *Kurve*. Minimum und
Maximum der Kalibrierung sind eine *Begrenzung*, die danach angewendet wird.
Der Unterschied ist wichtig: die Kurve sagt, wie der Tag aussieht, die
Begrenzung sagt, was eine bestimmte Leuchte verträgt.

### Helligkeitsverläufe

- **tanh** (Standard für neue Räume) beginnt *vor* Sonnenuntergang zu
  verblassen, der Abend kommt allmählich.
- **sun** folgt direkt dem Sonnenstand.

Fang mit tanh an. Wird es dir zu früh dunkel, wechsle zu sun.

---

## Schritt 4 — Ungleiche Leuchten angleichen

Eine Fassung mit einer Filamentlampe und einem LED-Streifen sieht nie
gleichmäßig aus. **Raum → Lampenkalibrierung → Hinzufügen.**

| Einstellung | Wann |
|---|---|
| **Helligkeitskorrektur** (Prozentpunkte) | Diese Lampe misst zu niedrig. `+10` hebt sie überall an. |
| **Farbtemperaturkorrektur** (Kelvin) | Diese Lampe läuft kälter als ihre Nachbarin. |
| **Minimale / maximale Helligkeit** | Unter 15 % flackert sie; über 80 % ist sie unerträglich. |

Die Korrektur wird **vor** der Begrenzung angewendet, mit Absicht: eine
Kalibrierung darf nie eine Betriebsgrenze verletzen. Wird ein Wert
abgeschnitten, steht das in der Diagnose des Raums, statt still zu
verschwinden.

---

## Schritt 5 — Szenen

Eine Szene ist **eine Liste der Lampen dieses Raums und wie jede aussehen
soll**. Szenen gehören zu einem Raum: eine Leseszene fürs Wohnzimmer und eine
fürs Schlafzimmer sind verschiedene Listen verschiedener Lampen.

**Panel → der Raum → Szenen → Hinzufügen.**

Zwei Wege:

- **Von Hand.** Lampen auswählen, Helligkeit und Farbe je Lampe setzen. Der
  Raum verändert sich beim Ziehen — deshalb haben Szenen eine Seite und kein
  Formular.
- **Den Raum aufnehmen, wie er gerade ist.** Bring den Raum auf beliebigem
  Weg in den richtigen Zustand und speichere, was du siehst.

Jede Lampe in einer Szene kann sein:

| Wahl | Ergebnis |
|---|---|
| Helligkeit und Farbe | Genau das. |
| **Nur Helligkeit** | So hell, Farbe folgt weiter der Sonne. |
| **Nur Farbe** | Diese Farbe, Helligkeit folgt weiter der Sonne. |
| **Aus** | Aus, solange diese Szene läuft. |
| **Unverändert lassen** | Unberührt — was sie gerade tut, tut sie weiter. |

*Nur Helligkeit* und *nur Farbe* werden am häufigsten übersehen. Eine
Filmszene, die die Farbe festlegt und die Helligkeit weiter dem Tag folgen
lässt, ist etwas anderes und Besseres als eine, die beides einfriert.

### Lampen, die die Szene nicht nennt

Jede Szene sagt, was mit dem Rest des Raums passiert: lassen, ausschalten
oder zurück auf die Kurve.

### Farbvorlagen

**Globale Einstellungen → Farbvorlagen.** Benenne eine Farbe einmal — „TV
Orange“, „Kerze“ — und wähle sie in jeder Szene über den Namen. Änderst du
die Vorlage, ändert sich jede Szene, die sie benutzt.

### Eine Szene ausblenden

Eine Szene kann aus dem Durchlauf eines Schalters (Schritt 6) und aus dem
Menü einer Dashboard-Karte (Schritt 14) herausgenommen werden, ohne sie zu
löschen. Nützlich für Szenen, die nur eine Automatisierung setzt.

---

## Schritt 6 — Wandschalter

Ein Raum ohne konfigurierten Schalter **läuft bereits durch**: ein einfaches
`light.turn_on` auf seiner Licht-Entität geht durch adaptiv und dann jede
Szene. Ein dummer Wandschalter funktioniert ohne jede Einrichtung.

Konfiguriere einen Schalter, wenn du mehr willst:

- **Eine bestimmte Reihenfolge.** Dieser Schalter geht adaptiv → Kochen →
  aus und überspringt die Filmszene.
- **Zwei Schalter in einem Raum mit unterschiedlichem Verhalten.** Der
  Türschalter geht alles durch, der Nachttischschalter direkt auf *Nacht*.
- **Eine Wippe.** Die untere Hälfte bekommt eigene Aktionen, und Halten an
  einem der Enden dimmt oder hellt auf.
- **Ein Schalter, der nur eine Zone steuert** (Schritt 10) — der Schalter am
  Schreibtisch beleuchtet den Schreibtisch und lässt die Couch in Ruhe.

**Panel → der Raum → Lichtschalter → Hinzufügen.** Binde ihn an die Entität,
die dein Schalter ohnehin erzeugt (ein `event`, ein `binary_sensor`, ein
`sensor`, ein `switch` oder ein `input_button`), und baue dann seine
geordnete Liste.

### Was ein Druck bedeutet

Ein Druck ist nicht nur „weiter“. Er **verwirft, was den Raum gerade
überstimmt**, und geht dann weiter. Steuert ein Hausmodus den Raum, holt der
erste Druck den Raum zurück, ohne weiterzugehen — einmal drücken bringt dich
also aus dem Film heraus, nochmal drücken startet den normalen Durchlauf. Du
musst nie zweimal drücken, ohne dass etwas passiert.

---

## Schritt 7 — Nachtmodus

Der Nachtmodus folgt **einem Helfer für das ganze Haus** — einem
`binary_sensor`, einem `input_boolean` oder einem `schedule`, den du schon
hast. Einstellbar unter **Globale Einstellungen**.

Jeder Raum entscheidet dann selbst, was das heißt:

| Raumverhalten | Was nachts passiert |
|---|---|
| **Minimale Einstellungen** | Helligkeit heruntergedeckelt, Farbe erwärmt. |
| **Eine Szene** | Dieser Raum zeigt seine Nachtszene. |
| **Aus** | Der Nachtmodus tut hier nichts. |

`switch.<raum>_night` spiegelt den Helfer und lässt sich auch von Hand
umlegen; beim nächsten Wechsel gewinnt der Helfer wieder.

**Anwesenheit nachts ignorieren** ist die Schlafzimmer-Einstellung: Bewegung
um drei Uhr nachts soll den Raum nicht beleuchten.

Die Karte zeigt ein Mond-Symbol, solange ein Raum tatsächlich im Nachtmodus
ist — was nicht dasselbe ist wie ein eingeschalteter Helfer, denn ein Raum
auf *Aus* sagt Nein.

---

## Schritt 8 — Bewegungs- und Türsensoren

Jeder Raum **und** jede Zone hat eigene Auslöser, einstellbar dort, wo du
den Raum ohnehin gerade ansiehst. **Panel → der Raum → Auslöser.**

### Auslöser sind ODER-verknüpft

Eine Einfahrt hat Bewegung, eine Garage ein Tor, eine Veranda vielleicht
beides. Einer reicht:

- **Solange ein Auslöser aktiv ist**, bleibt das Licht an.
- **Wenn der letzte frei wird**, geht es nach der eingestellten Verzögerung
  aus.
- **Ein neuer Auslöser während der Wartezeit startet sie neu.**

„Aktiv“ muss für die üblichen Sensoren nicht eingestellt werden: `on`,
`open`, `home` und `detected` zählen alle, ein Binärsensor, ein Rollladen und
ein Gerätetracker funktionieren also, wie sie sind. Für alles, was etwas
anderes meldet, gibst du den Zustand ausdrücklich an.

### Der Schalter schlägt den Timer

Hat jemand zum Schalter gegriffen — bevor je ein Sensor auslöste oder mitten
in der Wartezeit — **stoppt die Uhr**. Das Licht bleibt an, bis es von Hand
ausgeschaltet wird, und danach hat die Automatik es automatisch wieder. Du
musst nichts wieder aktivieren.

Schalte **Halten, wenn von Hand gesetzt** aus, wenn der Timer immer gewinnen
soll.

Die Karte zeigt ein Hand-Symbol, solange ein Raum so gehalten wird, und
einen Countdown, solange der Timer läuft.

### Ganzer Raum oder Zone

- Ein **Raum** wird über seine **Anwesenheits**-Einstellungen beleuchtet
  (Schritt 11).
- Eine **Zone** über *Diese Zone beleuchten, solange der Sensor aktiv ist* —
  so beleuchtet ein Bewegungsmelder die Einfahrt, ohne den Rest draußen
  anzufassen.

---

## Schritt 9 — Regeln

Regeln entscheiden, **ob ein Auslöser feuern darf**. Sie gehören zu
demselben Raum oder derselben Zone wie die Auslöser, die sie begrenzen.

| Regel | Prüft |
|---|---|
| **Zwischen zwei Uhrzeiten** | Die lokale Uhr. Ein Ende vor dem Anfang läuft über Mitternacht: 22:00 bis 06:00 ist eine Nacht. |
| **Ein Wert unter / über einer Schwelle** | Ein Lux-Sensor, eine Temperatur, alles Numerische. |
| **Eine Entität in einem bestimmten Zustand** | Ein Helfer, der die Automatik scharf schaltet, ein Urlaubsschalter, der sie abschaltet, oder `sun.sun` auf `below_horizon` — also „nach Einbruch der Dunkelheit“, ohne einen Lux-Sensor zu besitzen. |

### Sie sind UND-verknüpft

Jede Regel muss zutreffen. Eine Regel hinzuzufügen kann eine Automatik immer
nur **seltener** auslösen lassen — das Gegenteil eines weiteren Auslösers,
der sie nur häufiger auslösen lassen kann. Feuert etwas nicht, schau auf die
Regeln; feuert etwas zu oft, schau auf die Auslöser.

### Das Tagesband

Jede Regelliste wird als **Balken darunter** gezeichnet — grün, wo die Uhr
die Automatik offen lässt, rot, wo nicht, mit beschrifteten Zeiten. Ein
Fenster über Mitternacht liest sich als die beiden Enden einer Nacht und
nicht als Lücke. Nur die Uhr lässt sich so zeichnen; eine Lux-Schwelle gilt
weiterhin obendrauf.

### Unlesbare Sensoren blockieren standardmäßig

„Erlaubt nur, wenn jede Regel zutrifft“ — und eine Regel, die niemand
auswerten kann, trifft nicht zu. Jede Regel kann stattdessen durchlassen,
was du für einen wackeligen Sensor willst, der niemanden auf einem unbeleuchteten
Weg stehen lassen soll.

### Ein Rezept: die Veranda

- Auslöser: der Bewegungsmelder an der Tür.
- Regel: `sun.sun` ist `below_horizon`.
- Regel: zwischen 16:00 und 01:00.
- Ausschaltverzögerung: 2 Minuten.

Bei Tageslicht passiert nichts, um vier Uhr morgens passiert nichts, und
Besuch um elf bekommt pro Bewegung zwei Minuten Licht.

---

## Schritt 10 — Zonen: der Schreibtisch, der für den Film nicht dunkel wird

Ein Wohnzimmer ist ein Raum und mehrere Orte. Couch und Schreibtisch werden
zusammen beleuchtet, zusammen geschaltet und passen sich zusammen an — das
macht sie zu einem Raum — aber ein Schreibtisch, an dem jemand arbeitet,
sollte nicht abgedunkelt werden, weil der Rest des Raums einen Film schaut.

Eine Zone **folgt ihrem Raum, bis sie einen Grund dagegen hat**, und
Zurückkehren ist der Normalfall.

**Panel → der Raum → Zonen → Hinzufügen.** Gib ihr die Lampen, die zu ihr
gehören.

Was eine Zone bekommen kann:

- **Einen Anwesenheitssensor und *bei Anwesenheit aus einem Hausmodus
  aussteigen*.** Solange jemand da ist **und** ein raumübergreifender Modus
  den Raum steuert, macht die Zone für sich weiter. Ein Schreibtisch, an dem
  beim Filmstart schon jemand sitzt, wird gar nicht erst abgedunkelt.
- **Eine Rückkehrverzögerung.** Ist der Sensor so lange frei, geht die Zone
  zurück zu dem, was dem Raum gesagt wird. Nichts muss von Hand
  zurückgesetzt werden.
- **Einen eigenen Schalter.** Ein Schalter kann eine Zone benennen und
  steuert dann nur diese. Zurück auf adaptiv gestellt, kehrt die Zone in den
  Raum zurück.
- **Eine eigene adaptive Kurve.** Heller und kühler als der Raum um sie
  herum. Eine weitere Ebene derselben Art: Haus, dann Raum, dann Teil des
  Raums.
- **Eigene Auslöser und Regeln**, genau wie ein Raum sie hat.

Anwesenheit allein löst nichts aus dem Raum: steuert kein Modus den Raum,
ist ein belegter Schreibtisch Sache des Raums — dafür sind die
Anwesenheitseinstellungen des Raums da. Eine Lampe darf in höchstens einer
Zone sein, aus demselben Grund, aus dem sie in höchstens einem Raum ist.

---

## Schritt 11 — Anwesenheit für einen ganzen Raum

**Panel → der Raum → Anwesenheit.** Das ist, was die Auslöser des Raums mit
dem ganzen Raum machen.

| Einstellung | Wofür |
|---|---|
| **Was passiert, wenn jemand kommt** | Adaptiv, eine bestimmte Szene, oder nichts. |
| **Was passiert, wenn der Raum frei wird** | Aus, zurück zu adaptiv, oder nichts. |
| **Nur wenn das Licht aus ist** | Einen Raum nicht stören, den jemand schon eingestellt hat. |
| **Die Rollladen-Bedingung** | Diesen Raum nur beleuchten, wenn die Rollläden geschlossen sind — „hier drin ist es dunkel“ ohne Lux-Sensor. Ein unlesbarer Rollladen blockiert standardmäßig; das ist einstellbar. |

---

## Schritt 12 — Offene Fenster

**Panel → der Raum → Offene Fenster.** Zeig auf die Fenstersensoren dieses
Raums und wähle, was ein offenes Fenster bewirkt — meist ein bernsteinfarbenes,
insektenfreundliches Licht.

Es **schlägt einen Hausmodus**, weil ein offenes Fenster eine physische
Tatsache über diesen Raum ist, während ein Modus eine hausweite Vorliebe ist.
Einem Schalterdruck weicht es weiterhin: einmal drücken, und es ist verworfen,
bis das Fenster geschlossen und wieder geöffnet wird. Kein Timer beteiligt.

---

## Schritt 13 — Raumübergreifende Modi: Heimkino

Ein Modus ist **eine Sache, die mehrere Räume gleichzeitig ändert**, mit
benannten Zuständen.

**Panel → Modi → Hinzufügen.** Benenne ihn, benenne seine Zustände —
`playing`, `paused`, `ended` — und sag dann, was jeder Raum in jedem Zustand
tut.

Gesteuert wird er über `select.<modus>_state`. Das ist der ganze
Anknüpfungspunkt: deine Automatisierung für den Mediaplayer ruft
`select.select_option` auf, und sonst muss niemand irgendetwas wissen.

Was der Modus für dich erledigt:

- **Eine Momentaufnahme** jedes berührten Raums, **einmal** beim Start des
  Modus genommen und über Zustandswechsel hinweg behalten. (Neu aufzunehmen
  bei `paused` würde das Filmlicht festhalten, und am Ende ginge nichts
  wieder an.)
- **Wiederherstellen beim Beenden** auf *adaptiv, gefiltert auf die Lampen,
  die an waren*. Die Momentaufnahme liefert, welche Lampen; die Kurve
  liefert, wie hell. Die exakte Helligkeit von vor einer Stunde
  wiederherzustellen würde sofort gegen die Sonne arbeiten.
- **Ein Druck nimmt einen Raum heraus.** Wer mitten im Film zum
  Küchenschalter greift, behält die Küche. Sie wird am Ende auch nicht
  zurückgesetzt.
- **Aufgeschobene Aktionen.** „Küche ausschalten, wenn sie leer ist“ wartet,
  bis die Küche tatsächlich leer ist, prüft dann, dass der Film noch läuft
  und niemand den Raum übernommen hat, und handelt erst dann.

Ein Modus kann eigene **Regeln** tragen, dieselben Regeln, die ein Auslöser
benutzt.

---

## Schritt 14 — Anwesenheitssimulation

Solange das Haus leer ist, soll es bewohnt aussehen.

### Das eine, was stimmen muss

Zeig der Integration **einen Helfer, der sagt, dass niemand zu Hause ist** —
**Globale Einstellungen → Anwesenheitssimulation**. Bewusst *nicht* aus
Gerätetrackern hergeleitet: ein Gast, den niemand trackt, ist trotzdem jemand
zu Hause, und über dessen Kopf hinweg zu simulieren ist hier der einzige
Fehler, der wirklich zählt. Ein unlesbarer Helfer gilt als *nicht* leer.

### Was jeder Raum tut

| Modus | Was passiert |
|---|---|
| **Wiedergeben, was er tatsächlich getan hat** | Derselbe Wochentag vor einer Woche, aus dem Recorder — verschoben. Das ist die überzeugende Variante. |
| **Adaptiv beleuchten** | Gleichmäßig, und offensichtlich so. |
| **Eine Szene zeigen** | Ein festes Bild. |
| **Nicht teilnehmen** | Nichts. Ein Bad, das von der Straße niemand sieht, beweist nichts. |

Die Wiedergabe ist wichtiger, als sie klingt. Ein Flurlicht, das jeden Abend
um 19:03:12 angeht, bewirbt ein leeres Haus, statt es zu verbergen — deshalb
**verschiebt sich jeder Schritt um seinen eigenen Zufallsbetrag**, bis zur
eingestellten Streuung. Den ganzen Abend um dieselben zwölf Minuten zu
verschieben wäre immer noch exakt die letzte Woche. Die Reihenfolge übersteht
das Mischen: ein Licht geht weiterhin an, bevor es ausgeht. Wiedergegeben
wird nur, *welche Räume wann hell waren*; die Helligkeit kommt aus der Kurve
des Raums.

### Regeln, und Regeln je Raum

Dieselben Regeln, die ein Auslöser benutzt, begrenzen das Ganze. Hausweite
Regeln stehen unter **Globale Einstellungen → Anwesenheitssimulation**. Ein
Raum kann auf seiner eigenen Simulationsseite eigene hinzufügen, und **beide
müssen zutreffen**, bevor dieser Raum teilnimmt.

Ein Raum kann außerdem aussetzen, **solange seine Rollläden geschlossen
sind**, denn ein beleuchteter Raum, den niemand sieht, beweist nichts.

### Testen, ohne auf die Nacht zu warten

`switch.better_lighting_presence_simulation` zeigt, ob eine läuft und welche
Räume dabei sind. **Einschalten startet eine, unabhängig von den Regeln** —
so prüfst du die Einrichtung mitten am Nachmittag.

Sie stoppt in dem Moment, in dem der Helfer sagt, dass jemand zurück ist —
nicht am Ende des Zeitplans — und jeder Raum, den sie beleuchtet hat, geht
wieder aus, außer jemand hat auf dem Weg herein zum Schalter gegriffen.

Die Wiedergabe braucht den `recorder`. Ohne ihn findet dieser Modus nichts
zum Abspielen und lässt den Raum dunkel, was ein besserer Eindruck eines
leeren Hauses ist, als ihn den ganzen Abend zu beleuchten.

Die Karte zeigt ein Haus-Uhr-Symbol, solange ein Raum teilnimmt.

---

## Schritt 15 — Lichtgruppen

Drei smarte Leuchten in einer Fassung sollten sich gemeinsam ändern. Home
Assistant drei Entitäten setzen zu lassen schickt drei Zigbee-Befehle, und
man kann ihnen beim Eintreffen zusehen; eine Zigbee-Gruppenentität ist ein
einziger Multicast.

**Panel → der Raum → Lichtgruppen → Hinzufügen.** Gruppe benennen, Mitglieder
auflisten, und auf die Gruppenentität zeigen, die deine Zigbee-Integration
ohnehin bereitstellt.

Die Gruppe wird benutzt, wenn allen Mitgliedern dasselbe gesagt wird, und
fallen gelassen, sobald eines abweicht — eine Szene mit einer roten, einer
grünen und einer blauen Leuchte spricht sie also weiterhin einzeln an.

Gruppen können Gruppen enthalten. Ein 3×3 aus Deckenspots sind drei Reihen zu
dritt, und eine Szene kann die ganze Decke bernsteinfarben und die mittlere
Reihe gedimmt setzen — in zwei Zeilen statt neun. Der eigene Eintrag einer
Lampe schlägt jede Gruppe, die sie enthält. Eine Gruppe kann sich nicht
selbst enthalten; das Speichern wird mit Nennung der Schleife verweigert.

---

## Schritt 16 — Die Dashboard-Karte

**Dashboard bearbeiten → Karte hinzufügen → Better Lighting Raum.** Wähle das
Licht des Raums. Es gibt keine Ressource hinzuzufügen; die Karte wird für
dich geladen.

Je eine Reihe Bedienelemente:

- **Der Titel** öffnet den Lichtdialog von Home Assistant, in dem Farbe und
  Temperatur liegen.
- **Symbole** sagen, *warum* der Raum so aussieht — Anwesenheit, Nachtmodus,
  Anwesenheitssimulation, von Hand oder automatisch eingeschaltet, und ein
  Countdown, wenn der Raum sich demnächst ausschaltet. Sie sind alle im
  selben Grau: keines davon ist zum Drücken da.
- **Zurück zu adaptiv** erscheint nur, wenn der Raum *nicht* adaptiv ist. Es
  ist der Weg zurück nach einer Szene oder einer manuellen Änderung, kein
  Umschalter.
- **Ein/Aus** und ein **Helligkeitsbalken**, der den Raum so bewegt, wie der
  Raum eingestellt ist. Er funktioniert auch bei ausgeschaltetem Raum: eine
  Helligkeit dort schaltet ihn adaptiv mit dieser Helligkeit ein, während die
  Farbe weiter der Sonne folgt.
- **Szenen** als Zurück, Menü und Weiter.
- **Eine zusätzliche Schaltfläche**, optional, für etwas, das nicht diesen
  Raum betrifft — ein Hausmodus, ein Helfer, das Kino. Beliebige Entität, mit
  deren Symbol, sofern du kein anderes wählst.

In den Einstellungen der Karte kannst du außerdem **Szenen ausblenden**:
ausgeblendete Szenen fehlen im Menü *und* werden von den Pfeilen
übersprungen. Eine ausgeblendete Szene, die etwas anderes einschaltet, wird
trotzdem auf der Auswahl benannt — sie erscheint nur nie in der Liste.

---

## Schritt 17 — Die Bedienseite

**Panel → Bedienung.** Jeder Raum als die obige Karte, dazu das, was sonst ein
Dashboard bräuchte: der Schalter für die Anwesenheitssimulation, die
Zurück-zur-Nacht-Anforderung und jeder Hausmodus. Nützlich, bevor du ein
Dashboard gebaut hast, und nützlich, um eine Einrichtung von der Seite aus zu
testen, die sie konfiguriert hat.

---

## Automatisierungen und Dienste

Du brauchst sie selten — die Entitäten eines Raums decken das meiste ab —
aber es gibt sie.

| Dienst | Was er tut |
|---|---|
| `better_lighting.press` | Einen Schalterdruck simulieren, mit Nennung des Schalters. |
| `better_lighting.set_adaptive` | Einen Raum zurück auf die Kurve setzen. |
| `better_lighting.clear_manual` | Vergessen, dass eine Lampe von Hand gesetzt wurde. |

Für einen Modus rufst du `select.select_option` auf `select.<modus>_state`
auf. Für eine Szene `select.select_option` auf `select.<raum>_scene`.

---

## Wenn etwas überrascht

**Panel → Diagnose** ist die erste Anlaufstelle. Dort steht, was jeder Raum
und jeder Modus gerade glaubt — sein Modus, seine Szene, welche Lampen
manuell gesteuert sind, ob Nacht oder offenes Fenster gilt, welche
Modus-Sitzung ihn besitzt — daneben ein Live-Protokoll der Ereignisse, die
dorthin geführt haben. Der Zustand sagt, wo der Raum gelandet ist; das
Protokoll sagt, welcher Druck oder welcher Film ihn dorthin gebracht hat.

Es sind dieselben Daten wie im Diagnose-Download, Seite und Fehlerbericht
können sich also nicht widersprechen.

| Symptom | Nachsehen bei |
|---|---|
| **Ein Schalter tut nichts.** | Die Diagnose zeigt, was der Schalter gemeldet hat und was daraus gemacht wurde. Ein Wert, der zu keiner konfigurierten Aktion passt, ist die übliche Antwort. |
| **Ein Raum reagiert nicht auf Bewegung.** | Die Regeln. Sie sind UND-verknüpft, und ein unlesbarer Sensor blockiert standardmäßig. |
| **Ein Raum will nicht zurück in die Automatik.** | Jemand hält ihn von Hand. Einmal ausschalten oder *Zurück zu adaptiv* drücken. |
| **Eine Lampe ist an, Home Assistant zeigt sie aus.** | Das ist ein Problem auf Leuchtenebene; der Raum glaubt, was Home Assistant ihm sagt. |
| **Das Dashboard sagt, die Karte gibt es nicht.** | Die Seite, auf der du bist, wurde ausgeliefert, bevor die Integration eingerichtet war, oder dein Browser hält eine alte Kopie. Nutze **Frontend neu laden** im Drei-Punkte-Menü des Panels — es verwirft nur, was veralten kann. |
| **Änderungen im Panel erscheinen nicht.** | Dieselbe Schaltfläche. |

---

## Ein Abend, zusammengesetzt

- 16:30. Die Sonne sinkt; jeder Raum wird auf seiner eigenen Kurve wärmer und
  dunkler. Niemand hat etwas getan.
- 18:00. Der Verandasensor sieht jemanden. Die Regeln treffen zu — es ist
  nach Einbruch der Dunkelheit, es ist vor ein Uhr nachts — also leuchtet die
  Veranda zwei Minuten.
- 20:30. Der Film beginnt. Eine Automatisierung setzt
  `select.home_cinema_state` auf `playing`. Das Wohnzimmer dimmt auf seine
  Filmszene; die Küche soll ausgehen, ist aber belegt, also wartet die
  Anforderung. Der Schreibtisch in der Ecke ist belegt und steigt aus dem
  Modus aus: er bleibt, wie er war.
- 20:35. Die Küche wird leer. Die aufgeschobene Aktion prüft, dass der Film
  noch läuft und niemand die Küche übernommen hat, und schaltet sie aus.
- 21:10. Jemand drückt den Küchenschalter für ein Glas Wasser. Die Küche
  steigt aus dem Film aus und läuft normal ab adaptiv durch.
- 22:00. Der Nachthelfer geht an. Das Schlafzimmer deckelt auf sein Minimum
  und wird wärmer; das Wohnzimmer ignoriert das, weil es so eingestellt ist.
- 23:30. Der Film endet. Jeder Raum, den der Modus berührt hat, geht zurück
  auf adaptiv, gefiltert auf die Lampen, die beim Start an waren — mit der
  Helligkeit, die die Kurve für halb zwölf vorsieht, nicht der von halb neun.
  Die Küche, die ausgestiegen ist, bleibt genau so, wie die Person sie
  verlassen hat, die sie herausgenommen hat.
