"""Constants and register map for the CTC Local Easyconf integration.

Two transports are used side by side:

* Modbus TCP on port 502 is the primary source. It is documented by CTC in the
  BMS manual (162 600 16), it is side effect free and it always returns current
  values. It is also the only safe way to control the unit.
* The display's own web server on port 80 ("screen mirror", CTC Remote) is used
  for the values that Modbus does not expose at all, for example delivered heat
  in kW. Only the page the panel is currently showing is kept up to date there,
  so that transport is polled slowly and deliberately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

DOMAIN: Final = "ctc_ecozenith"

DEFAULT_MODBUS_PORT: Final = 502
DEFAULT_WEB_PORT: Final = 80
#: The display's own web interface, which the device's link in Home Assistant
#: points at. Its server answers the same page for any main.* address, on an
#: i255 as on an i550 Pro, and 400 for a bare /.
WEB_MAIN_PAGE: Final = "main.html"
DEFAULT_SLAVE: Final = 1

CONF_MODBUS_PORT: Final = "modbus_port"
CONF_WEB_PORT: Final = "web_port"
#: What an entry's device and entities are known by, in its data: the address
#: it was created with, kept when the address moves. An entry from before it
#: existed reads its address instead, see keys.device_key.
CONF_DEVICE_KEY: Final = "device_key"
CONF_SLAVE: Final = "slave"
#: Entry data: False for a heat pump set up on Modbus alone, because its
#: display's web interface did not answer when the address was typed in
#: (roadmap R11). Entries made before the key existed lack it, and all of
#: them have a display; see has_display.
CONF_DISPLAY: Final = "display"
#: Entry data: the family part of the display's settings file, "ezi2xx" of
#: settings_ezi2xx.bin, kept by entries made from here on (roadmap R19).
#: Older entries have only "settings_name", from which it can be taken.
CONF_SETTINGS_STEM: Final = "settings_stem"
CONF_LANGUAGE: Final = "language"
CONF_SLOW_PAGES: Final = "slow_pages"
#: The whole menu as it was last read, not only the pages being harvested, so
#: the tick boxes can be offered without walking the panel again.
CONF_MENU: Final = "menu"
#: The integration version that read the menu. A new version reads it again, so
#: pages and rows a newer parser understands are picked up by themselves.
CONF_MENU_VERSION: Final = "menu_version"
#: The operation data root's page number, as the menu reading found it. The
#: harvester steps back to it between two pages instead of going home for
#: each, and checks the number before it replays a route from there.
CONF_MENU_ROOT: Final = "menu_root"
#: Where the display keeps its system information and heat pump screens, once
#: found, so the identity is read from those two screens alone instead of a
#: sweep over the values of every screen in the map.
CONF_IDENTITY_SCREENS: Final = "identity_screens"
#: Whether the panel may be walked to the system information page once, to read
#: the serial number and the display's own software version.
CONF_VISIT_SYSTEM_INFO: Final = "visit_system_info"
#: Whether to ask GitHub once a day whether a newer release exists. A copy
#: installed by hand is invisible to Home Assistant's own update notices.
CONF_CHECK_UPDATES: Final = "check_updates"
RELEASES_API: Final = "https://api.github.com/repos/beolink/HA-CTC-Local-Easyconf/releases/latest"
RELEASES_PAGE: Final = "https://github.com/beolink/HA-CTC-Local-Easyconf/releases/latest"
CONF_SLOW_INTERVAL: Final = "slow_interval"
CONF_RESTORE_PAGE: Final = "restore_page"
CONF_ENABLE_CONTROL: Final = "enable_control"
CONF_FAST_INTERVAL: Final = "fast_interval"
# Must match OPTION_KEY in stats.py. Kept here so config_flow can build its
# schema without importing stats.py, which pulls in Home Assistant.
CONF_SEND_STATISTICS: Final = "send_statistics"
#: Whether the voluntary debug report goes out: what the diagnostics download
#: holds and the integration's own warnings and errors, every half hour, to
#: stats.rnet.se (debug_report.py). Off unless the owner switches it on, under
#: Configure or with its switch on the CTC page, which are the one setting;
#: once on it stays on until switched off. Applied in place, never by a
#: reload, and independent of the anonymous statistics.
CONF_DEBUG_REPORT: Final = "debug_report"

DEFAULT_FAST_INTERVAL: Final = 30
DEFAULT_SLOW_INTERVAL: Final = 1800
MIN_SLOW_INTERVAL: Final = 300

# The display speaks many languages. Index 0 is always English, which is what we
# match menu titles against, and index 1 is Swedish.
LANG_ENGLISH: Final = 0
LANG_SWEDISH: Final = 1

# English label of the "Driftinfo" tile on the home screen. The text id differs
# between models (532 on an i255, 570 on an i550 Pro) so the string is matched
# instead of the id.
OPERATION_DATA_LABEL_EN: Final = "Operation data"

# The walk to the system information page taps nothing but these, by their
# English label. The service menu next door holds a function test, a compressor
# quick start, reinstallation and a firmware update, and the panel is shared
# with whoever is standing at it, so anything unnamed is left alone.
SYSTEM_INFO_LABEL_EN: Final = "System information"
# "Installer" is what the panel calls Avancerat in English, and "Display setup"
# is the display menu under another name. Service is not here on purpose: the
# page has never been found under it on any model, and that menu is the one
# the walk exists to stay out of. The options texts list these menus, and a
# test derives what they may say from this tuple.
NAV_ALLOWED_EN: Final = (
    "Advanced",
    "Installer",
    "Display",
    "Display setup",
    "System information",
)
#: The menus the search for one page (page_search.py, roadmap L7) stays out
#: of, by the words the panel names them with, in English and in Swedish: a
#: control whose caption holds one of them as a word is never pressed, and a
#: page whose heading holds one is never searched, which the search learns
#: without a press for the page it is asked for and, for a page a tap leads
#: to, before anything on it is pressed. "Installer" and "Advanced" are
#: Avancerat, the menu that holds Service and Define (Definiera); "Language"
#: and "Språk" are the language selection. The search goes no further than
#: the operation data menu anyway; the words keep a caption that says where a
#: control leads from being pressed to find out.
NAV_FORBIDDEN: Final = (
    "Installer",
    "Advanced",
    "Avancerat",
    "Service",
    "Define",
    "Definiera",
    "Language",
    "Språk",
)
#: Whether the walk may also open the panel's own menu button on the home
#: screen, where an i550 Pro is thought to keep the system information page.
#: That is the one press made without a caption to go by, and it has never met
#: a real i550 Pro, so it sleeps: off by default and not offered in the options
#: form, to be switched on by hand for a trial with someone at the panel.
CONF_TRY_QUICK_MENU: Final = "try_quick_menu"
#: A caption this high up is the page's own heading, not a control: the
#: installer page is titled "Avancerat", which is "Installer" in English, and
#: pressing a heading does nothing at all. Same rule as the catalogue's.
PAGE_HEADER_HEIGHT: Final = 45
#: The header icon sits at the top left of a page, inside this many pixels
#: from the left edge, at x 5 on every page captured so far.
HEADER_ICON_MAX_X: Final = 60
#: How far apart, in pixels up or down, a caption and the reading or text
#: beside it may sit and still be the same row: on the pages the menu reading
#: walks, on the identity screens and beside the status field.
ROW_TOLERANCE: Final = 14
#: The panel's own control, top right. On a subpage it steps back; on the home
#: screen the same spot is a button of its own (the quick menu on an i550 Pro),
#: so it is never pressed as a way back on a page known to be home.
CHROME_BUTTON: Final = (440, 23)

#: The families the display's settings file names, by the stem of the file:
#: the model name chosen for each and the slug the daily report gives it. The
#: name is the member of the family that has the display with Modbus TCP,
#: since that is the only kind this integration can talk to at all. A family
#: joins the table once the fleet has shown its stem (roadmap R19); the names
#: discovery gives (discovery.MODEL_NAMES) and the report's closed list of
#: slugs (stats_extra.MODEL_SLUGS) are both read off it, so the two cannot
#: drift apart (roadmap L10).
MODEL_FAMILIES: Final = {
    "ezi2xx": ("EcoZenith i255", "i255"),
    "ezi3xx": ("EcoZenith i360", "i360"),
    "ezi5xx": ("EcoZenith i550 Pro", "i550"),
    "ecologic": ("EcoLogic", "ecologic"),
}

#: How the display prints an alarm out of its catalogue: "[E017] Givare
#: solpaneler ut". Information texts are printed "[I...]" and are no alarm.
ALARM_TEXT_PREFIX: Final = "[E"
#: The row name of the status field, casefolded, in the panel's own language:
#: the one text the panel fills from a variable that is read for an alarm
#: besides the header icon. The word is the same in Swedish, English, Danish
#: and Norwegian; a panel in another language has the header icon.
STATUS_CAPTIONS: Final = ("status",)
#: How many alarm episodes are kept, newest first.
ALARM_EPISODES: Final = 10

#: What makes a display row a period rather than a lifetime total: "Avgiven
#: värme/30 dagar" and "Avgiven energi/24h" sit right beside the totals on the
#: history page. A lifetime counter only grows; a period rises and falls as days
#: leave the window.
PERIOD_MARKERS: Final = ("/", "24", "30")

# CTC uses these raw values to say "no sensor fitted". They must never reach a
# sensor as a real reading.
SENTINELS: Final = frozenset({9999, -9999, 10000, -10000, 32767, -32768, 4294967295})

# The display's web server drops connections above roughly five in flight.
WEB_MAX_CONCURRENCY: Final = 3
#: How often to try again while the display is not answering, and how many
#: harvests in a row may fail before its readings are called unavailable.
RETRY_INTERVAL: Final = 300
HARVEST_PATIENCE: Final = 3
#: How many harvests in a row give way when the panel is not where the last
#: one left it, before the harvest goes ahead anyway. Somebody using the panel
#: is left alone for that long; a panel parked on another page, or left
#: somewhere by a restore that failed, does not stop the harvest for good.
HARVEST_SKIP_LIMIT: Final = 2

#: The event platform carries the heat pump's transitions (event.py). It has
#: been in Home Assistant since 2023.8, well inside hacs.json's floor. The
#: switch platform carries the debug report's one switch (switch.py).
PLATFORMS: Final = ["sensor", "binary_sensor", "number", "select", "button", "event", "switch"]

CONF_IDENTITY: Final = "identity"


def has_display(data: Any) -> bool:
    """Whether an entry has the display's web interface to read at all.

    Only an entry that says False does not: one set up on Modbus alone,
    because nothing answered on the web port. A missing key is an entry from
    before the key, and every one of those was set up through its display.
    """
    return data.get(CONF_DISPLAY, True) is not False


def identity_signal(entry_id: str) -> str:
    """The dispatcher signal sent when an entry's identity has been filled in.

    The serial number and the versions turn up late where the system
    information page had never been shown, and the entities that show them
    follow this signal rather than wait for a reload.
    """
    return f"{DOMAIN}_{entry_id}_identity"

#: English labels of the two lifetime counters that make a coefficient of
#: performance possible at all. Modbus carries the consumption side but not the
#: delivered side, so both have to come from the display.
#: The display's two instantaneous power rows, which live on the heat pump's own
#: page, matched on the label the display printed (text 1807 and 1810). The
#: catalogue strips a trailing unit from a row name, so "Avgiven värme (kW)" is
#: stored as "Avgiven värme" with the unit kW. The older Swedish name for
#: delivered heat is accepted too: the English string is the same in both
#: generations, the Swedish one is not.
LABEL_POWER_OUT_EN: Final = "energy output"
LABEL_POWER_OUT_SV: Final = "avgiven värme"
LABEL_POWER_OUT_SV_OLD: Final = "avgiven energi"
LABEL_POWER_IN_EN: Final = "energy add"
LABEL_POWER_IN_SV: Final = "tillförd effekt"

LABEL_ENERGY_OUT_EN: Final = "Energy output total"
LABEL_ENERGY_IN_EN: Final = "Energy consumption total"

#: Swedish equivalents, matched when the panel is not set to English.
LABEL_ENERGY_OUT_SV: Final = "Avgiven värme totalt"
LABEL_ENERGY_IN_SV: Final = "Tillförd energi totalt"

#: A year of daily samples, plus a margin so the oldest one is still there when
#: the newest arrives.
COP_HISTORY_DAYS: Final = 400
COP_WINDOW_DAYS: Final = 365


def web_interface_url(host: str, port: int = DEFAULT_WEB_PORT) -> str:
    """The address of the display's own web interface."""
    where = host if port == DEFAULT_WEB_PORT else f"{host}:{port}"
    return f"http://{where}/{WEB_MAIN_PAGE}"


