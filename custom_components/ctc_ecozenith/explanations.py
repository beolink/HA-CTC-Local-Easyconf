"""What each value on the CTC page means, and where it comes from.

Shown on the page when a name is hovered or tapped, the way the NIBE page explains
its registers. Free of Home Assistant imports, like dashboard_views.py, so every
explanation can be checked without an installation (tests/test_dashboard.py).

The texts rest on two sources. What a register is comes from CTC's BMS manual
(162 600 16), as const.py records it, together with what testing on an i255 and an
i550 Pro showed. What a value means to the person reading it comes from CTC's own
manual for the EcoZenith i255, chapter 11.7, which explains the operation data rows
the display shows. Where neither source settles what a value is, the text says so
rather than guessing.

Every text is written twice, in Swedish and in English (roadmap R23), as one
:class:`Text`: the page is built in Home Assistant's language, Swedish where it is
set to Swedish and English otherwise, and asks for its texts in that language.
Swedish is the reserve: a text whose English is missing reads in Swedish on an
English page rather than not at all, and tests/test_explanations_english.py
fails until the English is written. A name in a text is the name the reader
sees: an entity's name in the page's language, a display row's in the panel's
Swedish, a state or an option in the controller's Swedish, an attribute by the
name the entity carries.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from .const import (
    CONTROL_NUMBERS,
    CONTROL_SELECTS,
    COP_WINDOW_DAYS,
    DEFAULT_FAST_INTERVAL,
    DEFAULT_SLOW_INTERVAL,
    HARVEST_SKIP_LIMIT,
    MODBUS_SENSORS,
    MODBUS_SETTINGS,
)
from .cop import (
    DAY_MAX_HOURS,
    DAY_MIN_HOURS,
    MIN_CONSUMPTION_KWH,
    MIN_CONSUMPTION_KWH_DAY,
    RUNNING_HOURS,
    WINDOWS,
    YEAR_MAX_DAYS,
)
from .cop_estimate import (
    ESTIMATE_MAX,
    ESTIMATE_MIN,
    FIRST_READ,
    IMPLAUSIBLE,
    NO_DAY_READINGS,
    NO_ELECTRICITY,
    NO_HEAT,
    NO_HEAT_ROW,
    NO_INVERTER_ROW,
    SILENT,
)
from .integrate import MAX_GAP

#: The languages the texts are written in, Swedish first: it is the reserve.
SWEDISH = "sv"
ENGLISH = "en"


class Text(str):
    """A text in Swedish that carries its English beside it.

    The Swedish is the string itself, so whatever reads a table as text, a test
    that looks for a register number or a dash in it among them, reads the
    Swedish as it always has. The English is ``en``, empty where it has not been
    written. :meth:`in_language` gives the one the page asks for, and the
    Swedish where the English is missing.

    Two texts put together with ``+`` make a text in both languages. A plain
    string has no English, so a text put together with one has none either and
    reads in Swedish everywhere, which the test for English then points out,
    rather than an English page showing an English sentence with a Swedish one
    stuck to it.
    """

    en: str

    def __new__(cls, sv: str, en: str = "") -> Text:
        text = super().__new__(cls, sv)
        text.en = en
        return text

    def __add__(self, other: object) -> Text:
        if isinstance(other, Text):
            english = self.en + other.en if self.en and other.en else ""
            return Text(str(self) + str(other), english)
        if isinstance(other, str):
            return Text(str(self) + other)
        return NotImplemented

    def __radd__(self, other: object) -> Text:
        if isinstance(other, str):
            return Text(other + str(self))
        return NotImplemented

    def in_language(self, lang: str | None) -> str:
        """The English where the page is in English and it is written, else the Swedish."""
        if lang == ENGLISH and self.en:
            return self.en
        return str(self)


def in_language(text: str, lang: str | None) -> str:
    """A text from one of the tables in the page's language.

    A plain string, a text somebody added in Swedish alone, reads in Swedish
    whatever the language: it is the reserve.
    """
    return text.in_language(lang) if isinstance(text, Text) else str(text)


#: The floors the coefficient of performance figures are shown above, in the
#: words of the explanations: the day's, a week's and a month's as the day's
#: times the days, and the lifetime's. Read off cop.py so the text and the
#: rule cannot part ways again, as they did when the lifetime floor went from
#: fifty to ten and the explanation went on saying fifty.
_COP_FLOOR_DAY = f"{MIN_CONSUMPTION_KWH_DAY:.0f}"
_COP_FLOOR_WEEK = f"{MIN_CONSUMPTION_KWH_DAY * WINDOWS['week'][0]:.0f}"
_COP_FLOOR_MONTH = f"{MIN_CONSUMPTION_KWH_DAY * WINDOWS['month'][0]:.0f}"
_COP_FLOOR_LIFETIME = f"{MIN_CONSUMPTION_KWH:.0f}"

#: The other numbers the explanations name, read off the code for the same
#: reason: how old the samples behind the day, the week, the month and the
#: year may be, how often Modbus is polled and the display harvested by
#: default, and how many harvests in a row give way to somebody at the panel.
#: tests/test_explanation_numbers.py holds the words that stay words, five
#: minutes and ten alarms among them, to the constants they stand for, and
#: tests/test_explanations_english.py does the same for the English.
_DAY_HOURS = f"{DAY_MIN_HOURS} till {DAY_MAX_HOURS} timmar"
_DAY_HOURS_EN = f"{DAY_MIN_HOURS} to {DAY_MAX_HOURS} hours"
_WEEK_DAYS = f"{WINDOWS['week'][0]} till {sum(WINDOWS['week'])} dygn"
_WEEK_DAYS_EN = f"{WINDOWS['week'][0]} to {sum(WINDOWS['week'])} days"
_MONTH_DAYS = f"{WINDOWS['month'][0]} till {sum(WINDOWS['month'])} dygn"
_MONTH_DAYS_EN = f"{WINDOWS['month'][0]} to {sum(WINDOWS['month'])} days"
_YEAR_DAYS = f"{COP_WINDOW_DAYS} till {YEAR_MAX_DAYS} dagar"
_YEAR_DAYS_EN = f"{COP_WINDOW_DAYS} to {YEAR_MAX_DAYS} days"
_POLL_SECONDS = f"{DEFAULT_FAST_INTERVAL}:e sekund"
_POLL_SECONDS_EN = f"{DEFAULT_FAST_INTERVAL} seconds"
_HARVEST_MINUTES = f"{DEFAULT_SLOW_INTERVAL // 60}:e minut"
_HARVEST_MINUTES_EN = f"{DEFAULT_SLOW_INTERVAL // 60} minutes"
_SKIPS = {1: "ett varv", 2: "två varv", 3: "tre varv"}.get(HARVEST_SKIP_LIMIT, f"{HARVEST_SKIP_LIMIT} varv")
_SKIPS_EN = {1: "one round", 2: "two rounds", 3: "three rounds"}.get(
    HARVEST_SKIP_LIMIT, f"{HARVEST_SKIP_LIMIT} rounds"
)

#: The share of auxiliary heat (R28), in the words every span shares: what it
#: is, what the edges mean and what it is never made of, and why the longer
#: spans wait.
_SHARE_WHAT = Text(
    "Spetsvärmeandel: hur stor del av elen som gick till elpatronen, alltså elpatronens "
    "energi (register 62191) delat med elpatronens och kompressorns energi tillsammans. "
    "Kompressorns energi är samma räknare som värmefaktorn delar med.",
    "Share of auxiliary heat: how much of the electricity went to the immersion heater, that "
    "is the immersion heater's energy (register 62191) divided by the immersion heater's and "
    "the compressor's energy together. The compressor's energy is the same counter the "
    "coefficient of performance divides by.",
)
_SHARE_EDGES = Text(
    "100 procent visas bara när kompressorns räknare har stått still medan elpatronens har "
    "rört sig. Står kompressorns räknare på noll fast kompressorn har gått i minst "
    f"{RUNNING_HOURS} timmar (register 62214) visas ingen andel alls, eftersom styrenheten då "
    "inte fyller räknaren. Saknas talet säger attributet skäl (reason) varför, och elpatron "
    "kWh och kompressor kWh (immersion_kwh och compressor_kwh) visar energin i underlaget. "
    "Räknas aldrig ur displayens rader för 30 dagar och skickas inte med statistiken.",
    "100 per cent is shown only when the compressor's counter has stood still while the "
    "immersion heater's has moved. If the compressor's counter stands at zero although the "
    f"compressor has run for at least {RUNNING_HOURS} hours (register 62214), no share is "
    "shown at all, since the controller then does not fill the counter. Where there is no "
    "figure, the attribute skäl (reason) says why, and elpatron kWh and kompressor kWh "
    "(immersion_kwh and compressor_kwh) show the energy it rests on. Never worked out from "
    "the display's 30 day rows and not sent with the statistics.",
)
_SHARE_SAMPLED = Text(
    "Elpatronens räknare sparas i integrationens egna avläsningar sedan andelen kom till, så "
    "andelen kommer när en sådan avläsning är gammal nog.",
    "The immersion heater's counter is kept in the integration's own readings since the share "
    "was added, so the share appears once such a reading is old enough.",
)

#: The estimate from the display's two powers (cop_estimate.py), in the words its
#: three sensors share: what it is, what it rests on, how far it is from the
#: counters, and when it has no figure. The bounds and the gap are read off the
#: code, as the floors are above.
_ESTIMATE_BOUNDS = f"{ESTIMATE_MIN:.0f} till {ESTIMATE_MAX:.0f}"
_ESTIMATE_BOUNDS_EN = f"{ESTIMATE_MIN:.0f} to {ESTIMATE_MAX:.0f}"
_ESTIMATE_GAP = f"{int(MAX_GAP.total_seconds() // 3600)} timmar"
_ESTIMATE_GAP_EN = f"{int(MAX_GAP.total_seconds() // 3600)} hours"
_ESTIMATE_WHAT = Text(
    "Uppskattning, ingen räknare: avgiven värme delat med inverterns el, där båda är integrerade "
    "ur displayens två effekter, Avgiven värme (kW) och Inverter Motoreffekt (kW), som läses i "
    "samma skörd. Uppskattningen är grov men vetenskapligt grundad: den bygger på att integrera "
    "två uppmätta effekter över tid. Elen är inverterns effekt till kompressorn, inte pumpens "
    "hela tillförda el, så talet kan ligga något högre än räknarna.",
    "An estimate, not a counter: delivered heat divided by the inverter's electricity, where both "
    "are integrated from the display's two powers, Avgiven värme (kW) and Inverter Motoreffekt "
    "(kW), read in the same harvest. The estimate is rough but scientifically grounded: it rests "
    "on integrating two measured powers over time. The electricity is the inverter's power to the "
    "compressor, not the heat pump's whole supplied electricity, so the figure can run somewhat "
    "above the counters.",
)
_ESTIMATE_EDGES = Text(
    "Visas inte där värmen är noll hela tiden, eller när talet ligger utanför "
    f"{_ESTIMATE_BOUNDS}. Pumpar som inte redovisar avgiven värme får inget tal, och attributet "
    "skäl säger då varför. Attributen method, heat_hours och electric_hours visar hur talet "
    "är framtaget och hur många timmar mätningar det vilar på.",
    "Not shown where the heat has been zero all the time, or where the figure lies outside "
    f"{_ESTIMATE_BOUNDS_EN}. Heat pumps that do not report any heat output get no figure, and "
    "the attribute reason says why. The attributes method, heat_hours and electric_hours show how "
    "the figure is made and how many hours of measurements it rests on.",
)

#: When the day's starts and defrosts begin again from nought. The watch that
#: counts them is built anew by every set-up and keeps nothing on disk, so a
#: reload of the entry clears them as a restart does: saved options, the menu
#: written after an update, Home Assistant's reload after entities were
#: switched on. tests/test_explanation_numbers.py holds this to the code.
_COUNT_RESETS = Text(
    "Nollas vid midnatt och när posten laddas om, till exempel vid omstart av Home "
    "Assistant eller när alternativen sparas; attributet räknas sedan säger från när.",
    "Starts again from nought at midnight and whenever the entry is reloaded, for "
    "instance when Home Assistant restarts or the options are saved; the attribute "
    "räknas sedan (counting since) says from when.",
)

#: The Modbus registers the integration reads, by key.
MODBUS: dict[str, Text] = {
    "outdoor_temp": Text(
        "Utetemperaturen från pumpens utegivare. Värmekurvan räknar framledningens börvärde ur den.",
        "The outdoor temperature from the heat pump's outdoor sensor. The heating curve works "
        "out the flow setpoint from it.",
    ),
    "dhw_stop_temp": Text(
        "Temperaturen där pumpen slutar ladda varmvatten, enligt CTC:s registerlista.",
        "The temperature at which the heat pump stops charging hot water, according to CTC's "
        "register list.",
    ),
    "dhw_temp_raw": Text(
        "Dokumenterad som varmvattentemperatur, men står på 0 på en i550 Pro, där Varmvatten "
        "(register 62276) är den givare som lever.",
        "Documented as the hot water temperature, but reads 0 on an i550 Pro, where Hot water "
        "(register 62276) is the sensor that is live.",
    ),
    "system_status": Text(
        "Vad styrenheten gör just nu. Värmepump övre: värmepumpen värmer tankens övre del, "
        "alltså varmvatten. Värmepump nedre: den nedre delen, värme till huset. Spetsvärme: bara "
        "elpatronen värmer. Värmepump och spets: båda samtidigt.",
        "What the controller is doing right now, in its own Swedish words. Värmepump övre: the "
        "heat pump heats the upper part of the tank, that is hot water. Värmepump nedre: the "
        "lower part, heat for the house. Spetsvärme: only the immersion heater heats. Värmepump "
        "och spets: both at once.",
    ),
    "radiator_temp": Text(
        "Temperaturen på radiatorvattnet, där en sådan givare finns.",
        "The temperature of the radiator water, where such a sensor is fitted.",
    ),
    "hs1_flow_setpoint": Text(
        "Den temperatur vattnet ut till värmesystem 1 ska ha just nu, räknad ur värmekurvan och "
        "utetemperaturen. Står på 0 när värmen är avstängd.",
        "The temperature the water out to heating system 1 is to have right now, worked out from "
        "the heating curve and the outdoor temperature. Reads 0 while the heating is switched off.",
    ),
    "hs1_flow": Text(
        "Temperaturen på vattnet ut till värmesystem 1.",
        "The temperature of the water out to heating system 1.",
    ),
    "return_temp": Text(
        "Temperaturen på vattnet som kommer tillbaka från värmesystemet.",
        "The temperature of the water coming back from the heating system.",
    ),
    "dhw_circulation": Text(
        "Varmvattencirkulation (VVC) enligt CTC:s registerlista. Innehållet är inte bekräftat på "
        "en verklig anläggning.",
        "Hot water circulation according to CTC's register list. What it holds is not confirmed "
        "on a real installation.",
    ),
    "hp1_status": Text(
        "Värmepumpens eget läge: redo för start, startfördröjd, till värme, till varmvatten, "
        "avfrostning, blockerad, larm eller kommunikationsfel mellan styrenhet och värmepump.",
        "The heat pump's own state, in the controller's Swedish words: ready to start, start "
        "delay, heating, hot water, defrost, blocked, alarm or a communication error between the "
        "controller and the heat pump.",
    ),
    "hp1_in": Text(
        "Temperaturen på vattnet in till värmepumpen.",
        "The temperature of the water into the heat pump.",
    ),
    "hp1_out": Text(
        "Temperaturen på vattnet ut från värmepumpen. Skillnaden mot Värmepump in är hur mycket "
        "värmepumpen värmer vattnet.",
        "The temperature of the water out of the heat pump. The difference from Heat pump in is "
        "how much the heat pump warms the water.",
    ),
    "hp1_discharge": Text(
        "Hetgas: köldmediets temperatur när det lämnar kompressorn.",
        "Hot gas: the refrigerant's temperature as it leaves the compressor.",
    ),
    "hp1_suction": Text(
        "Suggas: köldmediets temperatur när det går in i kompressorn.",
        "Suction gas: the refrigerant's temperature as it enters the compressor.",
    ),
    "hp1_high_pressure": Text(
        "Trycket på köldkretsens varma sida, där värmen lämnas till vattnet. Står kompressorn "
        "stilla jämnar trycken ut sig.",
        "The pressure on the warm side of the refrigerant circuit, where the heat is handed to "
        "the water. While the compressor stands still the pressures even out.",
    ),
    "hp1_low_pressure": Text(
        "Trycket på köldkretsens kalla sida, där värmen hämtas in. Står kompressorn stilla jämnar "
        "trycken ut sig.",
        "The pressure on the cold side of the refrigerant circuit, where the heat is taken in. "
        "While the compressor stands still the pressures even out.",
    ),
    "hp1_brine_in": Text(
        "Köldbärarens temperatur in till värmepumpen, för berg-, jord- och sjövärme. En "
        "luft/vattenpump har ingen köldbärare, och på en EcoAir 720M följer talet utetemperaturen.",
        "The brine's temperature into the heat pump, for rock, ground and lake source heat. An "
        "air to water heat pump has no brine, and on an EcoAir 720M the figure follows the "
        "outdoor temperature.",
    ),
    "hp1_brine_out": Text(
        "Köldbärarens temperatur ut från värmepumpen, för berg-, jord- och sjövärme. En "
        "luft/vattenpump har ingen köldbärare, och på en EcoAir 720M följer talet utetemperaturen.",
        "The brine's temperature out of the heat pump, for rock, ground and lake source heat. An "
        "air to water heat pump has no brine, and on an EcoAir 720M the figure follows the "
        "outdoor temperature.",
    ),
    "hp1_charge_pump": Text(
        "Laddpumpens flöde i procent. Laddpumpen flyttar vattnet mellan värmepumpen och tanken.",
        "The charge pump's flow in per cent. The charge pump moves the water between the heat "
        "pump and the tank.",
    ),
    "hp1_brine_pump": Text(
        "Brinepumpens hastighet i procent, för berg-, jord- och sjövärme.",
        "The brine pump's speed in per cent, for rock, ground and lake source heat.",
    ),
    "hp1_fan": Text(
        "Fläktens hastighet i procent, för luft/vattenpumpar.",
        "The fan's speed in per cent, for air to water heat pumps.",
    ),
    "hp1_defrost_timer": Text(
        "Tid kvar innan värmepumpen tillåts avfrosta. Avfrostningen startar först när förångaren "
        "dessutom är kall nog. Enheten är obekräftad: registerlistan anger ingen och panelen "
        "skriver talet utan enhet, så det kan vara minuter likaväl som något annat.",
        "Time left before the heat pump is allowed to defrost. The defrost starts only once the "
        "evaporator is cold enough as well. The unit is not confirmed: the register list gives "
        "none and the panel writes the figure without one, so it may be minutes as well as "
        "something else.",
    ),
    "hp1_outdoor_temp": Text(
        "Utetemperaturen mätt vid värmepumpen.",
        "The outdoor temperature measured at the heat pump.",
    ),
    "degree_minutes": Text(
        "Värmeunderskottet, räknat som skillnaden mellan framledningens börvärde och verkliga "
        "temperatur summerad över tiden. Ju mer negativt, desto mer värme saknas, och kompressorn "
        "startar när underskottet når sin startgräns.",
        "The heat deficit, counted as the difference between the flow setpoint and the actual "
        "flow temperature summed over time. The more negative, the more heat is missing, and the "
        "compressor starts when the deficit reaches its start limit.",
    ),
    "immersion_upper_kw": Text(
        "Effekten den övre elpatronen ger just nu.",
        "The power the upper immersion heater gives right now.",
    ),
    "immersion_lower_kw": Text(
        "Effekten den nedre elpatronen ger just nu.",
        "The power the lower immersion heater gives right now.",
    ),
    "current_l1": Text(
        "Husets strömuttag på fas L1, mätt med strömkännare på inkommande ledningar. Blir strömmen "
        "högre än huvudsäkringen kopplar pumpen ner elpatronen för att skydda säkringarna.",
        "The house's current draw on phase L1, measured by current sensors on the incoming "
        "lines. When the current goes above the main fuse the heat pump steps the immersion "
        "heater down to protect the fuses.",
    ),
    "current_l2": Text(
        "Husets strömuttag på fas L2, mätt med strömkännare på inkommande ledningar. Blir strömmen "
        "högre än huvudsäkringen kopplar pumpen ner elpatronen för att skydda säkringarna.",
        "The house's current draw on phase L2, measured by current sensors on the incoming "
        "lines. When the current goes above the main fuse the heat pump steps the immersion "
        "heater down to protect the fuses.",
    ),
    "current_l3": Text(
        "Husets strömuttag på fas L3, mätt med strömkännare på inkommande ledningar. Blir strömmen "
        "högre än huvudsäkringen kopplar pumpen ner elpatronen för att skydda säkringarna.",
        "The house's current draw on phase L3, measured by current sensors on the incoming "
        "lines. When the current goes above the main fuse the heat pump steps the immersion "
        "heater down to protect the fuses.",
    ),
    "immersion_kwh": Text(
        "Energin elpatronen har använt sedan start. Samma räknare som Energi el total i displayens historik.",
        "The energy the immersion heater has used since it was started. The same counter as "
        "Energi el total in the display's history.",
    ),
    "hp1_rps": Text(
        "Kompressorns varvtal i varv per sekund.",
        "The compressor's speed in revolutions per second.",
    ),
    "room_temp_1": Text(
        "Rumstemperaturen från rumsgivaren för värmesystem 1.",
        "The room temperature from the room sensor of heating system 1.",
    ),
    "room_temp_2": Text(
        "Rumstemperaturen från rumsgivaren för värmesystem 2.",
        "The room temperature from the room sensor of heating system 2.",
    ),
    "compressor_hours": Text(
        "Kompressorns sammanlagda drifttid i timmar.",
        "The compressor's total running time in hours.",
    ),
    "compressor_hours_24h": Text(
        "Hur många minuter kompressorn gick under det senaste dygnet.",
        "How many minutes the compressor ran over the last day.",
    ),
    "hs1_status": Text(
        "Värmesystem 1: Till, Nattsänkning, Semester eller Värme av.",
        "Heating system 1: Till (on), Nattsänkning (night setback), Semester (holiday) or Värme "
        "av (heating off).",
    ),
    "tank_lower_setpoint": Text(
        "Börvärdet för tankens nedre del.",
        "The setpoint for the lower part of the tank.",
    ),
    "dhw_lower_temp": Text(
        "Temperaturen i tankens nedre del.",
        "The temperature in the lower part of the tank.",
    ),
    "dhw_temp": Text(
        "Varmvattnets temperatur.",
        "The hot water's temperature.",
    ),
    "dhw_capacity": Text(
        "Varmvattenkapacitet i procent enligt CTC:s registerlista, samma tal som panelen visar "
        "som Kapacitet under Varmvatten. Vad procenten räknas mot säger registerlistan inte.",
        "Hot water capacity in per cent according to CTC's register list, the same figure the "
        "panel shows as Kapacitet under Varmvatten. The register list does not say what the per "
        "cent is counted against.",
    ),
    "sg_mode": Text(
        "Vilket SmartGrid-läge som gäller. Normal: ingen påverkan. Blockering: det som valts i "
        "pumpens meny spärras. Lågpris och Överkapacitet: temperaturerna höjs med de grader som "
        "ställts in där.",
        "Which SmartGrid mode applies. Normal: no effect. Blockering (blocking): what is chosen "
        "in the heat pump's menu is blocked. Lågpris (low price) and Överkapacitet (overcapacity): "
        "the temperatures are raised by the degrees set there.",
    ),
    "control_sw": Text(
        "Styrenhetens programversion så som CTC rapporterar den. Numret går bara att jämföra mellan enheter av samma modell.",
        "The controller's software version as CTC reports it. The number can only be compared "
        "between units of the same model.",
    ),
    "control_sw_year": Text(
        "Året för styrenhetens programversion.",
        "The year of the controller's software version.",
    ),
    "hp1_power": Text(
        "Dokumenterad som tillförd effekt per värmepump, men visar 65,5 kW på en i550 Pro med "
        "stillastående kompressor, så talet är inte bekräftat på en pump i drift. Displayens "
        "rad Tillförd effekt visar samma storhet ur pumpens egen beräkning.",
        "Documented as supplied power per heat pump, but shows 65.5 kW on an i550 Pro with the "
        "compressor standing still, so the figure is not confirmed on a heat pump in operation. "
        "The display's row Tillförd effekt shows the same quantity from the heat pump's own "
        "calculation.",
    ),
    "compressor_kwh": Text(
        "El som kompressorn har förbrukat sedan start. Samma räknare som Tillförd energi totalt i "
        "displayens historik, och nämnaren i värmefaktorn där displayen saknar den.",
        "Electricity the compressor has used since it was started. The same counter as Tillförd "
        "energi totalt in the display's history, and the denominator of the coefficient of "
        "performance where the display lacks that counter.",
    ),
    # The stored settings, 61500 block. Read only: CTC warns that the number of
    # writes this memory tolerates is limited.
    "set_dhw_mode": Text(
        "Varmvattenprogrammet som är inställt i pumpen: Ekonomi för litet varmvattenbehov, Normal, eller Komfort för stort.",
        "The hot water program set in the heat pump: Ekonomi (economy) for a small need of hot "
        "water, Normal, or Komfort (comfort) for a large one.",
    ),
    # R50: what the one test on an i255 showed, 2026-10-13. 1006 held at one
    # hour for seven minutes, every write reaching the unit, and 61503 at 0.
    "set_extra_dhw": Text(
        "Hur lång tid Extra varmvatten har kvar, enligt CTC:s registerlista. Registret följde "
        "inte en start från Home Assistant när det provades på en i255: det stod på 0 i sju "
        "minuter medan Styr extra varmvatten hölls på en timme. Om det följer en start på "
        "pumpens panel är inte provat, och om pumpen startar om sin timer vid varje omskrivning "
        "från Home Assistant är inte känt.",
        "How long Extra hot water has left, according to CTC's register list. The register did "
        "not follow a start from Home Assistant when it was tried on an i255: it stayed at 0 for "
        "seven minutes while Control extra hot water was held at one hour. Whether it follows a "
        "start made at the heat pump's panel has not been tried, and whether the heat pump "
        "restarts its timer at each rewrite from Home Assistant is not known.",
    ),
    "set_room_1": Text(
        "Rumstemperaturen som är inställd för värmesystem 1 i pumpens meny.",
        "The room temperature set for heating system 1 in the heat pump's menu.",
    ),
    "set_slope_1": Text(
        "Värmekurvans lutning: hur mycket varmare vattnet till huset blir när det blir kallare ute. "
        "Är det för kallt inne när det är under noll ute, ökas lutningen ett par grader.",
        "The heating curve's slope: how much warmer the water to the house gets as it gets colder "
        "outside. If it is too cold indoors while it is below zero outside, raise the slope a "
        "couple of degrees.",
    ),
    "set_adjust_1": Text(
        "Värmekurvans justering, som flyttar hela kurvan uppåt eller nedåt. Är det för kallt inne "
        "när det är över noll ute, ökas justeringen ett par grader.",
        "The heating curve's adjustment, which moves the whole curve up or down. If it is too "
        "cold indoors while it is above zero outside, raise the adjustment a couple of degrees.",
    ),
    "set_hp1_blocked": Text(
        "Om kompressorn är tillåten eller spärrad i pumpens meny. Ja betyder att värmepumpen "
        "får gå, Nej att den är spärrad. Registret heter Värmepump tillåten i CTC:s "
        "registerlista (61521) och svarar 1 för Ja och 0 för Nej.",
        "Whether the compressor is allowed or blocked in the heat pump's menu. Ja (yes) means "
        "that the heat pump may run, Nej (no) that it is blocked. The register is called "
        "Värmepump tillåten in CTC's register list (61521) and answers 1 for Ja and 0 for Nej.",
    ),
    "set_heating_mode_1": Text(
        "Värmeläget för värmesystem 1. Auto: värmen stängs av och slås på efter utetemperaturen. "
        "Till och Från: alltid på eller alltid av.",
        "The heating mode of heating system 1. Auto: the heating is switched off and on by the "
        "outdoor temperature. Till (on) and Från (off): always on or always off.",
    ),
    "set_max_rps_1": Text(
        "Högsta kompressorvarvtal som är inställt i pumpen.",
        "The highest compressor speed set in the heat pump.",
    ),
    "set_max_immersion_lower": Text(
        "Högsta effekt den nedre elpatronen får ge, enligt pumpens inställning.",
        "The most power the lower immersion heater may give, as the heat pump is set.",
    ),
    "set_max_immersion_upper": Text(
        "Högsta effekt den övre elpatronen får ge, enligt pumpens inställning.",
        "The most power the upper immersion heater may give, as the heat pump is set.",
    ),
}

_VOLATILE = Text(
    " Skrivs till ett flyktigt register som pumpen glömmer cirka fem minuter efter sista "
    "skrivningen, så Home Assistant skriver om det varje minut så länge styrningen gäller. "
    "Styrningen räknas som aktiv först när en skrivning har nått pumpen, attributen senast "
    "skriven och gäller till (last_written och valid_until) säger när det var och hur länge "
    "pumpen håller värdet, och når ingen skrivning fram på fem minuter släpps den.",
    " Written to a volatile register that the heat pump forgets about five minutes after the "
    "last write, so Home Assistant writes it again every minute for as long as the control "
    "holds. The control counts as active only once a write has reached the heat pump, the "
    "attributes last_written and valid_until say when that was and how long the heat pump "
    "holds the value, and if no write gets through for five minutes the control is released.",
)

#: What a control with a mirror among the stored settings carries beside its
#: own value (R49): the pump's, whether or not Home Assistant holds the register.
_PUMP_VALUE = Text(
    " Attributet pumpens värde (pump_value) visar pumpens egen inställning, även medan Home "
    "Assistant håller ett eget värde.",
    " The attribute pump_value shows the heat pump's own setting, also while Home Assistant "
    "holds a value of its own.",
)

#: The control registers, by key.
CONTROL: dict[str, Text] = {
    "ctl_room_setpoint_1": Text(
        "Önskad rumstemperatur för värmesystem 1. Utan styrning visas pumpens egen inställning.",
        "The room temperature wanted for heating system 1. With no override in force the heat "
        "pump's own setting is shown.",
    ) + _PUMP_VALUE + _VOLATILE,
    "ctl_zone_mode_1": Text(
        "Tvingar värmesystem 1 till Av, Värme, Kyla, Auto eller På. Släpp styrningen lämnar "
        "tillbaka till pumpen. Pumpens Inställt värmeläge (61542) har andra lägen och speglar "
        "inte det här registret, så listan visar Släpp styrningen när inget hålls och har inget "
        "pumpens värde.",
        "Forces heating system 1 to Av (off), Värme (heating), Kyla (cooling), Auto or På (on). "
        "Släpp styrningen hands it back to the heat pump. The heat pump's Heating mode setting "
        "(61542) has other modes and does not mirror this register, so the list shows Släpp "
        "styrningen while nothing is held and has no pump_value.",
    ) + _VOLATILE,
    "ctl_dhw_mode": Text(
        "Varmvattenprogram: Ekonomi för litet behov, Normal, Komfort för stort behov, eller Manuell. "
        "Utan styrning visas pumpens eget program, Inställt varmvattenläge (61500). Släpp "
        "styrningen lämnar tillbaka till det. Kan 61500 inte läsas, till exempel under ett "
        "Modbusvarv som inte gick fram, står läget som okänt tills nästa varv svarar.",
        "Hot water program: Ekonomi (economy) for a small need, Normal, Komfort (comfort) for a "
        "large need, or Manuell (manual). With no override in force the heat pump's own program "
        "is shown, Hot water mode setting (61500). Släpp styrningen hands it back to that. If "
        "61500 cannot be read, during a Modbus round that did not get through for instance, the "
        "mode stands as unknown until the next round answers.",
    ) + _PUMP_VALUE + _VOLATILE,
    "ctl_dhw_setpoint": Text(
        "Önskad varmvattentemperatur. Pumpen har inget register som visar sin egen inställning, "
        "därför står värdet som okänt tills Home Assistant har skrivit det. Stopptemperatur "
        "varmvatten (62001) är något annat, temperaturen där laddningen slutar, och visas därför "
        "inte som pumpens värde.",
        "The hot water temperature wanted. The heat pump has no register that shows its own "
        "setting, so the value stands as unknown until Home Assistant has written it. Hot water "
        "stop temperature (62001) is something else, the temperature at which the charging "
        "stops, and is therefore not shown as the heat pump's value.",
    ) + _VOLATILE,
    # R50: no pumpens värde, and no claim to show the time left. 61503 did not
    # follow a start from here in the one test on an i255. The opening sentence
    # gives the hours only as CTC's register list has them and says the
    # keepalive may make it last longer, since Home Assistant rewrites 1006
    # every minute until the control is released.
    "ctl_extra_dhw": Text(
        "Skriver Extra varmvatten (1006) med så här många timmar; enligt CTC:s registerlista "
        "startar det Extra varmvatten så länge. Eftersom Home Assistant skriver om värdet varje "
        "minut tills styrningen släpps kan det pågå längre, så tryck Släpp när det inte behövs. "
        "Reglaget visar bara det Home Assistant håller och står tomt utan styrning. Pumpens Extra varmvatten kvar (61503) följde "
        "nämligen inte en start härifrån när det provades på en i255: registret stod på 0 i sju "
        "minuter medan reglaget hölls på en timme och varje skrivning nådde pumpen. Någon "
        "märkbar ändring i pumpens drift syntes inte heller, men tanken var då redan över "
        "stopptemperaturen. Om pumpen startar om sin timer vid varje omskrivning eller räknar "
        "ner från den första är inte känt.",
        "Writes Extra hot water (1006) with this many hours; according to CTC's register list "
        "that starts Extra hot water for that long. Since Home Assistant writes the value again "
        "every minute until the control is released it may last longer, so press Release when "
        "it is no longer needed. The control shows only what Home Assistant holds and stands "
        "empty while nothing is held. The heat pump's Extra hot water left "
        "(61503) did not follow a start from here when it was tried on an i255: the register "
        "stayed at 0 for seven minutes while the control was held at one hour and every write "
        "reached the heat pump. No noticeable change in the heat pump's operation was seen "
        "either, but the tank was already above its stop temperature. Whether the heat pump "
        "restarts its timer at each rewrite or counts down from the first one is not known.",
    ) + _VOLATILE,
    "ctl_price_mode": Text(
        "Talar om vilken elpriskategori som gäller: låg, normal eller hög. Vad pumpen gör vid "
        "respektive pris ställs in i dess meny.",
        "Tells the heat pump which electricity price category applies: Låg (low), Normal or Hög "
        "(high). What the heat pump does at each price is set in its menu.",
    ) + _VOLATILE,
    "ctl_max_rps": Text(
        "Begränsar kompressorns högsta varvtal. Utan styrning visas pumpens egen inställning.",
        "Limits the compressor's top speed. With no override in force the heat pump's own "
        "setting is shown.",
    ) + _PUMP_VALUE + _VOLATILE,
    "ctl_immersion_lower": Text(
        "Begränsar hur mycket effekt den nedre elpatronen får ge. Utan styrning visas pumpens egen inställning.",
        "Limits how much power the lower immersion heater may give. With no override in force "
        "the heat pump's own setting is shown.",
    ) + _PUMP_VALUE + _VOLATILE,
    "ctl_immersion_upper": Text(
        "Begränsar hur mycket effekt den övre elpatronen får ge. Utan styrning visas pumpens egen inställning.",
        "Limits how much power the upper immersion heater may give. With no override in force "
        "the heat pump's own setting is shown.",
    ) + _PUMP_VALUE + _VOLATILE,
    "release_control": Text(
        "Släpper all styrning från Home Assistant på en gång. Inget skrivs: Home Assistant slutar "
        "skriva, och pumpen går tillbaka till sina egna inställningar inom cirka fem minuter, "
        "eftersom den glömmer en styrning som inte skrivs om. Ett enskilt läge släpps med valet "
        "Släpp styrningen i sin lista. Ett tal har ingen sådan plats i en lista. Medan det styrs "
        "har dess rad en egen knapp Släpp, och det kan också släppas ensamt med åtgärden "
        "ctc_ecozenith.release. Ingen av dem skriver något, och resten av styrningen står kvar.",
        "Releases all control from Home Assistant at once. Nothing is written: Home Assistant "
        "stops writing, and the heat pump goes back to its own settings within about five "
        "minutes, since it forgets a control that is not written again. A single mode is "
        "released with the option Släpp styrningen in its list. A number has no such place in a "
        "list. While it is held its row has a button of its own, Release, and it can also be "
        "released alone with the action ctc_ecozenith.release. None of them writes anything, and "
        "the rest of the control stays as it is.",
    ),
}

#: Worked out by the integration rather than read.
DERIVED: dict[str, Text] = {
    "compressor_running": Text(
        "Till när värmepumpens status är till värme, till kyla eller till varmvatten.",
        "On while the heat pump's status is heating, cooling or hot water.",
    ),
    "defrosting": Text(
        "Till medan värmepumpen avfrostar. Gäller luft/vattenpumpar.",
        "On while the heat pump defrosts. Applies to air to water heat pumps.",
    ),
    "alarm": Text(
        "Till när värmepumpens status är av på grund av larm. Larm i resten av anläggningen "
        "syns i pumpens meny och, där displayen hämtas, i Senaste larm. Attributet episoder "
        "(episodes) bär de tio senaste larm displayen har visat, med kod, start, slut och "
        "utetemperatur när larmet började.",
        "On while the heat pump's status is off because of an alarm. Alarms in the rest of the "
        "installation show in the heat pump's menu and, where the display is harvested, in Last "
        "alarm. The attribute episodes carries the ten latest alarms the display has shown, with "
        "code, start, end and the outdoor temperature when the alarm began.",
    ),
    "display_alarm": Text(
        "Till när displayen visar ett larm. Larmet läses ur displayens egna larmtexter med "
        "E-kod, på de sidor som hämtas, och inte ur ett register i värmepumpen. Det räknas som "
        "visat när en hämtning har sett det på panelen inom den senaste tidsgränsen, som är 15 "
        "minuter eller ett hämtningsintervall plus fem minuter om det är längre. Då är sensorn "
        "till även när binärsensorn Larm är av. Bekräftad säger om larmet står så, och senast "
        "bekräftad när panelen senast visade det. Obekräftat betyder att ingen hämtning inom "
        "tidsgränsen har sett larmet på panelen, till exempel för att displayen inte gick att läsa "
        "eller för att den senaste hämtningen inte läste alla sidor som kan visa ett larm. Loggen "
        "kan då ha ett öppet larm som ingen längre har sett, och då är sensorn av. Sensorn är "
        "också till när värmepumpens egen larmstatus är till, så att ett larm inte missas bara "
        "för att displayen inte visar det.",
        "On while the display shows an alarm. The alarm is read from the display's own alarm "
        "texts, with the E-code, on the pages that are harvested, and not from a register in the "
        "heat pump. An alarm counts as shown when a harvest has seen it on the panel within the "
        "last window, which is 15 minutes or one harvest interval plus five minutes if that is "
        "longer, and then the sensor is on even while the Alarm sensor is off. The attribute "
        "confirmed says whether the alarm is confirmed, and last_confirmed when the panel last "
        "showed it. Not confirmed means no harvest within the window has seen the alarm on the "
        "panel, for instance because the display could not be read, or because the latest harvest "
        "did not read every page that can show an alarm. The log may then hold an open alarm that "
        "nobody has seen lately, and the sensor is off. The sensor is also on while the heat "
        "pump's own alarm status is on, so an alarm is not missed just because the display does "
        "not show it.",
    ),
    "last_alarm": Text(
        "Det larm displayen visade senast, med E-kod och text så som panelen skriver dem, "
        "till exempel [E017] Givare solpaneler ut. Läses ur rubrikikonen längst upp till "
        "vänster på de displaysidor som hämtas, och ur statusfältet, utan att panelen flyttas "
        "mer än hämtningen redan gör. Attributen säger när larmet började, när det försvann "
        "och vad utetemperaturen var när det började; pågår säger om det står kvar. Uppdateras "
        "bara när displayens sidor hämtas, så ett larm syns med upp till ett hämtningsintervalls "
        "fördröjning. Var panelen skriver larmet är sett på en i550 Pro och bekräftas vid nästa larm.",
        "The alarm the display showed last, with its E-code and text as the panel writes them, "
        "for instance [E017] Givare solpaneler ut. Read from the header icon at the top left of "
        "the display pages that are harvested, and from the status field, without the panel "
        "being moved any more than the harvest moves it already. The attributes say when the "
        "alarm began, when it went away and what the outdoor temperature was when it began; "
        "pågår (ongoing) says whether it is still there. Updated only when the display's pages are "
        "harvested, so an alarm shows up to one harvest interval late. Where the panel writes "
        "the alarm has been seen on an i550 Pro and is to be confirmed at the next alarm.",
    ),
    "blocked": Text(
        "Till när värmepumpens status är av och blockerad.",
        "On while the heat pump's status is off and blocked.",
    ),
    "immersion_active": Text(
        "Till när någon av elpatronerna ger effekt, den övre som i EcoZenith-tankarna sitter i "
        "varmvattendelen eller den nedre. Båda effekterna står som attribut.",
        "On while either immersion heater gives power, the upper one, which in the EcoZenith "
        "tanks sits in the hot water part, or the lower one. Both powers stand as attributes.",
    ),
    "smartgrid_active": Text(
        "Till när SmartGrid-läget är något annat än Normal.",
        "On while the SmartGrid mode is anything other than Normal.",
    ),
    "active_overrides": Text(
        "Hur många av pumpens flyktiga styrregister Home Assistant håller just nu, alltså skriver "
        "om varje minut. Noll när pumpen följer sina egna inställningar. Attributet styrningar "
        "(overrides) säger för varje styrning namn, register, värde och när värdet sattes, satt "
        "(set_at): när det först skrevs eller senast ändrades. Omskrivningen varje minut flyttar "
        "inte klockslaget, så listan ändras bara när en styrning sätts, ändras eller släpps. När "
        "en skrivning senast nådde pumpen står på reglaget självt. Räknas bara från en skrivning "
        "som har nått pumpen, och når ingen skrivning fram på fem minuter släpps styrningen, "
        "eftersom pumpen har glömt den. Släpp all styrning släpper allt på en gång.",
        "How many of the heat pump's volatile control registers Home Assistant holds right now, "
        "that is writes again every minute. Zero while the heat pump follows its own settings. "
        "The attribute overrides gives for each control its name, register, value and when the "
        "value was set, set_at: when it was first written or last changed. The rewrite every "
        "minute does not move that time, so the list changes only when a control is set, changed "
        "or released. When a write last reached the heat pump is on the control itself. Counted "
        "only from a write that has reached the heat pump, and if no write gets through for five "
        "minutes the control is released, since the heat pump has forgotten it. Release all "
        "control releases everything at once.",
    ),
    # The transitions in the heat pump's status, from one Modbus round to the
    # next (transitions.py). Nothing is stored, and the texts say so.
    "last_start": Text(
        "När kompressorn senast startade, ur övergångarna i Värmepump status: en start är när "
        "statusen går från stilla till värme, kyla, varmvatten eller avfrostning. Systemstatus "
        "och utetemperatur vid starten står som attribut. Känd först från en start som skett "
        "medan Home Assistant var igång.",
        "When the compressor last started, from the transitions in Heat pump status: a start is "
        "when the status goes from standing still to heating, cooling, hot water or defrost. The "
        "system status and the outdoor temperature at the start stand as attributes. Known only "
        "from a start that happened while Home Assistant was running.",
    ),
    "starts_today": Text(
        "Antal kompressorstarter sedan midnatt, räknade ur övergångarna i Värmepump status "
        f"var {_POLL_SECONDS} som förval. En avfrostning mitt i en körning räknas inte som ett "
        f"stopp och en ny start. {_COUNT_RESETS} Många korta körningar är pendling som sliter "
        "på kompressorn.",
        "The number of compressor starts since midnight, counted from the transitions in Heat "
        f"pump status every {_POLL_SECONDS_EN} by default. A defrost in the middle of a run does "
        f"not count as a stop and a new start. {_COUNT_RESETS.en} Many short runs are cycling, "
        "which wears the compressor.",
    ),
    "last_run": Text(
        "Hur många minuter kompressorns senaste avslutade körning varade, från start till stopp, "
        "med avfrostningar inräknade i körningen. Start och stopp står som attribut. Tom tills "
        "en hel körning har setts medan Home Assistant var igång.",
        "How many minutes the compressor's last finished run lasted, from start to stop, with "
        "defrosts counted as part of the run. Start and stop stand as attributes. Empty until a "
        "whole run has been seen while Home Assistant was running.",
    ),
    "mean_run_24h": Text(
        "Kompressorns drifttid det senaste dygnet (Modbus 62234) delat med displayens Antal "
        "starter /24 h: hur länge en körning varar i snitt. Korta körningar betyder pendling. "
        "Displayens räknare hämtas bara på det långsamma intervallet, så talet kan släpa något; "
        "båda talen står som attribut.",
        "The compressor's running time over the last day (Modbus 62234) divided by the display's "
        "Antal starter /24 h: how long a run lasts on average. Short runs mean cycling. The "
        "display's counter is harvested only on the slow interval, so the figure can lag "
        "somewhat; both figures stand as attributes.",
    ),
    "defrosts_today": Text(
        "Antal avfrostningar sedan midnatt, räknade ur övergångarna i Värmepump status. Gäller "
        "luft/vattenpumpar. Många avfrostningar en kall och fuktig dag är normalt; många en torr "
        f"dag kan betyda en igenisad förångare. {_COUNT_RESETS}",
        "The number of defrosts since midnight, counted from the transitions in Heat pump status. "
        "Applies to air to water heat pumps. Many defrosts on a cold and damp day are normal; "
        f"many on a dry day can mean an iced up evaporator. {_COUNT_RESETS.en}",
    ),
    "last_defrost": Text(
        "När värmepumpen senast började avfrosta. Längden i minuter, utetemperaturen vid starten "
        "och när den avslutades står som attribut; längden saknas medan avfrostningen pågår. En "
        "avfrostning räknas som en del av kompressorns körning, inte som ett stopp.",
        "When the heat pump last began to defrost. The length in minutes, the outdoor temperature "
        "at the start and when it ended stand as attributes; the length is missing while the "
        "defrost goes on. A defrost counts as part of the compressor's run, not as a stop.",
    ),
    "events": Text(
        "Pumpens övergångar som händelser i loggboken och som utlösare för automationer: "
        "kompressorstart och kompressorstopp, avfrostning start och slut, larm och larm borta, "
        "ändrat SmartGrid-läge och ändrad systemstatus. Attributen bär koden före och efter, "
        "deras etiketter, utetemperaturen och, för ett stopp eller ett avfrostningsslut, längden "
        "i minuter. Första avläsningen efter en start av Home Assistant ger inga händelser. "
        "Entiteten står kvar när Modbus tappas: dess tillstånd är ögonblicket för den senaste "
        "händelsen, som en tappad lina inte gör osann, och Modbus-sensorerna själva visar att "
        "linan är borta. En automation ser den därför aldrig gå via otillgänglig och tillbaka.",
        "The heat pump's transitions as events in the logbook and as triggers for automations: "
        "compressor start and compressor stop, defrost start and end, alarm and alarm cleared, a "
        "changed SmartGrid mode and a changed system status. The attributes carry the code before "
        "and after, their labels, the outdoor temperature and, for a stop or the end of a defrost, "
        "the length in minutes. The first reading after a start of Home Assistant gives no "
        "events. The entity stays when Modbus is lost: its state is the moment of the last event, "
        "which a lost line does not make untrue, and the Modbus sensors themselves show that the "
        "line is down. An automation therefore never sees it pass through unavailable and back.",
    ),
    "cop_day": Text(
        "Värmefaktor senaste dygnet: avgiven värme delat med tillförd el, ur två avläsningar av "
        f"energiräknarna {_DAY_HOURS} isär. Visas när minst {_COP_FLOOR_DAY} kWh har förbrukats.",
        "Coefficient of performance over the last day: delivered heat divided by supplied "
        f"electricity, from two readings of the energy counters {_DAY_HOURS_EN} apart. Shown "
        f"once at least {_COP_FLOOR_DAY} kWh has been used.",
    ),
    "cop_week": Text(
        f"Värmefaktor senaste {WINDOWS['week'][0]} dygnen, ur energiräknarna mot integrationens egen "
        f"avläsning {_WEEK_DAYS} gammal. Visas när minst {_COP_FLOOR_DAY} kWh per dygn i underlaget "
        "har förbrukats, "
        f"alltså {_COP_FLOOR_WEEK} kWh på en vecka, och tidigast en vecka efter att integrationen "
        "sattes upp.",
        f"Coefficient of performance over the last {WINDOWS['week'][0]} days, from the energy "
        f"counters against the integration's own reading {_WEEK_DAYS_EN} old. Shown once at "
        f"least {_COP_FLOOR_DAY} kWh per day of the basis has been used, that is "
        f"{_COP_FLOOR_WEEK} kWh in a week, and a week after the integration was set up at the "
        "earliest.",
    ),
    "cop_month": Text(
        f"Värmefaktor senaste {WINDOWS['month'][0]} dygnen, ur energiräknarna mot integrationens egen "
        f"avläsning {_MONTH_DAYS} gammal. Visas när minst {_COP_FLOOR_DAY} kWh per dygn i underlaget "
        "har förbrukats, "
        f"alltså {_COP_FLOOR_MONTH} kWh på en månad, och tidigast en månad efter att integrationen "
        "sattes upp.",
        f"Coefficient of performance over the last {WINDOWS['month'][0]} days, from the energy "
        f"counters against the integration's own reading {_MONTH_DAYS_EN} old. Shown once at "
        f"least {_COP_FLOOR_DAY} kWh per day of the basis has been used, that is "
        f"{_COP_FLOOR_MONTH} kWh in a month, and a month after the integration was set up at the "
        "earliest.",
    ),
    "cop_year": Text(
        f"Värmefaktor för ett rullande år, ur energiräknarna mot en egen avläsning {_YEAR_DAYS} "
        "gammal. Visas först när integrationen har ett års egna avläsningar, och när minst "
        f"{_COP_FLOOR_LIFETIME} kWh har förbrukats under året.",
        "Coefficient of performance over a rolling year, from the energy counters against a "
        f"reading of its own {_YEAR_DAYS_EN} old. Shown only once the integration has a year of "
        f"readings of its own, and once at least {_COP_FLOOR_LIFETIME} kWh has been used over "
        "the year.",
    ),
    "cop_first_year": Text(
        "Värmefaktor för första året efter driftstarten. Driftstarten räknas ur hur länge pumpen "
        "har varit spänningssatt, och värdet sparas för gott när året har gått.",
        "Coefficient of performance over the first year after commissioning. Commissioning is "
        "worked out from how long the heat pump has been powered, and the figure is kept for good "
        "once the year has passed.",
    ),
    "cop_lifetime": Text(
        "Värmefaktor över hela livslängden: all avgiven värme delat med all tillförd el sedan "
        f"driftstarten. Visas när minst {_COP_FLOOR_LIFETIME} kWh har förbrukats.",
        "Coefficient of performance over the whole lifetime: all delivered heat divided by all "
        f"supplied electricity since commissioning. Shown once at least {_COP_FLOOR_LIFETIME} kWh "
        "has been used.",
    ),
    # R28: the immersion heater's share of the electricity, over the
    # coefficient of performance's spans but the first year.
    "aux_share_day": Text(
        f"{_SHARE_WHAT} Senaste dygnet, ur två avläsningar av räknarna {_DAY_HOURS} isär. "
        f"Visas när minst {_COP_FLOOR_DAY} kWh har förbrukats sammanlagt. {_SHARE_EDGES}",
        f"{_SHARE_WHAT.en} The last day, from two readings of the counters {_DAY_HOURS_EN} "
        f"apart. Shown once at least {_COP_FLOOR_DAY} kWh has been used in all. "
        f"{_SHARE_EDGES.en}",
    ),
    "aux_share_week": Text(
        f"{_SHARE_WHAT} Senaste {WINDOWS['week'][0]} dygnen, mot integrationens egen avläsning "
        f"{_WEEK_DAYS} gammal. Visas när minst {_COP_FLOOR_DAY} kWh per dygn i underlaget har "
        f"förbrukats sammanlagt, alltså {_COP_FLOOR_WEEK} kWh på en vecka. {_SHARE_SAMPLED} "
        f"{_SHARE_EDGES}",
        f"{_SHARE_WHAT.en} The last {WINDOWS['week'][0]} days, against the integration's own "
        f"reading {_WEEK_DAYS_EN} old. Shown once at least {_COP_FLOOR_DAY} kWh per day of the "
        f"span has been used in all, that is {_COP_FLOOR_WEEK} kWh over a week. "
        f"{_SHARE_SAMPLED.en} {_SHARE_EDGES.en}",
    ),
    "aux_share_month": Text(
        f"{_SHARE_WHAT} Senaste {WINDOWS['month'][0]} dygnen, mot integrationens egen avläsning "
        f"{_MONTH_DAYS} gammal. Visas när minst {_COP_FLOOR_DAY} kWh per dygn i underlaget har "
        f"förbrukats sammanlagt, alltså {_COP_FLOOR_MONTH} kWh på en månad. {_SHARE_SAMPLED} "
        f"{_SHARE_EDGES}",
        f"{_SHARE_WHAT.en} The last {WINDOWS['month'][0]} days, against the integration's own "
        f"reading {_MONTH_DAYS_EN} old. Shown once at least {_COP_FLOOR_DAY} kWh per day of the "
        f"span has been used in all, that is {_COP_FLOOR_MONTH} kWh over a month. "
        f"{_SHARE_SAMPLED.en} {_SHARE_EDGES.en}",
    ),
    "aux_share_year": Text(
        f"{_SHARE_WHAT} Ett rullande år, mot en egen avläsning {_YEAR_DAYS} gammal. Visas när "
        f"minst {_COP_FLOOR_LIFETIME} kWh har förbrukats sammanlagt under året. {_SHARE_SAMPLED} "
        f"{_SHARE_EDGES}",
        f"{_SHARE_WHAT.en} A rolling year, against a reading of its own {_YEAR_DAYS_EN} old. "
        f"Shown once at least {_COP_FLOOR_LIFETIME} kWh has been used in all over the year. "
        f"{_SHARE_SAMPLED.en} {_SHARE_EDGES.en}",
    ),
    "aux_share_lifetime": Text(
        f"{_SHARE_WHAT} Hela livslängden: räknarna som de står sedan driftstarten. Visas när "
        f"minst {_COP_FLOOR_LIFETIME} kWh har förbrukats sammanlagt. {_SHARE_EDGES}",
        f"{_SHARE_WHAT.en} The whole lifetime: the counters as they stand since commissioning. "
        f"Shown once at least {_COP_FLOOR_LIFETIME} kWh has been used in all. {_SHARE_EDGES.en}",
    ),
    # The estimate from the display's two powers (cop_estimate.py), best effort
    # and said so on every sensor. Beside the counters' figures, never in place of them.
    "cop_estimate_day": Text(
        "Uppskattad värmefaktor sedan midnatt. "
        f"{_ESTIMATE_WHAT} Talet gäller från midnatt och till nu. {_ESTIMATE_EDGES}",
        "Estimated coefficient of performance since midnight. "
        f"{_ESTIMATE_WHAT.en} The figure covers the time since midnight up to now. "
        f"{_ESTIMATE_EDGES.en}",
    ),
    "cop_estimate_lifetime": Text(
        "Uppskattad värmefaktor över hela livslängden, sedan integrationen började spara "
        f"effekterna. {_ESTIMATE_WHAT} Mellan två skördar räknas aldrig längre än "
        f"{_ESTIMATE_GAP}, så en lucka i mätningarna fylls inte. {_ESTIMATE_EDGES}",
        "Estimated coefficient of performance over the whole lifetime, since the integration began "
        f"to keep the powers. {_ESTIMATE_WHAT.en} Two harvests are never counted across more than "
        f"{_ESTIMATE_GAP_EN} apart, so a gap in the measurements is not filled. {_ESTIMATE_EDGES.en}",
    ),
    "cop_estimate_now": Text(
        "Uppskattad värmefaktor just nu, ur de två effekterna i den senaste skörden. "
        "Det är ett enda ögonblick och ingen integrerad tid, så talet är brusigt och säger "
        "lite om ett dygn. Attributet read_at säger när skörden lästes, och är den gammal är "
        f"talet också gammalt. {_ESTIMATE_WHAT} {_ESTIMATE_EDGES}",
        "Estimated coefficient of performance now, from the two powers of the latest harvest. "
        "It is a single moment and no integrated time, so the figure is noisy and says little "
        "about a day. The attribute read_at says when the harvest was read, and if that is old "
        f"the figure is old too. {_ESTIMATE_WHAT.en} {_ESTIMATE_EDGES.en}",
    ),
}

#: The reasons an estimate gives where it has no figure, in the words the attribute
#: skäl carries, with the English the attribute reason carries beside them. Kept out
#: of TABLES: those hold the ⓘ texts as full sentences, and these are the phrases an
#: attribute carries, so "orimligt värde" stays exactly that.
ESTIMATE_REASONS: dict[str, Text] = {
    NO_HEAT_ROW: Text(
        "raden Avgiven värme (kW) finns inte på de sidor som hämtas",
        "the row Avgiven värme (kW) is not on the pages that are read",
    ),
    NO_INVERTER_ROW: Text(
        "raden Inverter Motoreffekt (kW) finns inte på de sidor som hämtas",
        "the row Inverter Motoreffekt (kW) is not on the pages that are read",
    ),
    SILENT: Text(
        "värmepumpens sida ger inga värden, vilket en avstängd värmepump inte gör",
        "the heat pump's page gives no values, as a heat pump that is switched off does not",
    ),
    FIRST_READ: Text(
        "väntar på en andra läsning, summeringen börjar vid nästa skörd",
        "waiting for a second reading, the sums start at the next harvest",
    ),
    NO_DAY_READINGS: Text(
        "inga mätningar sedan midnatt ännu",
        "no readings since midnight yet",
    ),
    NO_HEAT: Text(
        "pumpen anger ingen avgiven värme",
        "the heat pump reports no heat output",
    ),
    NO_ELECTRICITY: Text(
        "ingen tillförd el i underlaget",
        "no electricity in the basis",
    ),
    IMPLAUSIBLE: Text(
        "orimligt värde",
        "implausible value",
    ),
}

#: The integration's own account of how it reads the display.
HARVEST: dict[str, Text] = {
    "display_harvest": Text(
        "När displayens sidor senast lästes. Integrationen bläddrar till de valda sidorna "
        f"var {_HARVEST_MINUTES} som förval, hoppar över varvet om någon står vid panelen, högst "
        f"{_SKIPS} i rad, och försöker om efter fem minuter när displayen inte svarar. Attributen "
        "säger hur många varv i rad "
        "som hoppats över eller misslyckats, när nästa försök görs, vilket skälet var och "
        "vilka sidor som lästes och missades senast.",
        "When the display's pages were last read. The integration walks to the chosen pages "
        f"every {_HARVEST_MINUTES_EN} by default, skips the round while somebody is at the "
        f"panel, at most {_SKIPS_EN} in a row, and tries again after five minutes when the "
        "display does not answer. The attributes say how many rounds in a row have been skipped "
        "or have failed, when the next try is made, what the reason was and which pages were "
        "read and missed last.",
    ),
}

#: What the display says about the unit itself.
IDENTITY: dict[str, Text] = {
    "hp_model": Text(
        "Värmepumpens modell, som displayen visar den.",
        "The heat pump's model, as the display shows it.",
    ),
    "made": Text(
        "Tillverkningsår och vecka, lästa ur serienumrets andra grupp om fyra siffror.",
        "The year and week of manufacture, read from the second group of four digits of the "
        "serial number.",
    ),
    "serial": Text(
        "Serienumret: tre grupper om fyra siffror för produkt, tillverkningsår och vecka, och löpnummer.",
        "The serial number: three groups of four digits, for the product, the year and week of "
        "manufacture, and a running number.",
    ),
    "display_fw": Text(
        "Displayens programversion, skriven som ett datum.",
        "The display's software version, written as a date.",
    ),
    "hp_fw": Text(
        "Programversionen i värmepumpens styrkort, skriven som ett datum.",
        "The software version in the heat pump's control board, written as a date.",
    ),
    "bootloader": Text(
        "Displayens bootloaderversion.",
        "The display's bootloader version.",
    ),
}

#: The integration's own settings, which act on the integration rather than on
#: the heat pump.
OWN: dict[str, Text] = {
    "debug_report": Text(
        "Skickar det som Hämta diagnostik innehåller till utvecklaren på stats.rnet.se var 30:e "
        "minut så länge den är påslagen, och en gång när den slås på: alla display- och "
        "Modbus-värden, hur läsningarna gick, och integrationens egna varningar och fel ur loggen. "
        "IP-adresser, MAC-adresser, serienumrets löpnummer och värdnamn tvättas bort innan något "
        "lämnar huset. Rapporterna sparas i 14 dagar och raderas på begäran. Av som förval och "
        "oberoende av den anonyma statistiken. Samma inställning som under Konfigurera, och att "
        "slå på eller av den laddar inte om integrationen.",
        "Sends what Download diagnostics holds to the developer at stats.rnet.se every 30 "
        "minutes while it is on, and once when it is switched on: every display and Modbus "
        "value, how the readings went, and the integration's own warnings and errors from its "
        "log. IP addresses, MAC addresses, the serial number's running number and host names are "
        "washed out before anything leaves the house. The reports are kept for 14 days and "
        "erased on request. Off by default and independent of the anonymous statistics. The same "
        "setting as under Configure, and switching it on or off does not reload the integration.",
    ),
}

_REGISTERS = {d.key: d.address for d in MODBUS_SENSORS + MODBUS_SETTINGS}
_CONTROL_REGISTERS = {r.key: r.address for r in CONTROL_NUMBERS + CONTROL_SELECTS}
_SETTINGS = {d.key for d in MODBUS_SETTINGS}

_IN_OUT = Text(
    "Nummer 1 är in, nummer 2 är ut.",
    "Number 1 is in, number 2 is out.",
)
_SETPOINT_IN_BRACKETS = Text(
    " Inom parentes på panelen står börvärdet.",
    " The setpoint stands in brackets on the panel.",
)
_HEAT_OUT_TOTAL = Text(
    "All värme värmepumpen har levererat sedan driftstarten. Täljaren i värmefaktorn.",
    "All the heat the heat pump has delivered since commissioning. The numerator of the "
    "coefficient of performance.",
)
_HEAT_OUT_NOW = Text(
    "Värmeeffekten värmepumpen lämnar just nu, enligt dess egen beräkning. Under en avfrostning "
    "väntas talet bli negativt: kretsen vänds och värme tas ur tanken för att tina förångaren.",
    "The heating power the heat pump gives right now, by its own calculation. During a defrost "
    "the figure is expected to go negative: the circuit is reversed and heat is taken from the "
    "tank to thaw the evaporator.",
)
_SHUNT_DELAY = Text(
    "Tid kvar innan tillsatsvärme får användas via shunten, så att elpatronen inte används i "
    "onödan vid ett tillfälligt temperaturfall.",
    "Time left before additional heat may be used through the mixing valve, so that the "
    "immersion heater is not used needlessly when the temperature drops for a moment.",
)
_EXTERNAL_METER = Text(
    ", om en sådan är ansluten.",
    ", if one is connected.",
)

#: Display rows by the start of their label, casefolded, in the panel's Swedish
#: and a few in English. Longest match wins, so "inverter motoreffekt" is not
#: taken for "inverter". Numbers after a label are stripped first.
_DISPLAY: tuple[tuple[str, Text], ...] = (
    ("vp in/ut", Text(
        "Vattnets temperatur in till och ut från värmepumpen. ",
        "The water's temperature into and out of the heat pump. ",
    ) + _IN_OUT),
    ("brine in/ut", Text(
        "Brinens temperatur in till och ut från värmepumpen. ",
        "The brine's temperature into and out of the heat pump. ",
    ) + _IN_OUT),
    ("utetemperatur", Text(
        "Utetemperaturen, som den här delen av anläggningen mäter den.",
        "The outdoor temperature, as this part of the installation measures it.",
    )),
    ("givare förångare", Text(
        "Temperaturgivare vid förångaren, där värmepumpen hämtar värmen.",
        "A temperature sensor at the evaporator, where the heat pump takes in the heat.",
    )),
    ("givare 2 förångare", Text(
        "Den andra temperaturgivaren vid förångaren, där värmepumpen hämtar värmen.",
        "The second temperature sensor at the evaporator, where the heat pump takes in the heat.",
    )),
    ("hetgas/suggas", Text(
        "Köldmediets temperaturer runt kompressorn: hetgas efter den och suggas före den. Numret anger vilket av radens tal på panelen.",
        "The refrigerant's temperatures around the compressor: hot gas after it and suction gas "
        "before it. The number says which of the row's figures on the panel it is.",
    )),
    ("ac choke", Text(
        "Temperaturen vid invertens drossel (AC choke).",
        "The temperature at the inverter's choke (AC choke).",
    )),
    ("inverter motoreffekt", Text(
        "Effekten invertern driver kompressormotorn med just nu.",
        "The power the inverter drives the compressor motor with right now.",
    )),
    ("inverter dc-bus", Text(
        "Likspänningen i invertens mellanled.",
        "The DC voltage in the inverter's DC link.",
    )),
    ("inverter matningsspänning", Text(
        "Spänningen som matar invertern.",
        "The voltage that feeds the inverter.",
    )),
    ("inverter motorspänning", Text(
        "Spänningen invertern lägger på kompressormotorn.",
        "The voltage the inverter puts on the compressor motor.",
    )),
    ("inverter motorström", Text(
        "Strömmen invertern driver kompressormotorn med.",
        "The current the inverter drives the compressor motor with.",
    )),
    ("inverter", Text(
        "Temperaturen i invertern som driver kompressorn.",
        "The temperature in the inverter that drives the compressor.",
    )),
    ("kompressortemp", Text(
        "Kompressorns temperatur.",
        "The compressor's temperature.",
    )),
    ("kompressorvärmare", Text(
        "Om kompressorvärmaren är på.",
        "Whether the compressor heater is on.",
    )),
    ("kompressor", Text(
        "Om kompressorn går. R efter varvtalet betyder reducerat läge, till exempel tyst läge.",
        "Whether the compressor is running. An R after the speed means a reduced mode, a quiet "
        "mode for instance.",
    )),
    ("vätskerör", Text(
        "Temperaturen i vätskeröret, där köldmediet lämnar kondensorn i flytande form.",
        "The temperature in the liquid line, where the refrigerant leaves the condenser as a "
        "liquid.",
    )),
    ("expansionsventil", Text(
        "Hur mycket den elektroniska expansionsventilen är öppen. Den styr flödet av köldmedium till förångaren.",
        "How far the electronic expansion valve is open. It controls the flow of refrigerant to "
        "the evaporator.",
    )),
    ("flöde", Text(
        "Flödet i laddkretsen mellan värmepumpen och tanken.",
        "The flow in the charging circuit between the heat pump and the tank.",
    )),
    ("förångning", Text(
        "Förångningens temperatur och tryck, köldkretsens kalla sida.",
        "The evaporation's temperature and pressure, the cold side of the refrigerant circuit.",
    )),
    ("kondensering", Text(
        "Kondenseringens temperatur och tryck, köldkretsens varma sida.",
        "The condensation's temperature and pressure, the warm side of the refrigerant circuit.",
    )),
    ("överhettning", Text(
        "Hur många grader varmare suggasen är än förångningen. Överhettningen ser till att bara gas når kompressorn.",
        "How many degrees warmer the suction gas is than the evaporation. The superheat makes "
        "sure that only gas reaches the compressor.",
    )),
    ("laddpump", Text(
        "Laddpumpens drift och flöde i procent.",
        "Whether the charge pump runs, and its flow in per cent.",
    )),
    ("brinepump", Text(
        "Brinepumpens drift och hastighet i procent.",
        "Whether the brine pump runs, and its speed in per cent.",
    )),
    ("fläkt", Text(
        "Fläktens drift och hastighet i procent.",
        "Whether the fan runs, and its speed in per cent.",
    )),
    ("timer avfrostning", Text(
        "Tid kvar innan värmepumpen tillåts avfrosta. Avfrostningen startar först när förångaren dessutom är kall nog. Enheten är obekräftad: panelen skriver talet utan enhet.",
        "Time left before the heat pump is allowed to defrost. The defrost starts only once the "
        "evaporator is cold enough as well. The unit is not confirmed: the panel writes the "
        "figure without one.",
    )),
    ("ström l1/l2/l3", Text(
        "Husets strömuttag per fas, mätt med strömkännare på inkommande ledningar.",
        "The house's current draw per phase, measured by current sensors on the incoming lines.",
    )),
    ("ström", Text(
        "Strömmen över kompressorn.",
        "The current through the compressor.",
    )),
    ("programversion vp-styrkort", Text(
        "Värmepumpens programversion, skriven som ett datum.",
        "The heat pump's software version, written as a date.",
    )),
    ("modell", Text(
        "Värmepumpens modell.",
        "The heat pump's model.",
    )),
    ("avgiven värme totalt", _HEAT_OUT_TOTAL),
    ("avgiven värme/30", Text(
        "Värme levererad de senaste 30 dagarna.",
        "Heat delivered over the last 30 days.",
    )),
    ("avgiven värme", _HEAT_OUT_NOW),
    ("avgiven energi/24", Text(
        "Värme levererad det senaste dygnet.",
        "Heat delivered over the last day.",
    )),
    ("avgiven energi", Text(
        "All värme värmepumpen har levererat sedan driftstarten, i den äldre displayprogramvarans räknare.",
        "All the heat the heat pump has delivered since commissioning, in the older display "
        "software's counter.",
    )),
    ("avgiven kyla totalt", Text(
        "All kyla värmepumpen har levererat sedan driftstarten.",
        "All the cooling the heat pump has delivered since commissioning.",
    )),
    ("avgiven kyla/30", Text(
        "Kyla levererad de senaste 30 dagarna.",
        "Cooling delivered over the last 30 days.",
    )),
    ("avgiven kyla", Text(
        "Kyleffekten värmepumpen lämnar just nu.",
        "The cooling power the heat pump gives right now.",
    )),
    ("tillförd effekt", Text(
        "Den elektriska effekt som går till värmepumpen just nu. Med avgiven värme ger den värmefaktorn för stunden.",
        "The electrical power going to the heat pump right now. With the delivered heat it gives "
        "the coefficient of performance of the moment.",
    )),
    ("tillförd energi totalt", Text(
        "All el värmepumpen har förbrukat sedan driftstarten. Nämnaren i värmefaktorn, och samma räknare som Kompressorenergi.",
        "All the electricity the heat pump has used since commissioning. The denominator of the "
        "coefficient of performance, and the same counter as Compressor energy.",
    )),
    ("tillförd energi/30", Text(
        "El som värmepumpen har förbrukat de senaste 30 dagarna.",
        "Electricity the heat pump has used over the last 30 days.",
    )),
    ("energi el total", Text(
        "Hur mycket spetsvärme elpatronen har använt sedan start.",
        "How much auxiliary heat the immersion heater has used since it was started.",
    )),
    ("energi el/30", Text(
        "Spetsvärme elpatronen har använt de senaste 30 dagarna.",
        "Auxiliary heat the immersion heater has used over the last 30 days.",
    )),
    ("emxxx va", Text(
        "Skenbar effekt från en extern energimätare",
        "Apparent power from an external energy meter",
    ) + _EXTERNAL_METER),
    ("emxxx vln", Text(
        "Spänning mellan fas och nolla från en extern energimätare",
        "The voltage between phase and neutral from an external energy meter",
    ) + _EXTERNAL_METER),
    ("emxxx w", Text(
        "Effekt från en extern energimätare",
        "Power from an external energy meter",
    ) + _EXTERNAL_METER),
    ("emxxx", Text(
        "Energi från en extern energimätare",
        "Energy from an external energy meter",
    ) + _EXTERNAL_METER),
    ("högsta framledning", Text(
        "Den högsta temperatur som har levererats till värmesystemet.",
        "The highest temperature that has been delivered to the heating system.",
    )),
    ("total drifttid", Text(
        "Hur länge produkten har varit spänningssatt, i timmar. Ur den räknas driftstarten för första årets värmefaktor.",
        "How long the product has been powered, in hours. Commissioning, for the first year's "
        "coefficient of performance, is worked out from it.",
    )),
    ("drifttid total", Text(
        "Kompressorns sammanlagda drifttid i timmar.",
        "The compressor's total running time in hours.",
    )),
    ("drift /24", Text(
        "Kompressorns drifttid under förra dygnet.",
        "The compressor's running time over the previous day.",
    )),
    ("antal starter /24", Text(
        "Antal kompressorstarter det senaste dygnet.",
        "The number of compressor starts over the last day.",
    )),
    ("antal starter", Text(
        "Antal kompressorstarter sedan start.",
        "The number of compressor starts since the unit was started.",
    )),
    ("kritiska larm", Text(
        "Displayens egen räknare över kritiska larm från värmepumpen sedan driftstarten. Den "
        "säger hur många, inte vilka: vilka larm det var står i panelens larmlista, och det "
        "som pågår visas i Senaste larm medan displayen hämtas.",
        "The display's own count of critical alarms from the heat pump since commissioning. It "
        "says how many, not which: which alarms they were is in the panel's alarm list, and the "
        "one going on shows in Last alarm while the display is harvested.",
    )),
    ("medeltemperatur ute", Text(
        "Medelvärdet av utetemperaturen de senaste 30 dagarna.",
        "The mean outdoor temperature over the last 30 days.",
    )),
    ("tank övre", Text(
        "Temperaturen i tankens övre del.",
        "The temperature in the upper part of the tank.",
    ) + _SETPOINT_IN_BRACKETS),
    ("tank nedre", Text(
        "Temperaturen i tankens nedre del.",
        "The temperature in the lower part of the tank.",
    ) + _SETPOINT_IN_BRACKETS),
    ("extern vv-tank", Text(
        "Temperaturen i en extern varmvattentank.",
        "The temperature in an external hot water tank.",
    )),
    ("vv-tank", Text(
        "Temperaturen i varmvattentanken.",
        "The temperature in the hot water tank.",
    )),
    ("ext. bufferttank", Text(
        "Temperaturen i den externa bufferttanken.",
        "The temperature in the external buffer tank.",
    )),
    ("laddstart", Text(
        "Temperaturen där laddningen av bufferttanken startar.",
        "The temperature at which the charging of the buffer tank starts.",
    )),
    ("tidsräknare kyltank", Text(
        "Tidsräknare för kyltanken.",
        "A timer for the cooling tank.",
    )),
    ("kyltank", Text(
        "Temperaturen i kyltanken.",
        "The temperature in the cooling tank.",
    )),
    ("elpatron", Text(
        "Effekten elpatronen ger just nu.",
        "The power the immersion heater gives right now.",
    )),
    ("eleffekt", Text(
        "Tillskottseffekten på elpatronerna, nedre och övre.",
        "The added power of the immersion heaters, lower and upper.",
    )),
    ("gradminutkyla", Text(
        "Gradminuter för kyla: underskottet mot kylbehovet summerat över tiden.",
        "Degree minutes for cooling: the deficit against the cooling need summed over time.",
    )),
    ("gradminut", Text(
        "Värmeunderskottet som gradminuter. Ju mer negativt, desto mer värme saknas, och kompressorn startar vid sin startgräns.",
        "The heat deficit as degree minutes. The more negative, the more heat is missing, and the "
        "compressor starts at its start limit.",
    )),
    ("fördröjning shuntventil", _SHUNT_DELAY),
    ("shuntfördröjning", _SHUNT_DELAY),
    ("fördröjning spets", Text(
        "Tid kvar innan spetsvärmen får starta.",
        "Time left before the auxiliary heat may start.",
    )),
    ("radiatorpump", Text(
        "Om radiatorpumpen går.",
        "Whether the radiator pump is running.",
    )),
    ("shuntventil", Text(
        "Om shuntventilen öppnar eller stänger värmen ut till värmesystemet.",
        "Whether the mixing valve is opening or closing the heat out to the heating system.",
    )),
    ("smartgrid", Text(
        "Vilket SmartGrid-läge som gäller för den här delen.",
        "Which SmartGrid mode applies to this part.",
    )),
    ("framledning", Text(
        "Temperaturen ut till värmesystemet.",
        "The temperature out to the heating system.",
    ) + _SETPOINT_IN_BRACKETS),
    ("returledning", Text(
        "Temperaturen tillbaka från värmesystemet.",
        "The temperature back from the heating system.",
    )),
    ("rumstemperatur", Text(
        "Rumstemperaturen från rumsgivaren.",
        "The room temperature from the room sensor.",
    ) + _SETPOINT_IN_BRACKETS),
    ("extra varmvatten", Text(
        "Om Extra varmvatten är aktivt.",
        "Whether Extra hot water is active.",
    )),
    ("elprisläge", Text(
        "Vilken elpriskategori som gäller just nu: hög, medium eller låg.",
        "Which electricity price category applies right now: high, medium or low.",
    )),
    ("elpris", Text(
        "Aktuellt elpris i lokal valuta.",
        "The current electricity price in the local currency.",
    )),
    ("status", Text(
        "Driftläget för den här delen av anläggningen, som panelen visar det.",
        "The operating state of this part of the installation, as the panel shows it.",
    )),
    ("läge", Text(
        "Vilket program som är aktivt.",
        "Which program is active.",
    )),
    ("energy output total", _HEAT_OUT_TOTAL),
    ("energy consumption total", Text(
        "All el värmepumpen har förbrukat sedan driftstarten. Nämnaren i värmefaktorn.",
        "All the electricity the heat pump has used since commissioning. The denominator of the "
        "coefficient of performance.",
    )),
    ("energy output", _HEAT_OUT_NOW),
    ("energy add", Text(
        "Den elektriska effekt som går till värmepumpen just nu.",
        "The electrical power going to the heat pump right now.",
    )),
    ("total operation time", Text(
        "Drifttid i timmar: hur länge produkten har varit spänningssatt, eller kompressorns drifttid.",
        "Running time in hours: how long the product has been powered, or the compressor's "
        "running time.",
    )),
)
_DISPLAY_SORTED = sorted(_DISPLAY, key=lambda item: len(item[0]), reverse=True)
_NUMBER_SUFFIX = re.compile(r"\s+\d+$")

_UNNAMED = Text(
    "Ett tal från displayens sida utan en egen rubrik på panelen, därför namngivet efter sidan. "
    "Vad det visar framgår av panelen.",
    "A figure from the display's page without a heading of its own on the panel, so it is "
    "named after the page. What it shows can be read on the panel.",
)
_GENERIC = Text(
    "Ett värde som displayen visar på den här sidan, under samma namn.",
    "A value the display shows on this page, under the same name.",
)


def display_explanation(
    label: str | None, page_title: str | None = None, lang: str | None = SWEDISH
) -> str:
    """The explanation for a display row, from the label the panel printed.

    The label is the panel's, in Swedish whatever Home Assistant speaks, and
    is matched as such; ``lang`` only picks the language of the text.
    """
    text = _NUMBER_SUFFIX.sub("", str(label or "").strip()).casefold()
    title = str(page_title or "").strip().casefold()
    if not text or text == title or text.startswith("värde"):
        return in_language(_UNNAMED, lang)
    for prefix, explanation in _DISPLAY_SORTED:
        if text.startswith(prefix):
            return in_language(explanation, lang)
    return in_language(_GENERIC, lang)


#: Every table of explanations by key, in the order explain looks through them.
TABLES: tuple[dict[str, Text], ...] = (MODBUS, CONTROL, DERIVED, IDENTITY, HARVEST, OWN)


def explain(key: str, lang: str | None = SWEDISH) -> str | None:
    """The explanation for anything but a display row, or None for an unknown key."""
    for table in TABLES:
        if key in table:
            return in_language(table[key], lang)
    return None


#: The sources that are the same words for every key that has them.
_DERIVED_FROM_STATUS = Text(
    "Räknas fram ur Värmepump status, Modbus-register 62017",
    "Worked out from Heat pump status, Modbus register 62017",
)
_DERIVED_FROM_TRANSITIONS = Text(
    "Räknas fram ur övergångarna i Värmepump status, Modbus-register 62017",
    "Worked out from the transitions in Heat pump status, Modbus register 62017",
)
_SOURCES: dict[str, Text] = {
    "release_control": Text("Knapp i Home Assistant", "A button in Home Assistant"),
    "debug_report": Text(
        "Integrationens inställning, samma som under Konfigurera",
        "The integration's setting, the same as under Configure",
    ),
    "active_overrides": Text(
        "Räknas fram ur det Home Assistant skriver till pumpens flyktiga styrregister",
        "Worked out from what Home Assistant writes to the heat pump's volatile control registers",
    ),
    "display_alarm": Text(
        "Larmtexterna på displayens hämtade sidor, och Modbus-register 62017 för värmepumpens larm",
        "The alarm texts on the display's harvested pages, and Modbus register 62017 for the heat "
        "pump's alarm",
    ),
    **dict.fromkeys(("compressor_running", "defrosting", "alarm", "blocked"), _DERIVED_FROM_STATUS),
    **dict.fromkeys(
        ("last_start", "starts_today", "last_run", "defrosts_today", "last_defrost"),
        _DERIVED_FROM_TRANSITIONS,
    ),
    "mean_run_24h": Text(
        "Räknas ur Modbus-register 62234 och displayens Antal starter /24 h",
        "Worked out from Modbus register 62234 and the display's Antal starter /24 h",
    ),
    "events": Text(
        "Räknas fram ur övergångarna i Värmepump status, Systemstatus och SmartGrid-läge, "
        "Modbus-register 62017, 62005 och 62301",
        "Worked out from the transitions in Heat pump status, System status and SmartGrid mode, "
        "Modbus registers 62017, 62005 and 62301",
    ),
    "immersion_active": Text(
        "Räknas fram ur Elpatron övre och Elpatron nedre, Modbus-register 62168 och 62169",
        "Worked out from Upper immersion heater and Lower immersion heater, Modbus registers "
        "62168 and 62169",
    ),
    "smartgrid_active": Text(
        "Räknas fram ur SmartGrid-läge, Modbus-register 62301",
        "Worked out from SmartGrid mode, Modbus register 62301",
    ),
    "last_alarm": Text(
        "Rubrikikonen och statusfältet på de displaysidor som hämtas",
        "The header icon and the status field on the display pages that are harvested",
    ),
    "made": Text("Räknas ur serienumret", "Worked out from the serial number"),
    # Before the cop_ fallback below, which would name the energy counters.
    "cop_estimate_day": Text(
        "Räknas fram ur displayens rader Avgiven värme (kW) och Inverter Motoreffekt (kW), "
        "integrerade sedan midnatt",
        "Worked out from the display's rows Avgiven värme (kW) and Inverter Motoreffekt (kW), "
        "integrated since midnight",
    ),
    "cop_estimate_lifetime": Text(
        "Räknas fram ur displayens rader Avgiven värme (kW) och Inverter Motoreffekt (kW), "
        "integrerade över hela tiden",
        "Worked out from the display's rows Avgiven värme (kW) and Inverter Motoreffekt (kW), "
        "integrated over the whole time",
    ),
    "cop_estimate_now": Text(
        "Räknas fram ur displayens rader Avgiven värme (kW) och Inverter Motoreffekt (kW), "
        "i den senaste skörden",
        "Worked out from the display's rows Avgiven värme (kW) and Inverter Motoreffekt (kW), "
        "in the latest harvest",
    ),
}
_COP_SOURCE = Text("Räknas ur energiräknarna", "Worked out from the energy counters")
_SHARE_SOURCE = Text(
    "Räknas ur elpatronens energi, Modbus-register 62191, och kompressorns energiräknare",
    "Worked out from the immersion heater's energy, Modbus register 62191, and the "
    "compressor's energy counter",
)
_IDENTITY_SOURCE = Text("Displayens systeminformation", "The display's system information")
_HARVEST_SOURCE = Text(
    "Integrationens egen bokföring av displayskörden",
    "The integration's own account of the display harvest",
)


def _register_source(key: str) -> Text | None:
    """Where a register's value comes from, with its address, in both languages."""
    if key in _CONTROL_REGISTERS:
        address = _CONTROL_REGISTERS[key]
        return Text(f"Modbus-register {address}, flyktigt", f"Modbus register {address}, volatile")
    if key not in _REGISTERS:
        return None
    address = _REGISTERS[key]
    if key in _SETTINGS:
        return Text(
            f"Modbus-register {address}, lagrad inställning som bara läses",
            f"Modbus register {address}, a stored setting that is only read",
        )
    return Text(f"Modbus-register {address}", f"Modbus register {address}")


