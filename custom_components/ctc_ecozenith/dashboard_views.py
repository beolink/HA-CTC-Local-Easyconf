"""Layout of the CTC page, built from a plain description of the heat pumps.

Free of Home Assistant imports on purpose, like stats_extra.py, so the layout can be
checked without an installation (tests/test_dashboard.py). dashboard.py looks the
entities up every time the page is opened and hands this module one dict per pump:

    {"name": str,                       # the device name as the user sees it
     "entities": {key: entity_id},      # enabled and visible entities only
     "names": {key: str},               # the registry name, or the original one
     "pages": [{"title": str,
                "values": [{"key": str, "label": str, "unit": str | None}]}],
     "energy_out": key | None,          # the display's lifetime counters, as found
     "energy_in": key | None,           # by cop.find_energy_totals
     "unused": [key],                   # readings this installation only ever gave as 0
     "control_enabled": bool,
     "display_interval": int | None,    # seconds between display harvests
     "live_page": int | None,           # the page the panel rests on, read without a press
     "live_interval": int | None,       # seconds between two reads of it
     "language": "sv" | "en"}           # Home Assistant's, which the page is built in

A key is what follows "<domain>_<host>_" in the entity's unique_id: the Modbus keys in
const.py, the derived binary sensors, the control registers, the COP spans, the
identity rows and the display values ("p22_avgiven_varme"). A key that is missing
simply leaves its card out, so an i550 Pro without control gets no controls and a
unit without a history page gets no coefficient of performance.

Each pump gets four tabs, in the order the questions come:

  Overview     What the pump is doing right now: its status as a line of chips, the
               handful of controls worth reaching for, a day of the circuit, and the
               key figures as large numbers.
  Controls     Everything writable, grouped by what it does to the house rather than
               by entity domain: heating, hot water, operation and power, and last
               the integration's own switch for the debug report, under
               troubleshooting, whether control is on or off.
  Performance  How the pump has run: the graphs, and then every reading in its own
               section, which is what the page carried before the tabs.
  All values   The full list, every value the installation offers, in as many columns
               as the screen has room for, with a filter and an explanation per row.

What a page shows is decided by the installation, not by this module. The lists below
only say where a value goes if the installation has it: an entity exists or not, is
enabled or not, has a value or not, and a reading the installation has only ever
reported as zero is left out (seen.py), because CTC answers with a clean zero for
hardware that is not fitted and registers a model or software revision does not use.
Status, controls and stored settings are shown whatever they read, and the full list
shows everything whatever it reads, since that is what it is for.

Every value is carried by one of the integration's own cards
(www/ctc-ecozenith-card.js), drawn by hand rather than out of Home Assistant's tiles,
because a heat pump has a hundred values and they have to fit on a screen: chips for
the status, large numbers for the key figures, a row with the control itself for
anything writable, and a dense list everywhere else. Each name carries the blue "i"
that explains the value, see explanations.py.

The page speaks Home Assistant's language (roadmap R23): Swedish where Home Assistant
is set to Swedish and English otherwise, its headings from TEXT and every explanation
and source from explanations.py, while the integration's own entity names follow the
same language through their translations. What the panel prints stays the panel's:
a display row is named by its row on the panel, read in Swedish whatever the panel or
Home Assistant is set to, and the controller's states and modes are its own Swedish
words, which automations and the history rest on. The small line under a coefficient
of performance or a share of auxiliary heat is the page's own text, though it is read
from attributes that stay Swedish for automations (underlag and skäl, beside basis and
reason): the cards are handed a table that puts it in the page's language
(story_words), and a line the table does not know is shown as it is.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Any

from .const import (
    LABEL_POWER_IN_EN,
    LABEL_POWER_IN_SV,
    LABEL_POWER_OUT_EN,
    LABEL_POWER_OUT_SV,
    LABEL_POWER_OUT_SV_OLD,
)
from .cop import (
    BASIS_WORDS,
    SHARE_BOTH_STUCK,
    SHARE_IMMERSION_SILENT,
    SHARE_IMMERSION_UNREAD,
    SHARE_NOT_SAMPLED,
)
from .explanations import display_explanation, explain, source

URL_PATH = "ctc-ecozenith"
#: What the sidebar says. Short on purpose: it stands in a narrow column beside
#: the other entries, and the address stays as it is so a bookmark still works.
TITLE = "CTC"
#: CTC's own mark, from the integration's icon pack (www/ctc-ecozenith-icon.js).
ICON = "ctc:logo"

#: What the graphs cover: a day of readings, a month of daily figures.
GRAPH_HOURS = 24
GRAPH_DAYS = 30

#: A value in one of these states has nothing to show, and its card is hidden
#: until it has: a register that holds a sentinel, a display page that has not
#: been reached yet, a coefficient of performance with too little history.
HIDDEN_STATES = ["unavailable", "unknown"]
UNAVAILABLE = "unavailable"

#: The coefficient of performance is a figure with a story (sensor.py): its
#: sensors stay available without a figure and write what it rests on and why
#: it is missing as attributes, under the Swedish names and their English twins
#: (attribute_names.py), so the page asks for both and still finds the figure's
#: story when the Swedish names go. Such a value is drawn while it is merely
#: unknown, with the reason under it, and goes only when the sensor is
#: unavailable.
_COP_PREFIX = "cop_"
_COP_SUB = ("underlag", "basis")
_COP_REASON = ("skäl", "reason")
#: The share of auxiliary heat (R28) tells its story the same way, under the
#: same names, so it is drawn the same way. Unlike a coefficient of
#: performance it is often a true zero, an immersion heater that did nothing,
#: which is the answer and not hardware that is missing: the rule that leaves
#: out a reading only ever zero passes it over.
_SHARE_PREFIX = "aux_share_"
_STORY_PREFIXES = (_COP_PREFIX, _SHARE_PREFIX)


def _hidden_states(key: str) -> list[str]:
    """The states in which a value has nothing to show and its card hides it."""
    return [UNAVAILABLE] if key.startswith(_STORY_PREFIXES) else list(HIDDEN_STATES)

#: Where an entity this layout does not know yet, from a newer integration,
#: says it comes from. Its explanation is TEXT's unknown_explanation.
UNKNOWN_SOURCE = "CTC Local Easyconf"

CHIPS_CARD = "custom:ctc-ecozenith-chips"
READINGS_CARD = "custom:ctc-ecozenith-readings"
CONTROLS_CARD = "custom:ctc-ecozenith-controls"
ROWS_CARD = "custom:ctc-ecozenith-rows"

HEAT_POWER = "@heat_power"
SUPPLIED_POWER = "@supplied_power"
ENERGY_OUT = "@energy_out"
ENERGY_IN = "@energy_in"

#: Rows of the display matched on the label it printed, in English and in Swedish,
#: as the text catalogue has them (ids 1807 and 1810). The catalogue strips the
#: trailing unit from a row name, so "Avgiven värme (kW)" is stored as
#: "Avgiven värme" with the unit kW.
_POWER_LABELS = {
    HEAT_POWER: (LABEL_POWER_OUT_EN, LABEL_POWER_OUT_SV, LABEL_POWER_OUT_SV_OLD),
    SUPPLIED_POWER: (LABEL_POWER_IN_EN, LABEL_POWER_IN_SV),
}
_PERIOD_MARKERS = ("/", "24", "30")
#: How a display value's key starts: the page number it was harvested from.
_DISPLAY_KEY = re.compile(r"p\d+_")

# What the unit says about itself, which stays on the page whatever it reads.
_STATUS_TILES = ("system_status", "hp1_status")
_STATUS_ROWS = (
    "compressor_running", "defrosting", "immersion_active", "alarm",
    "blocked", "smartgrid_active", "hs1_status", "sg_mode",
)
#: The event entity (event.py) reads as the moment of the last transition. A
#: row in the full list, not a chip: the chips are states, and the logbook is
#: where the events are read.
_EVENT_ROWS = ("events",)
#: The boolean chips: each is on the line only while it is on, in the colour of
#: what it means, so a raised alarm reads from the door and a row of "Av" is not
#: there to read. The colour is the name of one of Home Assistant's theme
#: colours, which the card turns into --<name>-color; the enum chips stay grey
#: and say what they read whatever it is.
_CHIP_COLOURS = {
    "display_alarm": "error",
    "blocked": "warning",
    "smartgrid_active": "warning",
    "defrosting": "info",
    "compressor_running": "success",
    "immersion_active": "success",
}
#: The line of chips shows the display's alarm where the status list shows the
#: heat pump's own alarm status: the red chip is for what the panel shows, and
#: the list keeps the register's reading as it is.
_CHIP_LINE = tuple(
    "display_alarm" if key == "alarm" else key for key in _STATUS_TILES + _STATUS_ROWS
)
#: What a chip reads in place of its own word while it is on, where the entity
#: carries it: the first of the attributes named that is there. The display's
#: alarm reads the code and the text as the panel prints them.
_CHIP_VALUE_ATTRIBUTES = {"display_alarm": ("visat", "shown")}
#: What a binary sensor reads while it is on.
_ON = "on"

#: The overview's key figures: how warm it is around the circuit, how hard the
#: compressor is working and what comes out of it.
_KEY_READINGS = (
    "outdoor_temp", "room_temp_1", "dhw_temp", "hs1_flow", "return_temp",
    "hp1_rps", HEAT_POWER, SUPPLIED_POWER, "cop_day", "cop_week", "cop_month",
    "degree_minutes",
)
#: Keys the page may have a place for before the integration makes the entity,
#: when a page change and a sensor are built apart. The builder leaves out what
#: has no entity, so nothing shows until the sensor exists; the catalogue test
#: (tests/test_dashboard.py) allows exactly these to be known here and not made,
#: and asks for the set to be emptied the day the integration has caught up.
#: Empty now: the week's and the month's coefficient of performance exist.
AHEAD_OF_THE_INTEGRATION: frozenset[str] = frozenset()
#: The controls the overview carries, in the order they matter to someone
#: standing in the house. The rest are one tab away.
_QUICK_CONTROLS = ("ctl_room_setpoint_1", "ctl_dhw_mode", "ctl_extra_dhw", "ctl_price_mode")

#: The sensor that counts what Home Assistant holds in the controller, and the
#: button that lets go of all of it (R49).
_OVERRIDES = "active_overrides"
_RELEASE_ALL = "release_control"
#: The switch of the voluntary debug report (switch.py), Rapportera alla värden
#: till utvecklaren: a tick box on the controls tab, under a heading of its own
#: and last, since it does nothing to the house. It is there whether control
#: is on or off: the owner's choice to report has nothing to do with whether
#: Home Assistant may steer the pump.
_DEBUG_REPORT = "debug_report"
_TROUBLESHOOTING = ("troubleshooting", "mdi:bug-outline", (_DEBUG_REPORT,))
#: The overrides chip's colour, one of the theme's four the chips card knows:
#: Home Assistant holding the pump is worth seeing, not an alarm.
_OVERRIDES_COLOUR = "info"

#: Everything writable, grouped by what it does to the house: (id, icon, keys).
_CONTROL_GROUPS = (
    ("heating", "mdi:radiator", ("ctl_room_setpoint_1", "ctl_zone_mode_1")),
    ("hot_water", "mdi:water-boiler", ("ctl_dhw_mode", "ctl_dhw_setpoint", "ctl_extra_dhw")),
    # The button that lets go of every override stands last among the power
    # controls, with the five minute rule in its own explanation, rather than
    # in a column of its own beside a note. On the overview it stands beside
    # the count of what is being overridden right now, while there is any
    # (R49, _overrides_section).
    ("power", "mdi:flash",
     ("ctl_price_mode", "ctl_max_rps", "ctl_immersion_lower", "ctl_immersion_upper",
      _RELEASE_ALL)),
)

# The readings, in sections: (id, icon, keys).
_READINGS = (
    ("temperatures", "mdi:thermometer",
     ("outdoor_temp", "room_temp_1", "set_room_1", "hs1_flow", "hs1_flow_setpoint",
      "return_temp", "radiator_temp", "room_temp_2", "hp1_outdoor_temp")),
    ("hot_water", "mdi:water-boiler",
     ("dhw_temp", "dhw_capacity", "set_dhw_mode", "dhw_lower_temp", "dhw_stop_temp",
      "tank_lower_setpoint", "set_extra_dhw", "dhw_circulation", "dhw_temp_raw")),
    ("energy", "mdi:lightning-bolt",
     ("cop_day", "cop_week", "cop_month", "cop_year", "cop_first_year", "cop_lifetime",
      # R28: how much of the electricity went to the immersion heater, beside
      # the coefficient of performance it is the other half of.
      "aux_share_day", "aux_share_week", "aux_share_month", "aux_share_year",
      "aux_share_lifetime",
      HEAT_POWER, SUPPLIED_POWER,
      ENERGY_OUT, "compressor_kwh", ENERGY_IN, "immersion_kwh",
      "immersion_upper_kw", "immersion_lower_kw",
      "current_l1", "current_l2", "current_l3", "hp1_power")),
)
_TECHNICAL = (
    ("compressor", "mdi:heat-pump-outline",
     ("hp1_rps", "hp1_charge_pump", "hp1_brine_pump", "hp1_fan", "hp1_in", "hp1_out",
      "hp1_brine_in", "hp1_brine_out", "hp1_discharge", "hp1_suction",
      "hp1_high_pressure", "hp1_low_pressure", "compressor_hours",
      "compressor_hours_24h", "degree_minutes", "hp1_defrost_timer")),
    # What the status codes said from one round to the next (transitions.py).
    # Not a learnt section: a count of zero since midnight is the answer, not
    # hardware that is missing, and the timestamps hide themselves until a
    # start has been seen.
    ("cycles", "mdi:counter",
     ("last_start", "starts_today", "last_run", "mean_run_24h",
      "defrosts_today", "last_defrost")),
    ("settings", "mdi:cog-outline",
     ("set_heating_mode_1", "set_hp1_blocked", "set_slope_1", "set_adjust_1",
      "set_max_rps_1", "set_max_immersion_lower", "set_max_immersion_upper")),
    ("unit", "mdi:information-outline",
     ("hp_model", "made", "serial", "display_fw", "hp_fw", "bootloader",
      "control_sw", "control_sw_year", "display_harvest")),
)

#: The graphs: (id, kind, keys). "history" is a day of readings as Home
#: Assistant's history graph draws them, lines for numbers and timeline bands
#: for states; "change" and "mean" are a month of long term statistics, a bar
#: per day. A graph whose entities this installation does not have is left out,
#: which is how a unit without a history page ends up without a coefficient of
#: performance, and a reading only ever zero is left out of it (seen.py), which
#: is how an i550 Pro whose compressor hours stand at zero gets no running time.
_GRAPHS = (
    ("graph_temps", "history",
     ("outdoor_temp", "hs1_flow", "return_temp", "dhw_temp", "room_temp_1")),
    # What the pump did when: the two statuses and the heater as bands, not the
    # binaries derived from hp1_status, which would be four bands of one register.
    # The SmartGrid mode is not among them: its state is a label, "Normal" on a
    # pump that never uses it, and a label is never zero, so the rule that
    # leaves out a reading only ever zero (seen.py) cannot tell such a pump
    # from one that does, and the band would lie flat on both. The mode stays
    # a chip, as itself and as SmartGrid active while it is anything else.
    ("graph_pump", "history", ("hp1_status", "system_status", "immersion_active")),
    # How well the heating curve is followed: the flow against its setpoint,
    # with the outdoor temperature the setpoint is worked out from.
    ("graph_flow", "history", ("hs1_flow", "hs1_flow_setpoint", "outdoor_temp")),
    ("graph_compressor", "history", ("hp1_rps", "degree_minutes")),
    ("graph_energy", "change", (ENERGY_OUT, "compressor_kwh", "immersion_kwh")),
    # 62214 only grows, so its change per day is the hours the compressor ran.
    ("graph_runtime", "change", ("compressor_hours",)),
    # The day's figure, and beside it the month's as it stood that day.
    ("graph_cop", "mean", ("cop_day", "cop_month")),
)
#: Which graphs each tab shows, in order: the overview a day of the circuit and
#: a day of what the pump did; performance what the overview does not already
#: show, so the tab adds something rather than opening with the same picture.
_OVERVIEW_GRAPHS = ("graph_temps", "graph_pump")
_PERFORMANCE_GRAPHS = (
    "graph_flow", "graph_compressor", "graph_energy", "graph_runtime", "graph_cop"
)
#: The line a graph's heading promises, where it has one: without it, because
#: the installation lacks the entity or has only ever read it as zero, the
#: graph is left out rather than drawn under a heading that promises more. The
#: flow graph without its setpoint is two of the overview's five lines over
#: again, the very picture the performance tab is there not to repeat (a unit
#: whose heating circuit is switched off reads the setpoint as a flat 0), and
#: the compressor graph without the compressor's speed is a line of degree
#: minutes under the compressor's name.
_GRAPH_REQUIRES = {"graph_flow": "hs1_flow_setpoint", "graph_compressor": "hp1_rps"}
_GRAPH_ICONS = {"graph_pump": "mdi:chart-timeline", "history": "mdi:chart-line"}


def _graph(graph_id: str) -> tuple[str, str, tuple[str, ...]]:
    return next(graph for graph in _GRAPHS if graph[0] == graph_id)


def _graph_icon(graph_id: str, kind: str) -> str:
    return _GRAPH_ICONS.get(graph_id) or _GRAPH_ICONS.get(kind) or "mdi:chart-bar"

#: The sections of readings, where a value only ever zero is left out.
_LEARNT_SECTIONS = frozenset({"temperatures", "hot_water", "energy", "compressor"})

#: How every control entity's name starts, in Swedish and in English: the names
#: in translations/sv.json and en.json, sv.json's taken word for word from
#: const.CONTROL_NUMBERS and _SELECTS.
_CONTROL_PREFIX = "Styr "
_CONTROL_PREFIX_EN = "Control "
_CONTROL_PREFIXES = (_CONTROL_PREFIX, _CONTROL_PREFIX_EN)
#: A button has no value: its state is the time it was last pressed, unknown
#: until then, so it has a row where it can be pressed and none in the list.
_BUTTON = "button."

#: The full list, group by group, so that every value the integration makes has
#: a place in it: (id, icon, keys). Display values follow, a group per page.
_VALUE_GROUPS = (
    ("status", ICON,
     _STATUS_TILES + _STATUS_ROWS + ("display_alarm",) + _EVENT_ROWS + ("last_alarm",)),
    ("control", "mdi:tune-variant",
     (_OVERRIDES, *(k for _id, _icon, keys in _CONTROL_GROUPS for k in keys))),
    _TROUBLESHOOTING,
    *_READINGS,
    *_TECHNICAL,
)

#: Every key the layout knows of. What an installation has beyond this is from a
#: newer integration than the page, and lands under "Other".
_KNOWN_KEYS = frozenset(
    key for _id, _icon, keys in _VALUE_GROUPS for key in keys if not key.startswith("@")
)

TEXT = {
    "sv": {
        # The language itself, which the builders hand the explanations.
        "language": "sv",
        "overview": "Översikt",
        "controls": "Styrning",
        "performance": "Prestanda",
        "values": "Alla värden",
        "display": "Displayvärden",
        "status": "Drift",
        "overrides": "Styrs från Home Assistant",
        "quick": "Snabbstyrning",
        "readings": "Nyckeltal",
        "temperatures": "Temperaturer",
        "hot_water": "Varmvatten",
        "energy": "Energi och värmefaktor",
        "heating": "Värme",
        "power": "Drift och el",
        "control": "Styrning",
        "troubleshooting": "Felsökning",
        "compressor": "Kompressor och köldkrets",
        "cycles": "Starter, körningar och avfrostningar",
        "settings": "Pumpens inställningar",
        "unit": "Om enheten",
        "other": "Övrigt",
        "graph_temps": "Temperaturer det senaste dygnet",
        "graph_pump": "Pumpen det senaste dygnet",
        "graph_flow": "Framledning och börvärde det senaste dygnet",
        "graph_compressor": "Kompressorn det senaste dygnet",
        "graph_energy": "Energi per dygn",
        "graph_runtime": "Kompressorns gångtid per dygn",
        "graph_cop": "Värmefaktor per dygn",
        "filter": "Sök bland värdena",
        "empty": "Inget värde matchar sökningen.",
        # R39: the box on All values that shows the empty rows too, and what
        # the three numbers beside it are.
        "show_empty": "Visa även tomma",
        "count": "visade / med värde / totalt",
        "press": "Utför",
        "control_off": (
            "Styrning är avstängd. Slå på **Tillåt styrning av värmepumpen** i "
            "[integrationens inställningar](/config/integrations/integration/ctc_ecozenith), "
            "så dyker reglagen upp här."
        ),
        "no_controls": (
            "Styrning är påslagen, men inga reglage är aktiverade. De ligger på "
            "[enhetens sida](/config/integrations/integration/ctc_ecozenith), under entiteter."
        ),
        "not_running": (
            "Ingen CTC-värmepump är igång just nu. Sidan fylls i av sig själv när "
            "integrationen har startat."
        ),
        "setup_retry": (
            "{name} svarar inte just nu{reason}. Home Assistant försöker igen av sig "
            "själv, och sidan fylls i när värmepumpen har kommit igång."
        ),
        "failed": "Sidan kunde inte byggas. Detaljerna står i Home Assistants logg.",
        "more_info": "Mer info",
        "explain": "Förklaring",
        # The cards' own words for a binary state, by device class and plainly,
        # so a Swedish page never says "Not running" (state_words). The pair for
        # a problem stands beside the binary's name, which is Larm, so the word
        # for a raised one is not Larm again: the chip read "Larm Larm".
        "state_on": "Till",
        "state_off": "Av",
        "state_problem_on": "Utlöst",
        "state_problem_off": "OK",
        # A control without a value yet, and the 0 the pump means as no limit.
        "state_unset": "ej satt",
        "state_unset_note": (
            "Pumpen lämnar inte ut sitt eget värde här, så reglaget står tomt tills "
            "Home Assistant har skrivit ett."
        ),
        # A control with a mirror whose own value did not answer in the last
        # Modbus round (F2.1, F2.3): unknown as well, but for another reason.
        "state_unread": "ej läst",
        "state_unread_note": (
            "Pumpens eget värde gick inte att läsa i den senaste omgången och syns igen när "
            "nästa läsning svarar. Ett värde som sätts nu skickas ändå till pumpen."
        ),
        "state_no_limit": "ingen gräns",
        # A value unknown because nothing has happened yet, in the words of
        # what has not happened (_NONE_YET): the full list shows the row
        # whatever it reads, and "Okänd" there looks like a value gone missing.
        "state_none_alarm": "inget larm ännu",
        "state_none_event": "ingen händelse ännu",
        "state_none_start": "ingen start ännu",
        "state_none_run": "ingen körning ännu",
        "state_none_defrost": "ingen avfrostning ännu",
        # R49: a control Home Assistant holds right now, the hover text that
        # says what holding means, the pump's own value beside it, and the
        # button that lets go of one number.
        "state_controlled": "Styrd",
        "state_controlled_note": (
            "Home Assistant håller värdet och skriver om det varje minut. Pumpen glömmer det "
            "cirka fem minuter efter sista skrivningen, så tystnar Home Assistant går pumpen "
            "tillbaka till sitt eget värde."
        ),
        "state_pump_value": "pumpen själv",
        "state_release": "Släpp",
        # R23, R28: what a coefficient of performance or a share of auxiliary
        # heat rests on, the line under the figure, by span. The same words as
        # cop.BASIS_WORDS, which the attribute underlag carries in every
        # language; an English page puts its own in their place (story_words).
        "basis_year": "rullande år",
        "basis_day": "senaste dygnet",
        "basis_week": "senaste 7 dygnen",
        "basis_month": "senaste 30 dygnen",
        "basis_first_year": "första året",
        "basis_lifetime": "hela livslängden",
        # For an entity this layout does not know yet, from a newer integration.
        "unknown_explanation": (
            "Ett värde från integrationen som sidan ännu inte har någon egen förklaring för."
        ),
    },
    "en": {
        "language": "en",
        "overview": "Overview",
        "controls": "Controls",
        "performance": "Performance",
        "values": "All values",
        "display": "Display values",
        "status": "Operation",
        "overrides": "Held by Home Assistant",
        "quick": "Quick controls",
        "readings": "Key readings",
        "temperatures": "Temperatures",
        "hot_water": "Hot water",
        "energy": "Energy and COP",
        "heating": "Heating",
        "power": "Operation and power",
        "control": "Control",
        "troubleshooting": "Troubleshooting",
        "compressor": "Compressor and refrigerant circuit",
        "cycles": "Starts, runs and defrosts",
        "settings": "Settings in the heat pump",
        "unit": "About the unit",
        "other": "Other",
        "graph_temps": "Temperatures over the last day",
        "graph_pump": "The pump over the last day",
        "graph_flow": "Flow and setpoint over the last day",
        "graph_compressor": "The compressor over the last day",
        "graph_energy": "Energy per day",
        "graph_runtime": "Compressor running time per day",
        "graph_cop": "COP per day",
        "filter": "Search the values",
        "empty": "No value matches the search.",
        "show_empty": "Show empty ones too",
        "count": "shown / with a value / total",
        "press": "Run",
        "control_off": (
            "Control is switched off. Turn on **Allow controlling the heat pump** in "
            "[the integration's settings](/config/integrations/integration/ctc_ecozenith) "
            "and the controls appear here."
        ),
        "no_controls": (
            "Control is switched on, but no controls are enabled. They are on "
            "[the device's page](/config/integrations/integration/ctc_ecozenith), "
            "under entities."
        ),
        "not_running": (
            "No CTC heat pump is running right now. The page fills in by itself once "
            "the integration has started."
        ),
        "setup_retry": (
            "{name} is not answering right now{reason}. Home Assistant retries by "
            "itself, and the page fills in once the heat pump is back."
        ),
        "failed": "The page could not be built. The details are in Home Assistant's log.",
        "more_info": "More info",
        "explain": "Explanation",
        "state_on": "On",
        "state_off": "Off",
        "state_problem_on": "Raised",
        "state_problem_off": "OK",
        "state_unset": "not set",
        "state_unset_note": (
            "The pump does not give out its own value here, so the control stays empty "
            "until Home Assistant has written one."
        ),
        "state_unread": "not read",
        "state_unread_note": (
            "The pump's own value could not be read in the last round and shows again when "
            "the next read answers. A value set now is still sent to the pump."
        ),
        "state_no_limit": "no limit",
        "state_none_alarm": "no alarm yet",
        "state_none_event": "no event yet",
        "state_none_start": "no start yet",
        "state_none_run": "no run yet",
        "state_none_defrost": "no defrost yet",
        "state_controlled": "Overridden",
        "state_controlled_note": (
            "Home Assistant holds this value and writes it again every minute. The pump forgets "
            "it about five minutes after the last write, so if Home Assistant falls silent the "
            "pump goes back to its own value."
        ),
        "state_pump_value": "pump's own",
        "state_release": "Release",
        "basis_year": "rolling year",
        "basis_day": "last day",
        "basis_week": "last 7 days",
        "basis_month": "last 30 days",
        "basis_first_year": "first year",
        "basis_lifetime": "whole lifetime",
        "unknown_explanation": (
            "A value from the integration that the page has no explanation of its own for yet."
        ),
    },
}

#: The explanation of a value from a newer integration than the page, in Swedish.
UNKNOWN_EXPLANATION = TEXT["sv"]["unknown_explanation"]

#: Keys whose 0 the pump means as no limit at all, so the card writes the word
#: (state_no_limit) rather than a 0: the compressor's top speed, where an i255
#: runs at 50 rps with 0 set, as the stored setting and as the control that
#: mirrors it. The card uses the word only for the pump's own value, never for
#: a 0 somebody wrote through the control. Whether the immersion heater limits
#: mean the same by 0 is an open question, and they are left as they read.
_NO_LIMIT_AT_ZERO = frozenset({"ctl_max_rps", "set_max_rps_1"})

#: Keys whose unknown means that nothing has happened yet, not that a value is
#: missing: the last alarm until a panel has shown one, the events until the
#: first transition, the cycle timestamps until a start or a defrost has been
#: seen while Home Assistant ran (the last defrost all summer). The full list
#: shows every row whatever it reads, and these would read "Okänd" there for
#: months, the look the list was rid of when the button left it. The card
#: writes the word the page gives instead (state_none_*), the way it writes
#: "ej satt" for a control without a value; the other tabs hide the row.
_NONE_YET = {
    "last_alarm": "none_alarm",
    "events": "none_event",
    "last_start": "none_start",
    "last_run": "none_run",
    "last_defrost": "none_defrost",
}

#: What the words for states are called in TEXT.
_STATE_PREFIX = "state_"


def state_words(text: Mapping[str, str]) -> dict[str, str]:
    """The page's own words for states, as the cards take them under "states".

    Every TEXT entry that starts with state_, without the prefix: "on", "off"
    and a pair per device class that has its own words ("problem_on"). The
    cards look a binary state up by device class first and then plainly, and
    leave a state the table has no word for to Home Assistant.
    """
    return {
        key[len(_STATE_PREFIX):]: value
        for key, value in text.items() if key.startswith(_STATE_PREFIX)
    }


#: What TEXT calls the words for each span the line under a figure names.
_BASIS_PREFIX = "basis_"

#: The reasons a coefficient of performance or a share of auxiliary heat gives
#: for an empty figure that are always the same words, as cop.py writes them
#: in the attribute skäl, and what an English page says instead. The ones cop
#: names are taken from it; the others are literals there and written out here
#: word for word, which tests/test_card_readings.py holds to what cop.py's own
#: functions give.
_REASON_WORDS: dict[str, dict[str, str]] = {
    "en": {
        SHARE_NOT_SAMPLED: (
            "the samples in the basis lack the immersion heater's counter; newer samples have "
            "it, so the share comes when such a sample is old enough"
        ),
        SHARE_IMMERSION_UNREAD: (
            "the immersion heater's counter has not been read together with the display's "
            "counters since Home Assistant started"
        ),
        SHARE_IMMERSION_SILENT: (
            "register 62191 has not answered, so the immersion heater's counter is missing"
        ),
        SHARE_BOTH_STUCK: (
            "both counters stand at zero although the unit has been running; the controller "
            "does not fill them"
        ),
        "räknarna har inte lästs": "the counters have not been read",
        "första året är inte fullt ännu": "the first year is not complete yet",
        "driftstarten är okänd": "the day the unit was commissioned is unknown",
        "registret 62341 har inte svarat, så tillförd energi saknas": (
            "register 62341 has not answered, so the consumed energy is missing"
        ),
        "väntar på ett prov som är 20 till 30 timmar gammalt": (
            "waiting for a sample 20 to 30 hours old"
        ),
        "värmepumpens sida ger inga värden, vilket en avstängd värmepump inte gör": (
            "the heat pump's page gives no values, which a heat pump switched off does not"
        ),
        "väntar på en andra läsning, summeringen börjar vid nästa skörd": (
            "waiting for a second reading; the sum starts at the next harvest"
        ),
        "displayen har inte svarat sedan Home Assistant startade": (
            "the display has not answered since Home Assistant started"
        ),
    },
}

#: The reasons with a figure, a date or a row name in them, as cop.py's
#: f-strings write them: a regular expression for the Swedish line, anchored
#: at both ends, and the page's words with $1, $2 for what it caught, the way
#: the card's String.replace takes them. A row name stays the panel's, as on
#: the rest of the page. A spelling that changes in cop.py without a change
#: here is shown as it is, in Swedish, and fails tests/test_card_readings.py.
_REASON_PATTERNS: dict[str, tuple[tuple[str, str], ...]] = {
    "en": (
        (
            r"^(\d+) av (\d+) dygn samlade, äldsta provet (.+)$",
            "$1 of $2 days collected, oldest sample $3",
        ),
        (
            r"^(\d+) av (\d+) dygn samlade, inget prov med elpatronens räknare sparat ännu$",
            "$1 of $2 days collected, no sample with the immersion heater's counter kept yet",
        ),
        (
            r"^(\d+) av (\d+) dygn samlade, inget prov sparat ännu$",
            "$1 of $2 days collected, no sample kept yet",
        ),
        (
            r"^inget sparat prov är (\d+) till (\d+) dygn gammalt$",
            "no kept sample is $1 to $2 days old",
        ),
        (
            r"^väntar på ett prov som är (\d+) till (\d+) timmar gammalt$",
            "waiting for a sample $1 to $2 hours old",
        ),
        (
            r"^för lite energi ännu, (-?[\d.]+) av (-?[\d.]+) kWh$",
            "too little energy so far, $1 of $2 kWh",
        ),
        (
            r"^kvoten (\S+) är orimlig, räknarna hör inte ihop$",
            "the quotient $1 is implausible; the counters do not belong together",
        ),
        (
            r"^displayen har inte svarat sedan (.+)$",
            "the display has not answered since $1",
        ),
        (
            r"^displayens rad (.+) har inte lästs sedan (\S+)$",
            "the display's row $1 has not been read since $2",
        ),
        (
            r"^räknarna har gått bakåt sedan (.+), enheten är bytt eller nollställd$",
            "the counters have gone backwards since $1; the unit has been replaced or reset",
        ),
        (
            r"^båda räknarna har stått still sedan (.+)$",
            "both counters have stood still since $1",
        ),
        (
            r"^kompressorns räknare står på noll fast kompressorn har gått (\d+) timmar, "
            r"styrenheten fyller den inte$",
            "the compressor's counter stands at zero although the compressor has run for $1 "
            "hours; the controller does not fill it",
        ),
        (
            r"^räknaren står på noll fast kompressorn har gått (\d+) timmar, "
            r"styrenheten fyller den inte$",
            "the counter stands at zero although the compressor has run for $1 hours; the "
            "controller does not fill it",
        ),
    ),
}


def story_words(text: Mapping[str, str]) -> dict[str, Any] | None:
    """The line under a figure in the page's language, as the cards take it under "story".

    What a coefficient of performance or a share of auxiliary heat rests on,
    and why it has no figure, are attributes whose values stay Swedish for
    automations (roadmap R23); the card draws one of them under the figure.
    ``words`` puts a whole line in the page's words: the six spans from
    BASIS_WORDS, by TEXT's basis_ entries, and the reasons that are always the
    same words. ``patterns`` does the same for the reasons with a figure or a
    date in them, first match wins. A line neither knows is drawn as it is.
    None on a Swedish page, whose words are the attributes' own.
    """
    lang = language(text.get("language"))
    if lang == "sv":
        return None
    words = {
        swedish: text[f"{_BASIS_PREFIX}{span}"]
        for span, swedish in BASIS_WORDS.items() if f"{_BASIS_PREFIX}{span}" in text
    }
    words.update(_REASON_WORDS.get(lang, {}))
    return {
        "words": words,
        "patterns": [
            {"match": match, "text": said} for match, said in _REASON_PATTERNS.get(lang, ())
        ],
    }


def language(lang: str | None) -> str:
    """Swedish for any sv-* setting, English otherwise."""
    return "sv" if str(lang or "").lower().startswith("sv") else "en"


def _heading(text: str, icon: str) -> dict[str, Any]:
    return {"type": "heading", "heading": text, "heading_style": "title", "icon": icon}


def _shown_when_available(entity_id: str, states: list[str] | None = None) -> dict[str, Any]:
    return {"condition": "state", "entity": entity_id,
            "state_not": list(HIDDEN_STATES) if states is None else list(states)}


def _states_of(item: Mapping[str, Any]) -> list[str]:
    """The states that hide a built item: what its own flag says."""
    return [UNAVAILABLE] if item.get("show_reason") else list(HIDDEN_STATES)


def _any_of(conditions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return conditions if len(conditions) == 1 else [{"condition": "or", "conditions": conditions}]


def _label_of(value: Mapping[str, Any]) -> str:
    return str(value.get("label") or "").strip()


def display_roles(pump: Mapping[str, Any]) -> dict[str, str]:
    """The display rows the overview picks out, by role, when the pump has them."""
    roles: dict[str, str] = {}
    for key, role in ((pump.get("energy_out"), ENERGY_OUT), (pump.get("energy_in"), ENERGY_IN)):
        if key:
            roles[role] = key
    for page in pump.get("pages") or []:
        for value in page.get("values") or []:
            label = _label_of(value).casefold()
            if str(value.get("unit") or "").casefold() != "kw":
                continue
            if any(marker in label for marker in _PERIOD_MARKERS):
                continue
            for role, names in _POWER_LABELS.items():
                if role not in roles and label.startswith(names):
                    roles[role] = value["key"]
    # Modbus 62341 is the same counter as the display's consumed energy, and it
    # is fresh every half minute rather than every half hour. One is enough.
    if ENERGY_IN in roles and "compressor_kwh" in (pump.get("entities") or {}):
        del roles[ENERGY_IN]
    return roles


def _display_labels(pump: Mapping[str, Any]) -> dict[str, str]:
    """What each display value is called on the page: its own row name on the panel.

    The entity is named "<page title>: <row>", and under a heading that already
    names the page the prefix only pushes the value aside. A name the user gave
    the entity is kept as it is.
    """
    names = pump.get("names") or {}
    labels: dict[str, str] = {}
    for page in pump.get("pages") or []:
        title = str(page.get("title") or "")
        for value in page.get("values") or []:
            key = value.get("key")
            label = _label_of(value)
            original = f"{title}: {label}" if title else label
            given = names.get(key)
            labels[key] = label if not given or given == original else str(given)
    return labels


class _Builder:
    """Cards for one tab of one pump, remembering which keys have found a place."""

    def __init__(
        self, pump: Mapping[str, Any], text: Mapping[str, str], ha_version: tuple[int, int]
    ) -> None:
        self.entities: Mapping[str, str] = pump.get("entities") or {}
        self.names: Mapping[str, str] = pump.get("names") or {}
        self.labels = _display_labels(pump)
        self.roles = display_roles(pump)
        # Kept for the day a card needs a frontend that is newer than the
        # integration's minimum; everything the page uses is in 2024.12.
        self.ha_version = tuple(ha_version)
        self.text = text
        self.pages: list[Mapping[str, Any]] = list(pump.get("pages") or [])
        self.interval = pump.get("display_interval")
        #: The live page and its pace, for the source line of its rows.
        self.live = (pump.get("live_page"), pump.get("live_interval"))
        self.rows_of_page = {
            value.get("key"): (value, page)
            for page in self.pages for value in page.get("values") or []
        }
        # A share of auxiliary heat at zero is an answer, never missing
        # hardware, so it is never among the readings left out for it.
        self.unused = {
            key for key in pump.get("unused") or [] if not str(key).startswith(_SHARE_PREFIX)
        }
        self.placed: set[str] = set()
        #: The language the explanations are given in: the language of the
        #: words the tab is built with, which is the page's.
        self.lang = language(text.get("language"))
        #: The line under a figure in that language, or None on a Swedish page.
        self.story = story_words(text)

    def explanation(self, key: str) -> dict[str, str]:
        """What a value is and where it comes from, in the page's language, for the card."""
        found: dict[str, str] = {}
        if key in self.rows_of_page:
            value, page = self.rows_of_page[key]
            found["explanation"] = display_explanation(
                value.get("label"), page.get("title"), self.lang
            )
        else:
            found["explanation"] = (
                explain(key, self.lang) or TEXT[self.lang]["unknown_explanation"]
            )
        found["source"] = (
            source(key, self.pages, self.interval, self.lang, live=self.live) or UNKNOWN_SOURCE
        )
        return found

    def entity_id(self, key: str) -> str | None:
        """The entity behind a key or a display role, without claiming it."""
        real = self.roles.get(key) if key.startswith("@") else key
        return self.entities.get(real) if real else None

    def _take(self, key: str, learnt: bool) -> tuple[str, str, str | None] | None:
        """(key, entity_id, name) for a key or display role that has an entity.

        With learnt, a reading this installation has only ever given as zero is
        taken as placed but left out, so it turns up nowhere else on the tab.
        """
        real = self.roles.get(key) if key.startswith("@") else key
        entity_id = self.entities.get(real) if real else None
        if not entity_id or real in self.placed:
            return None
        self.placed.add(real)
        if learnt and real in self.unused:
            return None
        name = self.labels.get(real) or self.names.get(real)
        return real, entity_id, str(name) if name else None

    @staticmethod
    def _under_a_control_heading(name: str) -> str:
        """The name without the "Styr" or "Control" every control's name starts with.

        Under a heading that already says control the prefix says nothing, and
        with the control beside it, it takes the room the name needs:
        "Styr max elpatron nedre" and "... övre" both end in "Styr max elpatr…".
        """
        for prefix in _CONTROL_PREFIXES:
            if name.startswith(prefix):
                rest = name[len(prefix):]
                return rest[:1].upper() + rest[1:]
        return name

    def item(
        self, key: str, hideable: bool, learnt: bool = False, trim: bool = False
    ) -> dict[str, Any] | None:
        """One value as the cards take it: what it is, what it means, where from."""
        taken = self._take(key, learnt)
        if taken is None:
            return None
        real, entity_id, name = taken
        name = name or entity_id
        if trim:
            name = self._under_a_control_heading(name)
        item: dict[str, Any] = {"entity": entity_id, "name": name}
        item.update(self.explanation(real))
        if real in _NO_LIMIT_AT_ZERO:
            item["zero_means"] = "no_limit"
        if real in _NONE_YET:
            item["unknown_means"] = _NONE_YET[real]
        if real.startswith(_STORY_PREFIXES):
            # The figure says what it rests on and, while it has none, why: the
            # card draws the one or the other in small text under it.
            item["sub"] = list(_COP_SUB)
            item["reason"] = list(_COP_REASON)
            if hideable:
                # Gone only with the sensor: unknown is when it has something to say.
                item["show_reason"] = True
        elif hideable:
            # The card leaves the value out itself while it has nothing to show.
            item["hide_unavailable"] = True
        return item

    def _items(self, keys: Iterable[str], **kwargs: Any) -> list[dict[str, Any]]:
        return [item for item in (self.item(key, **kwargs) for key in keys) if item]

    def _card(self, kind: str, field: str, items: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not items:
            return None
        card: dict[str, Any] = {
            "type": kind,
            field: items,
            "more_info": self.text["more_info"],
            "explain": self.text["explain"],
            "states": state_words(self.text),
            # A section can be several columns wide, and a card of values fills it.
            "grid_options": {"columns": "full"},
        }
        # The line under a figure in the page's language, once per card and
        # only on a card that draws one; a Swedish page has none to send.
        story = self.story if any("sub" in item for item in items) else None
        if story is not None:
            card["story"] = story
        return card

    @staticmethod
    def _hide_when_empty(card: dict[str, Any] | None, items: list[dict[str, Any]]) -> None:
        """A card whose every value is hidden would be an empty frame."""
        if card is not None:
            card["visibility"] = _any_of(
                [_shown_when_available(i["entity"], _states_of(i)) for i in items]
            )

    def chips(self, keys: Iterable[str]) -> dict[str, Any] | None:
        """What the pump is doing right now, as a line of chips.

        An enum chip is always there and says what it reads, an unavailable pump
        included. A boolean chip is there only while it is on, in the colour of
        what it means.
        """
        items: list[dict[str, Any]] = []
        for key in keys:
            item = self.item(key, hideable=False)
            if item is None:
                continue
            if key in _CHIP_COLOURS:
                item["on_state"] = _ON
                item["color"] = _CHIP_COLOURS[key]
            if key in _CHIP_VALUE_ATTRIBUTES:
                item["value_attribute"] = list(_CHIP_VALUE_ATTRIBUTES[key])
            items.append(item)
        return self._card(CHIPS_CARD, "items", items)

    def readings(self, keys: Iterable[str], learnt: bool = True) -> dict[str, Any] | None:
        """The key figures, each a small name over a large number."""
        items = self._items(keys, hideable=True, learnt=learnt)
        card = self._card(READINGS_CARD, "items", items)
        self._hide_when_empty(card, items)
        return card

    def controls(self, keys: Iterable[str]) -> dict[str, Any] | None:
        """One row per control: the name, and the thing it is."""
        card = self._card(CONTROLS_CARD, "items",
                          self._items(keys, hideable=False, trim=True))
        if card is not None:
            card["press"] = self.text["press"]
        return card

    def rows(
        self, keys: Iterable[str], hideable: bool, learnt: bool = False
    ) -> dict[str, Any] | None:
        """A dense list, name on the left and value on the right."""
        items = self._items(keys, hideable=hideable, learnt=learnt)
        card = self._card(ROWS_CARD, "rows", items)
        if hideable:
            self._hide_when_empty(card, items)
        return card

    def list_card(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """The list card itself, around rows and headings that are already built."""
        card = self._card(ROWS_CARD, "rows", rows)
        assert card is not None  # only called with rows in hand
        return card

    def graph(self, graph_id: str, kind: str, keys: Iterable[str]) -> dict[str, Any] | None:
        """A graph of what this installation has of the keys, or None.

        Graphs read what the sections show, so they claim nothing: the same
        value is a chip on one tab and a line here. A graph whose heading
        promises a line this installation has not got is left out whole
        (_GRAPH_REQUIRES), rather than drawn as what is left of it.
        """
        required = _GRAPH_REQUIRES.get(graph_id)
        if required and (not self.entity_id(required) or required in self.unused):
            return None
        entities = []
        shown: list[dict[str, Any]] = []
        for key in keys:
            entity_id = self.entity_id(key)
            real = self.roles.get(key, key)
            if not entity_id or real in self.unused:
                continue
            name = self.labels.get(real) or self.names.get(real)
            entities.append({"entity": entity_id, "name": str(name)} if name else entity_id)
            shown.append(_shown_when_available(entity_id, _hidden_states(real)))
        if not entities:
            return None
        card: dict[str, Any] = {"grid_options": {"columns": "full"}}
        if kind == "history":
            card.update({"type": "history-graph", "hours_to_show": GRAPH_HOURS,
                         "entities": entities})
        else:
            # Long term statistics: a day at a time, which is what a counter or
            # a coefficient of performance is worth looking at over a month. A
            # bar per day, whatever the statistic: each day is one number, and
            # a line drew values between the days that were never there.
            card.update({
                "type": "statistics-graph",
                "chart_type": "bar",
                "period": "day",
                "days_to_show": GRAPH_DAYS,
                "stat_types": [kind],
                "entities": [e["entity"] if isinstance(e, dict) else e for e in entities],
            })
        card["visibility"] = _any_of(shown)
        return card


def _section(
    heading: dict[str, Any], cards: Iterable[dict[str, Any] | None], **extra: Any
) -> dict[str, Any] | None:
    """A grid section, hidden as a whole while every card in it is hidden."""
    cards = [card for card in cards if card]
    if not cards:
        return None
    section: dict[str, Any] = {"type": "grid", "cards": [heading, *cards], **extra}
    if all(card.get("visibility") for card in cards):
        conditions = []
        for card in cards:
            conditions.extend(_conditions_in(card["visibility"]))
        section["visibility"] = _any_of(conditions)
    return section


def _conditions_in(visibility: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The state conditions of a card's visibility, unwrapping an "or"."""
    found: list[dict[str, Any]] = []
    for condition in visibility:
        if condition.get("condition") == "or":
            found.extend(condition.get("conditions") or [])
        else:
            found.append(condition)
    return found


def _note(content: str) -> dict[str, Any]:
    return {"type": "markdown", "content": content, "grid_options": {"columns": "full"}}


def overview_sections(
    pump: Mapping[str, Any], text: Mapping[str, str], ha_version: tuple[int, int]
) -> list[dict[str, Any]]:
    """What the pump is doing right now: status, the near controls, a day, key figures."""
    build = _Builder(pump, text, ha_version)
    sections = [
        # The whole width: a line of chips reads as one line.
        _section(_heading(str(pump.get("name") or TITLE), ICON),
                 [build.chips(_CHIP_LINE)], column_span=4),
        _overrides_section(build, text),
        _section(_heading(text["quick"], "mdi:tune-variant"),
                 [build.controls(_QUICK_CONTROLS)]),
        # Twice the width of a column: a day of five lines, or of three bands
        # of what the pump did when, needs the room.
        *(_graph_section(build, text, graph_id) for graph_id in _OVERVIEW_GRAPHS),
        _section(_heading(text["readings"], "mdi:gauge"),
                 [build.readings(_KEY_READINGS)], column_span=4),
    ]
    return [section for section in sections if section]


def _overrides_section(build: _Builder, text: Mapping[str, str]) -> dict[str, Any] | None:
    """What Home Assistant holds in the pump, while it holds anything (R49).

    The count of held registers as a chip, and beside it the button that lets
    go of all of them, under a heading of their own. Both show only while the
    count is above zero, so the section, which hides when every card in it
    does, is not there at all while the pump follows its own settings: a
    keepalive that stops lets go of everything, and that is worth seeing at a
    glance, but an empty "nothing held" is not. Without the sensor, as with
    control switched off, there is no section.
    """
    entity_id = build.entity_id(_OVERRIDES)
    if entity_id is None:
        return None
    held = [{"condition": "numeric_state", "entity": entity_id, "above": 0}]
    chips = build.chips((_OVERRIDES,))
    if chips is None:
        return None
    for item in chips["items"]:
        item["color"] = _OVERRIDES_COLOUR
    chips["visibility"] = list(held)
    release = build.controls((_RELEASE_ALL,))
    if release is not None:
        release["visibility"] = list(held)
    return _section(_heading(text["overrides"], "mdi:hand-back-left-outline"), [chips, release])


def _graph_section(
    build: _Builder, text: Mapping[str, str], graph_id: str
) -> dict[str, Any] | None:
    """One graph under its heading, two columns wide, or None without entities."""
    _id, kind, keys = _graph(graph_id)
    return _section(_heading(text[graph_id], _graph_icon(graph_id, kind)),
                    [build.graph(graph_id, kind, keys)], column_span=2)


def controls_sections(
    pump: Mapping[str, Any], text: Mapping[str, str], ha_version: tuple[int, int]
) -> list[dict[str, Any]]:
    """Everything writable, grouped by what it does to the house, and the debug report last."""
    build = _Builder(pump, text, ha_version)
    sections = [
        _section(_heading(text[group], icon), [build.controls(keys)])
        for group, icon, keys in _CONTROL_GROUPS
    ]
    found = [section for section in sections if section]
    if not found:
        # Nothing writable: either control is off, or every control entity is.
        note = text["control_off"] if not pump.get("control_enabled") else text["no_controls"]
        found = [_section(_heading(text["control"], "mdi:tune-variant"), [_note(note)])]
    group, icon, keys = _TROUBLESHOOTING
    debug = _section(_heading(text[group], icon), [build.controls(keys)])
    return [*found, debug] if debug else found


def performance_sections(
    pump: Mapping[str, Any], text: Mapping[str, str], ha_version: tuple[int, int]
) -> list[dict[str, Any]]:
    """How the pump has run: the graphs, then every reading in its own section."""
    build = _Builder(pump, text, ha_version)
    sections: list[dict[str, Any] | None] = [
        _graph_section(build, text, graph_id) for graph_id in _PERFORMANCE_GRAPHS
    ]
    for section_id, icon, keys in _READINGS + _TECHNICAL:
        learnt = section_id in _LEARNT_SECTIONS
        sections.append(_section(
            _heading(text[section_id], icon),
            [build.rows(keys, hideable=True, learnt=learnt)],
        ))

    # What no list here knows of, from a newer integration than the page. A
    # display value whose page is no longer harvested stays in the entity
    # registry but never gets a value again, so it is left off altogether.
    leftovers = [
        key for key in build.entities
        if key not in _KNOWN_KEYS and not _DISPLAY_KEY.match(key)
    ]
    sections.append(_section(
        _heading(text["other"], "mdi:dots-horizontal"),
        [build.rows(leftovers, hideable=True, learnt=True)],
    ))
    return [section for section in sections if section]


def values_sections(
    pump: Mapping[str, Any], text: Mapping[str, str], ha_version: tuple[int, int]
) -> list[dict[str, Any]]:
    """Every value the installation offers, in one list with a filter over it.

    No value is left out here, whatever it reads and whatever it has only ever
    read: this is the tab you open to find out whether a value exists at all. A
    button is not a value, and is on the controls tab instead. What the card
    hides is only on screen (R39): a row with nothing to show waits behind the
    box for empty rows, and a search finds it there.
    """
    build = _Builder(pump, text, ha_version)
    rows: list[dict[str, Any]] = []

    def group(heading: str, keys: Iterable[str], trim: bool = False) -> None:
        found = [
            item for item in build._items(keys, hideable=False, trim=trim)
            if not str(item["entity"]).startswith(_BUTTON)
        ]
        if found:
            rows.append({"heading": heading})
            rows.extend(found)

    for group_id, _icon, keys in _VALUE_GROUPS:
        group(text[group_id], keys, trim=group_id == "control")
    group(text["other"], [
        key for key in build.entities
        if key not in _KNOWN_KEYS and not _DISPLAY_KEY.match(key)
    ])
    for page in build.pages:
        group(str(page.get("title") or TITLE),
              [value.get("key") for value in page.get("values") or [] if value.get("key")])

    if not rows:
        return []
    card = build.list_card(rows)
    card["filter"] = text["filter"]
    card["empty"] = text["empty"]
    # R39: a row with nothing to show, unavailable or unknown, is left out of
    # the list until this box is ticked, and a search still finds it.
    card["show_empty"] = text["show_empty"]
    card["count"] = text["count"]
    section = _section(_heading(text["values"], "mdi:format-list-bulleted"), [card],
                       column_span=4)
    return [section] if section else []


def _sort(pumps: Iterable[Any]) -> list[Mapping[str, Any]]:
    wanted = [p for p in pumps or [] if isinstance(p, Mapping)]
    return sorted(wanted, key=lambda p: str(p.get("name") or "").casefold())


def page_language(pumps: Iterable[Any], fallback: str | None) -> str:
    """The one language the whole page is built in, for every pump on it.

    The language the first pump carries, with the pumps in the page's own
    order (by name), or ``fallback`` while no pump runs. dashboard.py gives
    every pump Home Assistant's language (roadmap R23), so in a running Home
    Assistant this is that language whatever the pumps; a panel's own
    language only decides how its menu is read. The page and the section
    card (section_card.py, asked through websocket.py) both take it from
    here, so that a section reads on a dashboard of its own as it does on
    the page.
    """
    ordered = _sort(pumps)
    return language(ordered[0].get("language") if ordered else fallback)


def waiting_message(waiting: Iterable[Mapping[str, Any]], text: Mapping[str, str]) -> str:
    """What the page says while no pump runs: why, when a pump is being retried.

    A pump in setup_retry is not a pump that is switched off, it is one that
    did not answer, and Home Assistant says in what words (the entry's
    reason). Several pumps give one line each; none at all gives the plain
    note.
    """
    lines = []
    for pump in waiting or []:
        if not isinstance(pump, Mapping):
            continue
        name = str(pump.get("name") or TITLE)
        reason = str(pump.get("reason") or "").strip()
        lines.append(text["setup_retry"].format(name=name, reason=f" ({reason})" if reason else ""))
    return "\n\n".join(lines) if lines else text["not_running"]


def build_dashboard(
    pumps: Iterable[Mapping[str, Any]],
    fallback_language: str | None,
    ha_version: tuple[int, int],
    waiting: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The whole page: four tabs per heat pump, in the order the questions come.

    Every pump's tabs are in one language, the first pump's (page_language).
    ``waiting`` names the pumps Home Assistant is retrying, with the reason it
    gives, so that a page with no running pump can say why rather than that
    nothing is running.
    """
    pumps = _sort(pumps)
    lang = page_language(pumps, fallback_language)
    text = TEXT[lang]
    if not pumps:
        return message_dashboard(waiting_message(waiting, text), lang)

    several = len(pumps) > 1
    views: list[dict[str, Any]] = []
    for index, pump in enumerate(pumps, start=1):
        name = str(pump.get("name") or TITLE)
        suffix = f"-{index}" if several else ""
        tabs = [
            ("overview", overview_sections(pump, text, ha_version)),
            ("controls", controls_sections(pump, text, ha_version)),
            ("performance", performance_sections(pump, text, ha_version)),
            ("values", values_sections(pump, text, ha_version)),
        ]
        for tab, sections in tabs:
            if not sections:
                continue
            views.append({
                "title": f"{name}: {text[tab].lower()}" if several else text[tab],
                "path": f"{tab}{suffix}",
                "type": "sections",
                "max_columns": 4,
                "sections": sections,
            })
    return {"title": TITLE, "views": views}


def message_dashboard(message: str, lang: str | None) -> dict[str, Any]:
    """A page with nothing but a note on it, for when there is nothing else to show."""
    text = TEXT[language(lang)]
    return {
        "title": TITLE,
        "views": [{
            "title": text["overview"],
            "path": "overview",
            "type": "sections",
            "sections": [{
                "type": "grid",
                "cards": [_heading(TITLE, ICON), _note(message)],
            }],
        }],
    }


def entity_ids(config: Mapping[str, Any]) -> set[str]:
    """Every entity a built page refers to, looking inside cards, rows and conditions."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            entity = node.get("entity")
            if isinstance(entity, str):
                found.add(entity)
            # A graph names its entities in a list, and a statistics graph
            # names them as plain strings.
            for item in node.get("entities") or []:
                if isinstance(item, str):
                    found.add(item)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(config)
    return found