@dataclass(frozen=True)
class ModbusSensor:
    """One readable Modbus holding register.

    ``key`` is the entity's key in its unique_id and its translation key: the
    entity is named in Home Assistant's language from the translations under
    it (roadmap R23). ``name`` is the Swedish name, which translations/sv.json
    carries word for word; an enum's labels are the controller's Swedish words
    and the entity's states in every language.
    """

    key: str
    address: int
    name: str
    scale: float = 1.0
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = "measurement"
    signed: bool = True
    count: int = 1
    enum: dict[int, str] | None = None
    enabled_default: bool = True
    icon: str | None = None
    #: Something about the unit rather than what it is doing, which Home
    #: Assistant files under the device's diagnostics.
    diagnostic: bool = False


#: The device class a unit implies, by the name sensor.py maps onto Home
#: Assistant's classes. Shared by the register table, which only names a class
#: where the unit does not give it, and the display rows, which never name one.
#: Hours and minutes are durations, so operating hours convert and graph as time.
UNIT_DEVICE_CLASSES: Final = {
    "°C": "temperature",
    "kW": "power",
    "kWh": "energy",
    "A": "current",
    "V": "voltage",
    "bar": "pressure",
    "h": "duration",
    "min": "duration",
}


def device_class_for(unit: str | None, declared: str | None = None) -> str | None:
    """The device class a reading gets: what the table says, else what its unit implies."""
    if declared:
        return declared
    return UNIT_DEVICE_CLASSES.get(unit or "")


