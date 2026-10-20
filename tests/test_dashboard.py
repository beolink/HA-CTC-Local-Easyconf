"""The sidebar page's layout (dashboard_views.py), checked without Home Assistant."""

import json
import re

from conftest import COMPONENT, load

NEW_HA = (2026, 9)
OLD_HA = (2024, 12)
HIDDEN = {"condition": "state", "state_not": ["unavailable", "unknown"]}


def _headings(sections):
    return [s["cards"][0]["heading"] for s in sections]


def _section(sections, heading):
    return next(s for s in sections if s["cards"][0]["heading"] == heading)


CHIPS = "custom:ctc-ecozenith-chips"
READINGS = "custom:ctc-ecozenith-readings"
CONTROLS = "custom:ctc-ecozenith-controls"
ROWS = "custom:ctc-ecozenith-rows"
KINDS = {CHIPS: "chip", READINGS: "reading", CONTROLS: "control", ROWS: "row"}
VALUE_CARDS = tuple(KINDS)


def _cards(section):
    """The cards of a section that carry values, the heading aside."""
    return [card for card in section["cards"][1:] if card["type"] in KINDS]


def _items(section):
    """(kind, value, entity_id) in reading order."""
    found = []
    for card in _cards(section):
        for item in card.get("items", card.get("rows", [])):
            if "entity" in item:
                found.append((KINDS[card["type"]], item, item["entity"]))
    return found


def _keys(pump, section, kind=None):
    by_entity = {entity_id: key for key, entity_id in pump["entities"].items()}
    return [by_entity[e] for k, _c, e in _items(section) if kind in (None, k)]


def _tab(dashboard_views, pump, tab, version=NEW_HA, lang="sv"):
    """The sections of one tab, built on their own."""
    build = getattr(dashboard_views, f"{tab}_sections")
    return build(pump, dashboard_views.TEXT[lang], version)


def _view(config, path):
    return next(v for v in config["views"] if v["path"] == path)


def _values_card(config, path="values"):
    (section,) = _view(config, path)["sections"]
    (card,) = section["cards"][1:]
    return card


# --------------------------------------------------------------------- tabs


def test_a_pump_gets_the_four_tabs_in_the_order_the_questions_come(dashboard_views, pumps):
    config = dashboard_views.build_dashboard([pumps["vsh"]], "en", NEW_HA)
    # The sidebar says CTC, and the pump's own name heads its status.
    assert config["title"] == "CTC"
    assert [(v["title"], v["path"], v["type"]) for v in config["views"]] == [
        ("Översikt", "overview", "sections"),
        ("Styrning", "controls", "sections"),
        ("Prestanda", "performance", "sections"),
        ("Alla värden", "values", "sections"),
    ]


def test_several_pumps_get_four_tabs_each_sorted_by_name(dashboard_views, pumps):
    config = dashboard_views.build_dashboard([pumps["vsh"], pumps["pt"], None], "sv", NEW_HA)
    assert [(v["title"], v["path"]) for v in config["views"]] == [
        ("CTC EcoZenith i255: översikt", "overview-1"),
        ("CTC EcoZenith i255: styrning", "controls-1"),
        ("CTC EcoZenith i255: prestanda", "performance-1"),
        ("CTC EcoZenith i255: alla värden", "values-1"),
        ("CTC EcoZenith i550 Pro: översikt", "overview-2"),
        ("CTC EcoZenith i550 Pro: styrning", "controls-2"),
        ("CTC EcoZenith i550 Pro: prestanda", "performance-2"),
        ("CTC EcoZenith i550 Pro: alla värden", "values-2"),
    ]


def test_headings_follow_the_panel_language(dashboard_views, pumps):
    pump = pumps["vsh"]
    pump["language"] = "en"
    config = dashboard_views.build_dashboard([pump], "sv", NEW_HA)
    assert [v["title"] for v in config["views"]] == [
        "Overview", "Controls", "Performance", "All values"
    ]
    headings = _headings(_view(config, "performance")["sections"])
    assert "Temperatures" in headings and "Energy and COP" in headings


# ----------------------------------------------------------------- overview


def test_the_overview_says_what_the_pump_is_doing_right_now(dashboard_views, pumps):
    pump = pumps["vsh"]
    sections = _tab(dashboard_views, pump, "overview")
    assert _headings(sections) == [
        "CTC EcoZenith i255", "Snabbstyrning",
        "Temperaturer det senaste dygnet", "Pumpen det senaste dygnet", "Nyckeltal",
    ]
    status = sections[0]
    # One line of chips, and the whole width to read it on.
    assert status["column_span"] == 4
    assert [c["type"] for c in _cards(status)] == [CHIPS]
    assert _keys(pump, status, "chip") == [
        "system_status", "hp1_status", "compressor_running", "defrosting",
        "immersion_active", "display_alarm", "blocked", "smartgrid_active", "hs1_status", "sg_mode",
    ]
    # Status stays on the page whatever it reads: an unavailable pump is news.
    assert "visibility" not in status
    assert all("visibility" not in card for card in status["cards"])
    assert all("hide_unavailable" not in item for _k, item, _e in _items(status))