def _page_source(
    key: str, pages: list[Mapping[str, Any]] | None, interval: int | None
) -> Text | None:
    """Where a display row comes from: its page, screen, place and how often it is read."""
    for page in pages or []:
        for value in page.get("values") or []:
            if value.get("key") != key:
                continue
            # R39: the screen and the place in the page's list, where the page
            # gives them, since one page can have several rows by one name.
            # The page's title is the panel's own, in Swedish in both texts.
            places = [
                (field, sv, en) for field, sv, en in (("screen", "skärm", "screen"), ("row", "rad", "row"))
                if value.get(field) is not None
            ]
            where_sv = "".join(f", {sv} {value[field]}" for field, sv, _en in places)
            where_en = "".join(f", {en} {value[field]}" for field, _sv, en in places)
            minutes = max(1, round(interval / 60)) if interval else None
            every_sv = f", hämtas var {minutes}:e minut" if minutes else ""
            every_en = f", read every {minutes} minutes" if minutes else ""
            title = page.get("title")
            return Text(
                f"Displaysidan {title}{where_sv}{every_sv}",
                f"Display page {title}{where_en}{every_en}",
            )
    return None


def source(
    key: str,
    pages: list[Mapping[str, Any]] | None = None,
    interval: int | None = None,
    lang: str | None = SWEDISH,
) -> str | None:
    """Where a value comes from, in a few words, in the page's language."""
    found: str | None = _register_source(key)
    if found is None:
        found = _SOURCES.get(key)
    if found is None and key.startswith("cop_"):
        found = _COP_SOURCE
    if found is None and key.startswith("aux_share_"):
        found = _SHARE_SOURCE
    if found is None and key in IDENTITY:
        found = _IDENTITY_SOURCE
    if found is None and key in HARVEST:
        found = _HARVEST_SOURCE
    if found is None:
        found = _page_source(key, pages, interval)
    return None if found is None else in_language(found, lang)