STATUS_SYSTEM: Final = {
    0: "Värmepump övre",
    1: "Värmepump nedre",
    2: "Spetsvärme",
    3: "Värmepump och spets",
    4: "Värme",
    5: "Varmvatten",
    6: "Pool",
    7: "Kyla",
    8: "Av",
}

#: What an enum sensor reads when the controller answers with a code the table
#: has no label for. It has to be one of the sensor's own options: Home Assistant
#: refuses a state that is not, and then the sensor stops updating altogether.
#: The code itself goes in an attribute and once into the log.
STATUS_UNKNOWN: Final = "Okänd"

def enum_label(table: dict[int, str], value: int) -> tuple[str, int | None]:
    """The label for a code, plus the code itself when the table has none for it."""
    label = table.get(value)
    if label is not None:
        return label, None
    return STATUS_UNKNOWN, value


STATUS_HEATPUMP: Final = {
    0: "Kompressor av, startfördröjning",
    1: "Redo för start",
    2: "Väntar på flöde",
    3: "Till värme",
    4: "Avfrostning",
    5: "Till kyla",
    6: "Av, blockerad",
    7: "Av, larm",
    8: "Funktionstest",
    30: "Ej definierad",
    31: "Ej tillgänglig",
    32: "Kommunikationsfel",
    33: "Till varmvatten",
}