def test_boolean_chips_are_there_only_while_on_and_in_the_colour_of_what_they_mean(
    dashboard_views, pumps
):
    """R36: a raised alarm reads from the door, and a row of "Av" is not there to read."""
    pump = pumps["vsh"]
    status = _tab(dashboard_views, pump, "overview")[0]
    by_key = {
        key: item
        for key, (_k, item, _e) in zip(_keys(pump, status), _items(status), strict=True)
    }
    # The theme's colours, by name: the card turns "error" into --error-color.
    assert {key: item.get("color") for key, item in by_key.items() if "on_state" in item} == {
        "display_alarm": "error",
        "blocked": "warning",
        "smartgrid_active": "warning",
        "defrosting": "info",
        "compressor_running": "success",
        "immersion_active": "success",
    }
    assert all(item["on_state"] == "on" for item in by_key.values() if "on_state" in item)
    # The display's alarm reads the code and text the panel prints while it is on.
    assert by_key["display_alarm"]["value_attribute"] == ["visat", "shown"]
    # The enum chips stay as they are and say what they read, whatever it is.
    for key in ("system_status", "hp1_status", "hs1_status", "sg_mode"):
        assert "on_state" not in by_key[key] and "color" not in by_key[key]
    # Every boolean on the line is coloured; the colours are the theme's four.
    assert dashboard_views._CHIP_COLOURS.keys() == {
        key for key, item in by_key.items() if item["entity"].startswith("binary_sensor.")
    }
    assert set(dashboard_views._CHIP_COLOURS.values()) == {"error", "warning", "info", "success"}


def test_the_overview_carries_the_controls_worth_reaching_for(dashboard_views, pumps):
    pump = pumps["vsh"]
    quick = _section(_tab(dashboard_views, pump, "overview"), "Snabbstyrning")
    assert _keys(pump, quick) == [
        "ctl_room_setpoint_1", "ctl_dhw_mode", "ctl_extra_dhw", "ctl_price_mode"
    ]
    names = [item["name"] for _k, item, _e in _items(quick)]
    assert names == ["Rumsbörvärde", "Varmvattenläge", "Extra varmvatten", "Elprisläge"]
    # The card draws the control itself and needs a word for a button.
    (card,) = _cards(quick)
    assert card["type"] == CONTROLS and card["press"] == "Utför"


def test_a_pump_without_control_has_no_quick_controls(dashboard_views, pumps):
    assert "Snabbstyrning" not in _headings(_tab(dashboard_views, pumps["pt"], "overview"))


def test_the_key_readings_are_large_numbers_that_hide_while_they_have_nothing(
    dashboard_views, pumps
):
    pump = pumps["vsh"]
    readings = _section(_tab(dashboard_views, pump, "overview"), "Nyckeltal")
    assert readings["column_span"] == 4
    assert _keys(pump, readings) == [
        "outdoor_temp", "room_temp_1", "dhw_temp", "hs1_flow", "return_temp",
        "hp1_rps", "p22_avgiven_varme", "p22_tillford_effekt", "cop_day", "degree_minutes",
    ]
    (card,) = _cards(readings)
    assert card["type"] == READINGS
    items = dict(
        zip(_keys(pump, readings), (i for _k, i, _e in _items(readings)), strict=True)
    )
    # The coefficient of performance is the one figure that stays while merely
    # unknown, with its reason under it (R35); everything else hides.
    assert all(item["hide_unavailable"] for key, item in items.items() if key != "cop_day")
    assert items["cop_day"]["show_reason"] is True
    # The card goes when every figure on it has.
    (either,) = card["visibility"]
    assert len(either["conditions"]) == len(_items(readings))


def test_the_coefficient_of_performance_says_what_it_rests_on_and_why_it_is_empty(
    dashboard_views, pumps
):
    """R35: underlag and skäl under the figure, and a tile that stays while merely unknown."""
    pump = pumps["vsh"]
    for tab, heading, other in (
        ("overview", "Nyckeltal", "outdoor_temp"),
        ("performance", "Energi och värmefaktor", "compressor_kwh"),
    ):
        section = _section(_tab(dashboard_views, pump, tab), heading)
        items = dict(
            zip(_keys(pump, section), (i for _k, i, _e in _items(section)), strict=True)
        )
        for key, item in items.items():
            if key.startswith("cop_"):
                # Today's attribute names and the English ones they may get, so
                # a renaming in the integration does not blank the line.
                assert item["sub"] == ["underlag", "basis"]
                assert item["reason"] == ["skäl", "reason"]
                assert item["show_reason"] is True and "hide_unavailable" not in item
            else:
                assert not {"sub", "reason", "show_reason"} & item.keys()
        # The card's visibility follows: unknown keeps the coefficient on the page.
        (card,) = _cards(section)
        conditions = {c["entity"]: c["state_not"] for c in card["visibility"][0]["conditions"]}
        assert conditions[pump["entities"]["cop_day"]] == ["unavailable"]
        assert conditions[pump["entities"][other]] == ["unavailable", "unknown"]
    # The graph of it is drawn for the same states.
    graph = _section(_tab(dashboard_views, pump, "performance"), "Värmefaktor per dygn")["cards"][1]
    assert graph["visibility"] == [
        {"condition": "state", "entity": pump["entities"]["cop_day"], "state_not": ["unavailable"]}
    ]
    # The full list never hides a row, and still writes the line under it.
    config = dashboard_views.build_dashboard([pump], "sv", NEW_HA)
    rows = {row["entity"]: row for row in _values_card(config)["rows"] if "entity" in row}
    cop = rows[pump["entities"]["cop_lifetime"]]
    assert cop["sub"] == ["underlag", "basis"] and cop["reason"] == ["skäl", "reason"]
    assert "show_reason" not in cop and "hide_unavailable" not in cop