#: The heat pump status codes the derived binary sensors are built on, by what
#: they mean. The binaries judge the code, never the label: a label is a Swedish
#: string that may be reworded or translated, the code is what the controller
#: said. A test pins each code to its label, so a change in either is noticed.
HP_RUNNING_CODES: Final = frozenset({3, 5, 33})
HP_DEFROST_CODE: Final = 4
HP_BLOCKED_CODE: Final = 6
HP_ALARM_CODE: Final = 7

STATUS_HEATING_SYSTEM: Final = {
    0: "Värme av",
    1: "Semester",
    2: "Nattsänkning",
    3: "Till",
}

DHW_MODE: Final = {0: "Ekonomi", 1: "Normal", 2: "Komfort", 3: "Manuell"}
HEATING_MODE: Final = {0: "Auto", 1: "Till", 2: "Från"}
SG_MODE: Final = {0: "Normal", 1: "Blockering", 2: "Lågpris", 3: "Överkapacitet"}
#: SmartGrid is doing something whenever the mode is not this one.
SG_NORMAL_CODE: Final = 0
PRICE_MODE: Final = {1: "Låg", 2: "Normal", 3: "Hög"}
ZONE_MODE: Final = {0: "Av", 1: "Värme", 2: "Kyla", 3: "Auto", 4: "På"}
#: A stored setting that is a yes or a no, so it reads as a word rather than
#: the 1 the controller answers with. Whether the heat pump is allowed to run
#: (61521) is the one such setting in the 61500 block the integration reads.
YES_NO: Final = {0: "Nej", 1: "Ja"}

_T = "temperature"
_P = "power"
_E = "energy"


# Read only measurements, the 62000 block.
MODBUS_SENSORS: Final[tuple[ModbusSensor, ...]] = (
    ModbusSensor("outdoor_temp", 62000, "Utetemperatur", 0.1, "°C", _T),
    ModbusSensor("dhw_stop_temp", 62001, "Stopptemperatur varmvatten", 0.1, "°C", _T),
    # 62003 is documented as the hot water temperature but reads a constant 0 on
    # an i550 Pro, where 62276 is the live one. Created switched on like every
    # reading since 0.14.0; the page leaves it out where it has only read zero.
    ModbusSensor("dhw_temp_raw", 62003, "Varmvattentemperatur (62003)", 0.1, "°C", _T),
    ModbusSensor("system_status", 62005, "Systemstatus", 1, None, None, None, enum=STATUS_SYSTEM, icon="mdi:heat-pump"),
    ModbusSensor("radiator_temp", 62006, "Radiatorvatten", 0.1, "°C", _T),
    ModbusSensor("hs1_flow_setpoint", 62007, "Framledning börvärde VS1", 0.1, "°C", _T),
    ModbusSensor("hs1_flow", 62011, "Framledning VS1", 0.1, "°C", _T),
    ModbusSensor("return_temp", 62015, "Returtemperatur", 0.1, "°C", _T),
    ModbusSensor("dhw_circulation", 62016, "Varmvattencirkulation", 1, None, None, None, icon="mdi:water-sync"),
    ModbusSensor("hp1_status", 62017, "Värmepump status", 1, None, None, None, enum=STATUS_HEATPUMP, icon="mdi:heat-pump-outline"),
    ModbusSensor("hp1_in", 62027, "Värmepump in", 0.1, "°C", _T),
    ModbusSensor("hp1_out", 62037, "Värmepump ut", 0.1, "°C", _T),
    ModbusSensor("hp1_discharge", 62047, "Hetgas", 0.1, "°C", _T),
    ModbusSensor("hp1_suction", 62057, "Suggas", 0.1, "°C", _T),
    ModbusSensor("hp1_high_pressure", 62067, "Högtryck", 0.1, "bar", "pressure"),
    ModbusSensor("hp1_low_pressure", 62077, "Lågtryck", 0.1, "bar", "pressure"),
    ModbusSensor("hp1_brine_in", 62087, "Köldbärare in", 0.1, "°C", _T),
    ModbusSensor("hp1_brine_out", 62097, "Köldbärare ut", 0.1, "°C", _T),
    ModbusSensor("hp1_charge_pump", 62107, "Laddpump", 0.1, "%", None, icon="mdi:pump"),
    ModbusSensor("hp1_brine_pump", 62117, "Brinepump", 0.1, "%", None, icon="mdi:pump"),
    ModbusSensor("hp1_fan", 62127, "Fläkt", 0.1, "%", None, icon="mdi:fan"),
    ModbusSensor("hp1_defrost_timer", 62137, "Avfrostningstimer", 1, None, None, icon="mdi:timer-sand"),
    ModbusSensor("hp1_outdoor_temp", 62147, "Utetemperatur vid värmepump", 0.1, "°C", _T),
    # Degree minutes and hot water capacity are written in tenths, like the
    # temperatures: on an i360 the register read -3 447 and 846 while the panel's
    # own rows showed Gradminut -392.7 and Kapacitet 85.7 % (GitHub issue 1).
    ModbusSensor("degree_minutes", 62167, "Gradminuter", 0.1, None, None, icon="mdi:counter"),
    ModbusSensor("immersion_upper_kw", 62168, "Elpatron övre", 0.1, "kW", _P),
    ModbusSensor("immersion_lower_kw", 62169, "Elpatron nedre", 0.1, "kW", _P),
    ModbusSensor("current_l1", 62171, "Ström L1", 0.1, "A", "current"),
    ModbusSensor("current_l2", 62172, "Ström L2", 0.1, "A", "current"),
    ModbusSensor("current_l3", 62173, "Ström L3", 0.1, "A", "current"),
    # A lifetime counter in one word, so it is read without a sign: with one,
    # 32 769 kWh would come out as -32 767, which Home Assistant refuses for a
    # total that only grows. The temperatures around it need the sign; this is
    # the only one word register that can pass 32 767 at all.
    ModbusSensor("immersion_kwh", 62191, "Elpatron energi", 1, "kWh", _E, "total_increasing", signed=False),
    ModbusSensor("hp1_rps", 62193, "Kompressorvarvtal", 0.1, "rps", None, icon="mdi:speedometer"),
    ModbusSensor("room_temp_1", 62203, "Rumstemperatur", 0.1, "°C", _T),
    ModbusSensor("room_temp_2", 62204, "Rumstemperatur 2", 0.1, "°C", _T),
    ModbusSensor("compressor_hours", 62214, "Kompressordrifttid", 1, "h", None, "total_increasing", count=2, icon="mdi:timer-outline"),
    ModbusSensor("compressor_hours_24h", 62234, "Kompressordrift senaste dygnet", 1, "min", None, icon="mdi:timer-outline"),
    ModbusSensor("hs1_status", 62246, "Värmesystem status", 1, None, None, None, enum=STATUS_HEATING_SYSTEM, icon="mdi:radiator"),
    ModbusSensor("tank_lower_setpoint", 62274, "Nedre tank börvärde", 0.1, "°C", _T),
    ModbusSensor("dhw_lower_temp", 62275, "Varmvatten nedre", 0.1, "°C", _T),
    ModbusSensor("dhw_temp", 62276, "Varmvatten", 0.1, "°C", _T),
    ModbusSensor("dhw_capacity", 62279, "Varmvattenkapacitet", 0.1, "%", None, icon="mdi:water-percent"),
    # Pump and fan speeds carry one decimal: an EcoAir 720M running at 66.2 per
    # cent reports 662, which read as a whole number would be nonsense.
    ModbusSensor("sg_mode", 62301, "SmartGrid-läge", 1, None, None, None, enum=SG_MODE, icon="mdi:transmission-tower"),
    # The control unit's own software, which CTC reports as a number and a year
    # in two neighbouring registers. Diagnostics, so no state class, and filed
    # with the device's diagnostics like the display's own version rows.
    ModbusSensor("control_sw", 62244, "Programversion styrenhet", 1, None, None, None, icon="mdi:chip", diagnostic=True),
    ModbusSensor("control_sw_year", 62245, "Programversion styrenhet, år", 1, None, None, None, icon="mdi:chip", diagnostic=True),
    # 62331 is documented as supplied power per heat pump. On an i550 Pro it
    # reads 65.5 with the compressor stopped, which cannot be kilowatts, so the
    # explanation says the value is unconfirmed. The register number is the
    # source row's to show, as for every other Modbus row, not the name's: the
    # unique_id is built from the key, so the name is free to change.
    ModbusSensor("hp1_power", 62331, "Tillförd effekt värmepump", 0.1, "kW", _P),
    ModbusSensor("compressor_kwh", 62341, "Kompressorenergi", 1, "kWh", _E, "total_increasing", count=2),
)