def test_the_overview_ends_in_a_day_of_the_circuit(dashboard_views, pumps):
    pump = pumps["vsh"]
    graph = _section(_tab(dashboard_views, pump, "overview"), "Temperaturer det senaste dygnet")
    (card,) = graph["cards"][1:]
    assert card["type"] == "history-graph"
    assert card["hours_to_show"] == 24
    by_entity = {entity_id: key for key, entity_id in pump["entities"].items()}
    assert [by_entity[e["entity"]] for e in card["entities"]] == [
        "outdoor_temp", "hs1_flow", "return_temp", "dhw_temp", "room_temp_1"
    ]
    # The graph names its lines itself, so no device name is repeated five times.
    assert card["entities"][0]["name"] == "Utetemperatur"


def test_the_overview_shows_what_the_pump_did_when(dashboard_views, pumps):
    """R38: the statuses and the heater as timeline bands under the temperatures."""
    pump = pumps["vsh"]
    sections = _tab(dashboard_views, pump, "overview")
    headings = _headings(sections)
    assert headings.index("Pumpen det senaste dygnet") == headings.index(
        "Temperaturer det senaste dygnet"
    ) + 1
    graph = _section(sections, "Pumpen det senaste dygnet")
    assert graph["column_span"] == 2
    (card,) = graph["cards"][1:]
    assert card["type"] == "history-graph" and card["hours_to_show"] == 24
    by_entity = {entity_id: key for key, entity_id in pump["entities"].items()}
    # The registers themselves, not the binaries derived from hp1_status,
    # which would be four bands of the same register.
    assert [by_entity[e["entity"]] for e in card["entities"]] == [
        "hp1_status", "system_status", "immersion_active",
    ]
    assert [e["name"] for e in card["entities"]] == [
        "Värmepump status", "Systemstatus", "Elpatron aktiv",
    ]
    # No SmartGrid band: its state is a label, never zero, so the rule for a
    # reading only ever zero could not tell a pump that never uses it from one
    # that does, and the band lay flat as "Normal" on both. The mode is a chip.
    assert pump["entities"]["sg_mode"] not in json.dumps(graph)
    chips = _section(sections, pump["name"])["cards"][1]
    assert pump["entities"]["sg_mode"] in {item["entity"] for item in chips["items"]}


def test_a_graph_is_left_out_when_the_pump_has_none_of_its_readings(dashboard_views, pumps):
    pump = pumps["vsh"]
    for key in ("outdoor_temp", "hs1_flow", "return_temp", "dhw_temp", "room_temp_1"):
        pump["entities"].pop(key, None)
    assert "Temperaturer det senaste dygnet" not in _headings(
        _tab(dashboard_views, pump, "overview")
    )


# ----------------------------------------------------------------- controls


def test_controls_are_grouped_by_what_they_do_to_the_house(dashboard_views, pumps):
    pump = pumps["vsh"]
    pump["entities"]["release_control"] = "button.ctc_ecozenith_i255_slapp_all_styrning"
    pump["names"]["release_control"] = "Släpp all styrning"
    sections = _tab(dashboard_views, pump, "controls")
    # Three groups, no more: the button that lets go of everything is the last
    # row of the power group, and its note went into its explanation (V5).
    assert _headings(sections) == ["Värme", "Varmvatten", "Drift och el"]
    assert _keys(pump, _section(sections, "Värme")) == ["ctl_room_setpoint_1", "ctl_zone_mode_1"]
    assert _keys(pump, _section(sections, "Varmvatten")) == [
        "ctl_dhw_mode", "ctl_dhw_setpoint", "ctl_extra_dhw"
    ]
    assert _keys(pump, _section(sections, "Drift och el")) == [
        "ctl_price_mode", "ctl_max_rps", "ctl_immersion_lower", "ctl_immersion_upper",
        "release_control",
    ]
    assert not [c for s in sections for c in s["cards"] if c["type"] == "markdown"]


def test_a_control_says_what_it_is_and_is_never_hidden(dashboard_views, pumps):
    pump = pumps["vsh"]
    pump["entities"]["release_control"] = "button.ctc_ecozenith_i255_slapp_all_styrning"
    pump["names"]["release_control"] = "Släpp all styrning"
    sections = _tab(dashboard_views, pump, "controls")
    by_key = {}
    for section in sections:
        by_key.update(
            zip(_keys(pump, section), (i for _k, i, _e in _items(section)), strict=True)
        )
    # The heading already says control, so the name drops the "Styr".
    assert by_key["ctl_room_setpoint_1"]["name"] == "Rumsbörvärde"
    assert by_key["ctl_immersion_lower"]["name"] == "Max elpatron nedre"
    assert by_key["release_control"]["name"] == "Släpp all styrning"
    # Every control is explained, and the card decides from the entity itself
    # whether it is a list, a slider or a field.
    assert all(item.get("explanation") and item.get("source") for item in by_key.values())
    assert all("hide_unavailable" not in item for item in by_key.values())
    # A control is never hidden: an unavailable one says something is wrong.
    assert all("visibility" not in c for s in sections for c in _cards(s))


def test_without_control_the_tab_says_how_to_turn_it_on(dashboard_views, pumps):
    (section,) = _tab(dashboard_views, pumps["pt"], "controls")
    assert section["cards"][0]["heading"] == "Styrning"
    (note,) = section["cards"][1:]
    assert note["type"] == "markdown"
    assert "Tillåt styrning av värmepumpen" in note["content"]
    assert "/config/integrations/integration/ctc_ecozenith" in note["content"]


def test_control_switched_on_but_every_control_disabled_says_so(dashboard_views, pumps):
    pump = pumps["vsh"]
    for key in [k for k in pump["entities"] if k.startswith("ctl_")]:
        del pump["entities"][key]
    (section,) = _tab(dashboard_views, pump, "controls")
    (note,) = section["cards"][1:]
    assert "inga reglage är aktiverade" in note["content"]


# -------------------------------------------------------------- performance


def test_performance_opens_with_the_graphs_and_then_every_reading(dashboard_views, pumps):
    pump = pumps["vsh"]
    sections = _tab(dashboard_views, pump, "performance")
    assert _headings(sections) == [
        "Framledning och börvärde det senaste dygnet",
        "Kompressorn det senaste dygnet",
        "Energi per dygn",
        "Kompressorns gångtid per dygn",
        "Värmefaktor per dygn",
        "Temperaturer",
        "Varmvatten",
        "Energi och värmefaktor",
        "Kompressor och köldkrets",
        "Pumpens inställningar",
        "Om enheten",
    ]
    assert all(s.get("column_span") == 2 for s in sections[:5])
    energy = _section(sections, "Energi per dygn")["cards"][1]
    assert energy["type"] == "statistics-graph"
    assert energy["chart_type"] == "bar" and energy["stat_types"] == ["change"]
    assert energy["period"] == "day" and energy["days_to_show"] == 30
    assert energy["entities"] == [
        pump["entities"][key]
        for key in ("p30_avgiven_varme_totalt", "compressor_kwh", "immersion_kwh")
    ]
    # A bar per day: each day is one number, and a line drew values between
    # the days that were never there (V4).
    cop = _section(sections, "Värmefaktor per dygn")["cards"][1]
    assert cop["chart_type"] == "bar" and cop["stat_types"] == ["mean"]
    assert cop["entities"] == [pump["entities"]["cop_day"]]


def test_the_compressors_running_time_per_day_is_a_bar_per_day_and_goes_where_it_is_zero(
    dashboard_views, pumps
):
    """R38: 62214 only grows, so its change per day is the hours the compressor ran."""
    pump = pumps["vsh"]
    sections = _tab(dashboard_views, pump, "performance")
    headings = _headings(sections)
    assert headings.index("Kompressorns gångtid per dygn") == headings.index("Energi per dygn") + 1
    runtime = _section(sections, "Kompressorns gångtid per dygn")["cards"][1]
    assert runtime["type"] == "statistics-graph"
    assert runtime["chart_type"] == "bar" and runtime["stat_types"] == ["change"]
    assert runtime["period"] == "day" and runtime["days_to_show"] == 30
    assert runtime["entities"] == [pump["entities"]["compressor_hours"]]
    # An i550 Pro whose compressor hours have only ever been zero gets no graph
    # of them, the same rule that keeps a flat zero out of the sections.
    pump["unused"] = ["compressor_hours"]
    assert "Kompressorns gångtid per dygn" not in _headings(
        _tab(dashboard_views, pump, "performance")
    )


def test_performance_opens_with_the_flow_against_its_setpoint_not_the_overviews_graph(
    dashboard_views, pumps
):
    """V4: a graph that adds something, instead of the one the overview just showed."""
    pump = pumps["vsh"]
    sections = _tab(dashboard_views, pump, "performance")
    assert "Temperaturer det senaste dygnet" not in _headings(sections)
    (card,) = _section(sections, "Framledning och börvärde det senaste dygnet")["cards"][1:]
    assert card["type"] == "history-graph" and card["hours_to_show"] == 24
    by_entity = {entity_id: key for key, entity_id in pump["entities"].items()}
    assert [by_entity[e["entity"]] for e in card["entities"]] == [
        "hs1_flow", "hs1_flow_setpoint", "outdoor_temp",
    ]
    assert card["entities"][1]["name"] == "Framledning börvärde VS1"
    # The overview keeps the circuit's five lines.
    assert "Temperaturer det senaste dygnet" in _headings(_tab(dashboard_views, pump, "overview"))


def test_the_weeks_and_months_coefficient_take_their_place_when_the_pump_has_them(
    dashboard_views, pumps
):
    """V4 and R25: the page has the place before the sensors exist, and shows nothing
    until they do."""
    pump = pumps["vsh"]
    assert not {"cop_week", "cop_month"} & pump["entities"].keys()
    overview = _tab(dashboard_views, pump, "overview")
    assert "cop_week" not in _keys(pump, _section(overview, "Nyckeltal"))
    cop = _section(_tab(dashboard_views, pump, "performance"), "Värmefaktor per dygn")["cards"][1]
    assert cop["entities"] == [pump["entities"]["cop_day"]]

    pump["entities"]["cop_week"] = "sensor.ctc_ecozenith_i255_veckovarmefaktor"
    pump["entities"]["cop_month"] = "sensor.ctc_ecozenith_i255_manadsvarmefaktor"
    pump["names"]["cop_week"] = "Veckovärmefaktor"
    pump["names"]["cop_month"] = "Månadsvärmefaktor"
    readings = _section(_tab(dashboard_views, pump, "overview"), "Nyckeltal")
    keys = _keys(pump, readings)
    assert keys[keys.index("cop_day"):][:3] == ["cop_day", "cop_week", "cop_month"]
    performance = _tab(dashboard_views, pump, "performance")
    energy = _keys(pump, _section(performance, "Energi och värmefaktor"))
    assert energy[:4] == ["cop_day", "cop_week", "cop_month", "cop_year"]
    # The month's figure as the second series of the daily bars.
    cop = _section(performance, "Värmefaktor per dygn")["cards"][1]
    assert cop["entities"] == [pump["entities"]["cop_day"], pump["entities"]["cop_month"]]
    # Both are explained, say what they rest on, and are listed with the rest.
    items = {key: item for key, (_k, item, _e) in zip(keys, _items(readings), strict=True)}
    for key in ("cop_week", "cop_month"):
        assert items[key]["explanation"].startswith("Värmefaktor")
        assert items[key]["sub"] == ["underlag", "basis"] and items[key]["show_reason"] is True
    config = dashboard_views.build_dashboard([pump], "sv", NEW_HA)
    listed = {row["entity"] for row in _values_card(config)["rows"] if "entity" in row}
    assert {pump["entities"]["cop_week"], pump["entities"]["cop_month"]} <= listed
    assert "Övrigt" not in _headings(_view(config, "performance")["sections"])