# Stored settings, the 61500 block. Exposed read only. CTC warns explicitly that
# this block lives in EEPROM with a limited number of write cycles, so the
# integration never writes here.
MODBUS_SETTINGS: Final[tuple[ModbusSensor, ...]] = (
    ModbusSensor("set_dhw_mode", 61500, "Inställt varmvattenläge", 1, None, None, None, enum=DHW_MODE, icon="mdi:water-boiler"),
    ModbusSensor("set_extra_dhw", 61503, "Extra varmvatten kvar", 0.5, "h", None, icon="mdi:water-plus"),
    ModbusSensor("set_room_1", 61509, "Inställd rumstemperatur", 0.1, "°C", _T),
    ModbusSensor("set_slope_1", 61513, "Kurvlutning", 0.1, None, None, icon="mdi:chart-line-variant"),
    ModbusSensor("set_adjust_1", 61517, "Kurvjustering", 0.1, None, None, icon="mdi:tune-vertical"),
    ModbusSensor("set_hp1_blocked", 61521, "Värmepump tillåten", 1, None, None, None, enum=YES_NO, icon="mdi:heat-pump-outline"),
    ModbusSensor("set_heating_mode_1", 61542, "Inställt värmeläge", 1, None, None, None, enum=HEATING_MODE, icon="mdi:radiator"),
    ModbusSensor("set_max_rps_1", 61572, "Inställt max varvtal", 0.1, "rps", None, icon="mdi:speedometer"),
    ModbusSensor("set_max_immersion_lower", 61590, "Max elpatron nedre", 0.1, "kW", _P),
    ModbusSensor("set_max_immersion_upper", 61591, "Max elpatron övre", 0.1, "kW", _P),
)