def test_readings_hide_while_they_have_nothing_to_show(dashboard_views, pumps):
    pump = pumps["vsh"]
    temperatures = _section(_tab(dashboard_views, pump, "performance"), "Temperaturer")
    assert all(item["hide_unavailable"] is True for _k, item, _e in _items(temperatures))
    (rows_card,) = _cards(temperatures)
    assert rows_card["type"] == ROWS
    (either,) = rows_card["visibility"]
    assert either["condition"] == "or"
    # The whole section goes when every value in it has.
    (section_either,) = temperatures["visibility"]
    assert len(section_either["conditions"]) == len(_items(temperatures))


def test_every_card_of_values_fills_the_section_it_stands_in(dashboard_views, pumps):
    pump = pumps["vsh"]
    sections = _tab(dashboard_views, pump, "performance")
    cards = [c for s in sections for c in _cards(s)]
    assert cards and all(c["grid_options"] == {"columns": "full"} for c in cards)
    temperatures = _section(sections, "Temperaturer")
    assert _keys(pump, temperatures)[:4] == [
        "outdoor_temp", "room_temp_1", "set_room_1", "hs1_flow"
    ]
    rows = {row["name"] for _k, row, _e in _items(temperatures)}
    assert "Framledning börvärde VS1" in rows


def test_the_energy_section_picks_the_display_rows_out_by_label(dashboard_views, pumps):
    pump = pumps["vsh"]
    energy = _section(_tab(dashboard_views, pump, "performance"), "Energi och värmefaktor")
    keys = _keys(pump, energy)
    assert keys[:8] == [
        "cop_day", "cop_year", "cop_first_year", "cop_lifetime",
        "p22_avgiven_varme", "p22_tillford_effekt",
        "p30_avgiven_varme_totalt", "compressor_kwh",
    ]
    # Modbus 62341 counts the same consumed energy, and fresher.
    assert "p30_tillford_energi_totalt" not in keys
    # Periods are no lifetime counters.
    assert not [k for k in keys if "30_dagar" in k]


def test_display_roles_in_english_and_without_modbus(dashboard_views):
    pump = {
        "entities": {"p22_energy_output": "sensor.a", "p22_energy_add": "sensor.b"},
        "pages": [{"title": "Heat pump", "values": [
            {"key": "p22_energy_output", "label": "Energy output", "unit": "kW"},
            {"key": "p22_energy_add", "label": "Energy add", "unit": "kW"},
            {"key": "p22_energy_output_24", "label": "Energy output/24h", "unit": "kW"},
        ]}],
        "energy_out": "p30_out",
        "energy_in": "p30_in",
    }
    assert dashboard_views.display_roles(pump) == {
        dashboard_views.HEAT_POWER: "p22_energy_output",
        dashboard_views.SUPPLIED_POWER: "p22_energy_add",
        dashboard_views.ENERGY_OUT: "p30_out",
        dashboard_views.ENERGY_IN: "p30_in",
    }


def test_entities_nobody_placed_end_up_under_other_and_are_still_explained(dashboard_views, pumps):
    pump = pumps["vsh"]
    pump["entities"]["something_new"] = "sensor.ctc_ecozenith_i255_something_new"
    pump["names"]["something_new"] = "Något nytt"
    other = _section(_tab(dashboard_views, pump, "performance"), "Övrigt")
    # Display values have their own place and do not spill into it.
    assert _keys(pump, other) == ["something_new"]
    (_kind, row, _entity), = _items(other)
    assert row["explanation"] == dashboard_views.UNKNOWN_EXPLANATION
    assert row["source"] == dashboard_views.UNKNOWN_SOURCE


def test_a_reading_this_installation_never_uses_is_left_off_but_still_listed(
    dashboard_views, pumps
):
    pump = pumps["vsh"]
    pump["unused"] = ["hp1_brine_in", "current_l3"]
    performance = _tab(dashboard_views, pump, "performance")
    shown = {e for s in performance for _k, _c, e in _items(s)}
    assert pump["entities"]["hp1_brine_in"] not in shown
    assert pump["entities"]["current_l3"] not in shown
    # The graphs leave it out too, rather than drawing a flat zero.
    compressor = _section(performance, "Kompressorn det senaste dygnet")["cards"][1]
    assert pump["entities"]["hp1_brine_in"] not in json.dumps(compressor)
    # The full list is the tab that answers whether a value exists at all.
    config = dashboard_views.build_dashboard([pump], "sv", NEW_HA)
    listed = {row["entity"] for row in _values_card(config)["rows"] if "entity" in row}
    assert pump["entities"]["hp1_brine_in"] in listed


# --------------------------------------------------------------- all values


def test_all_values_is_one_list_with_a_heading_per_group(dashboard_views, pumps):
    config = dashboard_views.build_dashboard([pumps["pt"]], "sv", NEW_HA)
    (section,) = _view(config, "values")["sections"]
    assert section["cards"][0]["heading"] == "Alla värden"
    # Wide enough for a full name, since it is a list and not a column of tiles.
    assert section["column_span"] == 4
    card = _values_card(config)
    assert card["type"] == ROWS
    assert card["filter"] == "Sök bland värdena"
    assert card["empty"] == "Inget värde matchar sökningen."
    headings = [row["heading"] for row in card["rows"] if "heading" in row]
    assert headings[:3] == ["Drift", "Temperaturer", "Varmvatten"]
    # The display's own pages follow, under the names the panel prints.
    assert "Historisk driftinfo" in headings
    assert "Om enheten" in headings


def test_all_values_lists_every_entity_the_pump_has_exactly_once(dashboard_views, pumps):
    for pump in pumps.values():
        config = dashboard_views.build_dashboard([pump], "sv", NEW_HA)
        rows = [row for row in _values_card(config)["rows"] if "entity" in row]
        entities = [row["entity"] for row in rows]
        assert len(entities) == len(set(entities))
        assert set(entities) == set(pump["entities"].values())
        # Nothing is hidden here, whatever it reads.
        assert all("hide_unavailable" not in row for row in rows)
        assert all(row.get("explanation") and row.get("source") for row in rows)


def test_a_button_is_on_the_controls_tab_and_not_in_the_list_of_values(dashboard_views, pumps):
    """V6: a button's state is the time it was last pressed, "Unknown" until then."""
    pump = pumps["vsh"]
    pump["entities"]["release_control"] = "button.ctc_ecozenith_i255_slapp_all_styrning"
    pump["names"]["release_control"] = "Släpp all styrning"
    config = dashboard_views.build_dashboard([pump], "sv", NEW_HA)
    listed = [row["entity"] for row in _values_card(config)["rows"] if "entity" in row]
    assert pump["entities"]["release_control"] not in listed
    # Everything else is still there once, and the button has its row to be
    # pressed on, so the page as a whole still refers to every entity.
    assert set(listed) == set(pump["entities"].values()) - {pump["entities"]["release_control"]}
    assert len(listed) == len(set(listed))
    assert dashboard_views.entity_ids(config) == set(pump["entities"].values())


def test_the_supplied_power_register_is_named_without_its_number_and_keeps_its_identity(
    const, explanations
):
    """V6: the register number is the source row's to show, and the unique_id never
    held the name."""
    (power,) = [d for d in const.MODBUS_SENSORS if d.key == "hp1_power"]
    assert power.name == "Tillförd effekt värmepump"
    assert power.address == 62331
    assert explanations.source("hp1_power") == "Modbus-register 62331"
    # The entity's identity is "<domain>_<device key>_<key>" (keys.unique_id), so
    # a renamed register keeps its entity_id, its history and the user's own settings.
    sensor = _source("sensor.py")
    cls = sensor[sensor.index("class CtcModbusSensor"):sensor.index("class CtcModbusSensor") + 600]
    assert "self._attr_unique_id = unique_id(runtime.device, description.key)" in cls
    assert "description.name" not in cls[:cls.index("_attr_unique_id")]


def test_a_display_row_is_listed_under_the_name_the_panel_prints(dashboard_views, pumps):
    pump = pumps["pt"]
    config = dashboard_views.build_dashboard([pump], "sv", NEW_HA)
    names = [row["name"] for row in _values_card(config)["rows"] if "entity" in row]
    assert "Avgiven värme totalt" in names
    assert not [n for n in names if n.startswith("Historisk driftinfo")]


def test_under_the_control_heading_the_names_drop_the_styr_they_start_with(
    dashboard_views, pumps
):
    config = dashboard_views.build_dashboard([pumps["vsh"]], "sv", NEW_HA)
    names = [row["name"] for row in _values_card(config)["rows"] if "entity" in row]
    assert "Rumsbörvärde" in names
    assert not [n for n in names if n.startswith("Styr ")]


def test_a_name_the_user_gave_a_display_value_is_kept(dashboard_views, pumps):
    pump = pumps["vsh"]
    pump["names"]["p30_avgiven_varme_totalt"] = "Värme ut sedan start"
    config = dashboard_views.build_dashboard([pump], "sv", NEW_HA)
    names = [row["name"] for row in _values_card(config)["rows"] if "entity" in row]
    assert "Värme ut sedan start" in names


def test_values_of_a_page_no_longer_harvested_are_left_off(dashboard_views, pumps):
    # The i550 Pro once had four more pages ticked. Their entities stay in the
    # registry, unavailable for good, and belong nowhere on the page.
    pump = pumps["pt"]
    orphans = {k: e for k, e in pump["entities"].items()
               if k.startswith(("p23_", "p24_", "p27_", "p58_"))}
    assert orphans
    pump["pages"] = [page for page in pump["pages"] if page["title"] == "Historisk driftinfo"]
    config = dashboard_views.build_dashboard([pump], "sv", NEW_HA)
    on_page = dashboard_views.entity_ids(config)
    assert not set(orphans.values()) & on_page
    assert "Övrigt" not in _headings(_view(config, "performance")["sections"])