@dataclass(frozen=True)
class ControlRegister:
    """A volatile control register from CTC's 1000 block.

    These are write only. They are not stored in EEPROM, they can be written as
    often as wanted, and the unit forgets them roughly five minutes after the
    last write. That expiry is the safety net: if Home Assistant stops, the heat
    pump returns to its own settings on its own.

    ``key`` is the entity's translation key, as for a ModbusSensor, and
    ``name`` its Swedish name, word for word in translations/sv.json; every
    Swedish name starts with "Styr" and every English one with "Control",
    which the CTC page leaves out under a heading that already says control.
    """

    key: str
    address: int
    name: str
    scale: float = 1.0
    unit: str | None = None
    minimum: float = 0
    maximum: float = 100
    step: float = 1
    enum: dict[int, str] | None = None
    mirror: int | None = None  # register holding the unit's own setting
    icon: str | None = None


CONTROL_NUMBERS: Final[tuple[ControlRegister, ...]] = (
    ControlRegister("ctl_max_rps", 1002, "Styr max varvtal", 0.1, "rps", 0, 120, 1, mirror=61572, icon="mdi:speedometer"),
    ControlRegister("ctl_immersion_lower", 1003, "Styr max elpatron nedre", 0.1, "kW", 0, 9, 0.1, mirror=61590, icon="mdi:heating-coil"),
    ControlRegister("ctl_immersion_upper", 1004, "Styr max elpatron övre", 0.1, "kW", 0, 9, 0.1, mirror=61591, icon="mdi:heating-coil"),
    ControlRegister("ctl_room_setpoint_1", 1010, "Styr rumsbörvärde", 0.1, "°C", 10, 30, 0.1, mirror=61509, icon="mdi:home-thermometer"),
    ControlRegister("ctl_dhw_setpoint", 1033, "Styr varmvattenbörvärde", 0.1, "°C", 30, 65, 1, icon="mdi:water-thermometer"),
    # No mirror (roadmap R50). 61503, Extra varmvatten kvar, stood at 0.0 for
    # seven minutes on an i255 while 1006 was held at one hour and every write
    # reached the unit, so it is not the pump's own value of this control and
    # the number shows only what Home Assistant holds. Whether the controller
    # starts its timer again at each keepalive rewrite is not known.
    ControlRegister("ctl_extra_dhw", 1006, "Styr extra varmvatten", 0.5, "h", 0, 12, 0.5, icon="mdi:water-plus"),
)