def test_a_pump_without_display_pages_keeps_its_four_tabs(dashboard_views, pumps):
    pump = pumps["pt"]
    pump["pages"] = []
    pump["energy_out"] = pump["energy_in"] = None
    config = dashboard_views.build_dashboard([pump], "sv", NEW_HA)
    assert [v["path"] for v in config["views"]] == [
        "overview", "controls", "performance", "values"
    ]


def test_no_running_pump_gives_a_note(dashboard_views):
    config = dashboard_views.build_dashboard([], "sv-SE", NEW_HA)
    (view,) = config["views"]
    (section,) = view["sections"]
    heading, note = section["cards"]
    assert heading["heading"] == "CTC"
    assert note["type"] == "markdown" and note["content"].startswith("Ingen CTC-värmepump")
    assert dashboard_views.build_dashboard([], "de", NEW_HA)["views"][0]["title"] == "Overview"


def test_the_page_is_plain_json_and_its_entities_can_be_listed(dashboard_views, pumps):
    config = dashboard_views.build_dashboard(list(pumps.values()), "sv", NEW_HA)
    assert json.loads(json.dumps(config)) == config
    assert dashboard_views.entity_ids(config) == set(pumps["vsh"]["entities"].values()) | set(
        pumps["pt"]["entities"].values()
    )


# ------------------------------------------------------- the whole catalogue


def _source(name):
    return (COMPONENT / name).read_text(encoding="utf-8")


def test_every_key_on_the_page_is_a_unique_id_the_integration_creates(dashboard_views, const):
    created = {d.key for d in const.MODBUS_SENSORS + const.MODBUS_SETTINGS}
    created |= {r.key for r in const.CONTROL_NUMBERS + const.CONTROL_SELECTS}
    created |= set(re.findall(r'DerivedBinary\(\s*"([a-z0-9_]+)"', _source("binary_sensor.py")))
    identity = re.search(r"IDENTITY_ROWS = \((.*?)\n\)", _source("sensor.py"), re.S)
    created |= set(re.findall(r'\("([a-z_]+)", "', identity.group(1)))
    spans = re.search(r"for span in \(([^)]*)\)", _source("sensor.py")).group(1)
    created |= {f"cop_{span}" for span in re.findall(r'"([a-z_]+)"', spans)}
    # The share of auxiliary heat over its spans (R28), built beside them.
    assert 'unique_id(runtime.device, f"aux_share_{span}")' in _source("sensor.py")
    created |= {f"aux_share_{span}" for span in load("cop").SHARE_SPANS}
    # Entities with a key of their own, spelled out in the unique id: the
    # button, the sensor that accounts for the display harvest, the events and
    # the debug report's switch.
    for name in ("binary_sensor.py", "button.py", "sensor.py", "event.py", "switch.py"):
        created |= set(re.findall(r'unique_id\(runtime\.device, "([a-z_]+)"\)', _source(name)))
    # The transitions' sensors are a table in transitions.py (R27, R31), and
    # the mean run over a day is the one sensor beside it.
    transitions = load("transitions")
    created |= {item.key for item in transitions.TRANSITION_SENSORS}
    created.add(transitions.MEAN_RUN_KEY)

    used = set(dashboard_views._KNOWN_KEYS)
    # The page may know a key before the integration makes it, but only the
    # ones it says so about (the week's and month's coefficient, R25), and the
    # day the integration has caught up that set is to be emptied, so this
    # check is as strict as it was.
    ahead = dashboard_views.AHEAD_OF_THE_INTEGRATION
    assert used - created <= ahead
    assert not ahead & created, "the integration makes these now: empty AHEAD_OF_THE_INTEGRATION"
    # And the other way: nothing the integration makes is left to "Other".
    assert created - used == set()


def test_every_key_a_tab_picks_out_is_in_the_full_list(dashboard_views):
    def known(keys):
        return {key for key in keys if not key.startswith("@")} <= dashboard_views._KNOWN_KEYS

    assert known(dashboard_views._KEY_READINGS)
    assert known(dashboard_views._QUICK_CONTROLS)
    assert known(key for _id, _kind, keys in dashboard_views._GRAPHS for key in keys)


def test_every_control_name_starts_with_the_prefix_the_page_drops(dashboard_views, const):
    for register in const.CONTROL_NUMBERS + const.CONTROL_SELECTS:
        assert register.name.startswith(dashboard_views._CONTROL_PREFIX), register.key


def test_the_coefficient_of_performance_is_kept_as_a_statistic(dashboard_views):
    """The page graphs it per day, which Home Assistant only has if it records it."""
    source = _source("sensor.py")
    cop = source[source.index("class CtcCopSensor"):source.index("class CtcCopSensor") + 900]
    assert "_attr_state_class = SensorStateClass.MEASUREMENT" in cop
    assert ("graph_cop", "mean", ("cop_day", "cop_month")) in dashboard_views._GRAPHS


def test_no_reading_without_a_device_class_is_left_with_the_default_icon(const):
    for description in const.MODBUS_SENSORS + const.MODBUS_SETTINGS:
        if description.device_class is None:
            assert description.icon, f"{description.key} would show Home Assistant's eye"


def _every_item(dashboard_views, pump, lang="sv"):
    config = dashboard_views.build_dashboard([pump], lang, NEW_HA)
    return [
        (card, item, entity_id)
        for view in config["views"] for section in view["sections"]
        for card in _cards(section)
        for _kind, item, entity_id in _items({"cards": [None, card]})
    ]


def test_every_value_on_every_tab_is_explained_and_says_where_it_comes_from(
    dashboard_views, pumps
):
    for pump in pumps.values():
        pump["display_interval"] = 1800
        for card, item, entity_id in _every_item(dashboard_views, pump):
            assert item.get("explanation"), entity_id
            assert item.get("source"), entity_id
            assert card["more_info"] == "Mer info"
            # What the "i" beside the value is called, for a screen reader.
            assert card["explain"] == "Förklaring"


def test_sources_name_the_register_or_the_display_page(dashboard_views, pumps):
    pump = pumps["vsh"]
    pump["display_interval"] = 1800
    by_entity = {e: item for _c, item, e in _every_item(dashboard_views, pump)}
    assert by_entity[pump["entities"]["outdoor_temp"]]["source"] == "Modbus-register 62000"
    assert by_entity[pump["entities"]["set_room_1"]]["source"] == (
        "Modbus-register 61509, lagrad inställning som bara läses"
    )
    assert by_entity[pump["entities"]["ctl_dhw_mode"]]["source"] == "Modbus-register 1007, flyktigt"
    assert by_entity[pump["entities"]["p22_avgiven_varme"]]["source"] == (
        "Displaysidan Driftinfo Värmepump, hämtas var 30:e minut"
    )
    assert by_entity[pump["entities"]["compressor_running"]]["source"].endswith("62017")


def test_display_rows_are_explained_by_their_label(explanations):
    assert explanations.display_explanation("VP in/ut 2", "Driftinfo Värmepump").startswith(
        "Vattnets temperatur in till och ut"
    )
    # The longest match wins.
    assert "invertern driver kompressormotorn med just nu" in explanations.display_explanation(
        "Inverter Motoreffekt", "Driftinfo Värmepump"
    )
    assert explanations.display_explanation("Inverter", "x").startswith("Temperaturen i invertern")
    assert explanations.display_explanation("Avgiven värme totalt", "x").startswith("All värme")
    assert explanations.display_explanation("Avgiven värme/30 dagar", "x").startswith(
        "Värme levererad"
    )
    assert explanations.display_explanation("Avgiven värme", "x").startswith("Värmeeffekten")
    assert explanations.display_explanation("Energy output total", "x").startswith("All värme")
    # The i550 Pro's overview picture has numbers without a heading of their own.
    unnamed = explanations.display_explanation("CTC EcoZenith i550 Pro 3", "CTC EcoZenith i550 Pro")
    assert unnamed == explanations.display_explanation("Värde 4", "CTC EcoZenith i550 Pro")
    assert "utan en egen rubrik" in unnamed
    assert explanations.display_explanation("Något helt okänt", "x") == explanations._GENERIC


def test_every_key_the_integration_makes_has_an_explanation(explanations, dashboard_views):
    used = set(dashboard_views._KNOWN_KEYS)
    missing = sorted(key for key in used if not explanations.explain(key))
    assert missing == []
    assert sorted(key for key in used if not explanations.source(key)) == []


def test_every_value_a_real_installation_has_is_explained(dashboard_views, pumps):
    """Both houses, every value: nothing falls back on the page's own apology."""
    for pump in pumps.values():
        for _card, item, entity_id in _every_item(dashboard_views, pump):
            assert item["explanation"] != dashboard_views.UNKNOWN_EXPLANATION, entity_id
            assert item["source"] != dashboard_views.UNKNOWN_SOURCE, entity_id


def test_explanations_use_no_dashes_as_punctuation(explanations):
    texts = [
        *explanations.MODBUS.values(), *explanations.CONTROL.values(),
        *explanations.DERIVED.values(), *explanations.IDENTITY.values(),
        *(text for _prefix, text in explanations._DISPLAY),
        explanations._UNNAMED, explanations._GENERIC,
    ]
    for text in texts:
        assert "—" not in text and " – " not in text and " - " not in text, text


def test_the_pages_own_words_use_no_dashes_as_punctuation(dashboard_views):
    for text in dashboard_views.TEXT.values():
        for word in text.values():
            assert "—" not in word and " – " not in word and " - " not in word, word


def test_the_button_is_a_control_of_its_own_with_a_word_on_it(dashboard_views, pumps):
    pump = pumps["vsh"]
    pump["entities"]["release_control"] = "button.ctc_ecozenith_i255_slapp_all_styrning"
    pump["names"]["release_control"] = "Släpp all styrning"
    power = _section(_tab(dashboard_views, pump, "controls"), "Drift och el")
    (card,) = _cards(power)
    assert card["type"] == CONTROLS and card["press"] == "Utför"
    (_kind, item, entity_id) = _items(power)[-1]
    assert entity_id.startswith("button.")
    assert item["name"] == "Släpp all styrning"
    # The note that said why it is there is now its own explanation (V5): the
    # five minute rule, and that a mode has a release of its own while a
    # number has not.
    assert item["explanation"].startswith("Släpper all styrning")
    assert "fem minuter" in item["explanation"] and "Släpp styrningen" in item["explanation"]
    assert (
        "release" not in dashboard_views.TEXT["sv"]
        and "control_note" not in dashboard_views.TEXT["sv"]
    )


def test_the_address_of_the_web_interface(const):
    """What the device's link in Home Assistant points at.

    The display answers the same page for any main.* address, and the port is
    only written out when it is not the usual one.
    """
    assert const.web_interface_url("192.0.2.55") == "http://192.0.2.55/main.html"
    assert const.web_interface_url("192.0.2.55", 80) == "http://192.0.2.55/main.html"
    assert const.web_interface_url("192.0.2.55", 8080) == "http://192.0.2.55:8080/main.html"