CONTROL_SELECTS: Final[tuple[ControlRegister, ...]] = (
    ControlRegister("ctl_price_mode", 1005, "Styr elprisläge", enum=PRICE_MODE, icon="mdi:cash-clock"),
    ControlRegister("ctl_dhw_mode", 1007, "Styr varmvattenläge", enum=DHW_MODE, mirror=61500, icon="mdi:water-boiler"),
    ControlRegister("ctl_zone_mode_1", 1015, "Styr driftläge VS1", enum=ZONE_MODE, icon="mdi:radiator"),
)

# Virtual digital inputs. Bit 0 to 7 map to "BMS Di 0" to "BMS Di 7" which are
# assigned to functions such as SmartGrid A and B in the unit's own
# "Definiera / Fjärrstyrning" menu. Nothing writes this register: no entity,
# service or manager uses it, and it has never been tried against a unit, so
# it is not among the addresses the write path lets through. The day a
# function needs it, it comes in with its own entity and its own test.
CONTROL_VDI_REGISTER: Final = 1100

#: Every address the integration may ever write: exactly the control tables
#: above, derived rather than typed out, so a new ControlRegister is let
#: through by itself and nothing else ever is: the Modbus client refuses any
#: other address before it takes the connection. The 61500 block lives in
#: EEPROM with a limited number of write cycles, and a write that lands there
#: by mistake cannot be undone, so the promise that it is never written is kept
#: here and proved by the tests instead of resting on convention.
CONTROL_ADDRESSES: Final[frozenset[int]] = frozenset(
    register.address for register in CONTROL_NUMBERS + CONTROL_SELECTS
)

#: The control registers by entity key, for the release action (roadmap R76),
#: which is handed an entity and finds its register from the key in the
#: entity's unique_id. Derived like the addresses, so it holds exactly the
#: registers the tables hold.
CONTROL_BY_KEY: Final[dict[str, ControlRegister]] = {
    register.key: register for register in CONTROL_NUMBERS + CONTROL_SELECTS
}

# How often the volatile control registers are refreshed. CTC requires at least
# every five minutes; a minute leaves a wide margin.
CONTROL_KEEPALIVE_SECONDS: Final = 60
#: How long the controller holds a control register after the last write that
#: reached it. Once an address has gone this long without a successful write
#: the unit has forgotten it, and Home Assistant stops saying it is in force.
CONTROL_EXPIRY_SECONDS: Final = 300


@dataclass
class SlowValue:
    """One value harvested from the display's web interface."""

    key: str
    label: str
    page: int
    screen: int
    fmt: str
    var_indices: list[int] = field(default_factory=list)
    unit: str | None = None
    scale: float = 1.0
    #: The key the row had in the menu this one replaced, where that was
    #: another: what set-up carries the row's entity and stored values over
    #: from (keys.previous_keys, roadmap L2).
    previous_key: str | None = None


@dataclass
class SlowPage:
    """A menu page of the display that can be polled as a unit.

    ``route`` is the sequence of taps that reaches this page from the operation
    data root, recorded while the menu was explored. Replaying a known route is
    far more robust than trying to work the layout out again at poll time, since
    an i255 and an i550 Pro lay their operation data pages out differently.
    """

    page: int
    title: str
    screens: list[int] = field(default_factory=list)
    values: list[SlowValue] = field(default_factory=list)
    route: list[tuple[int, int]] = field(default_factory=list)
