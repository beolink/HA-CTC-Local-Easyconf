"""The chart of the coefficient of performance beside the outdoor temperature.

www/ctc-ecozenith-chart.js draws custom:ctc-ecozenith-cop-chart, the two axes
Home Assistant's own graphs cannot give: its history graph puts each unit in a
chart of its own, and its statistics graph draws both on one axis with no unit.
What is pinned here is what decides the picture: how Home Assistant's two
answers are read, where the axes start and stop and what their labels say, and
how a gap in a history is drawn. The helpers are loaded into node, as the
page's cards are (tests/test_card*.py). The card is served and loaded under the
integration's version, as the page's cards are.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

TESTS = Path(__file__).resolve().parent
COMPONENT = TESTS.parent / "custom_components" / "ctc_ecozenith"
CHART = COMPONENT / "www" / "ctc-ecozenith-chart.js"

node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _run(expression: str):
    """The value of ``expression`` with the chart's helpers in scope as ``h``."""
    program = (
        f"const h = require({json.dumps(str(CHART))});\n"
        f"process.stdout.write(JSON.stringify({expression}));\n"
    )
    out = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@node
def test_a_history_is_read_in_time_order_and_a_state_that_is_no_number_is_a_gap():
    answer = {"sensor.cop": [
        {"s": "2.6", "lu": 100}, {"s": "unknown", "lu": 200}, {"s": "2.7", "lu": 150.5},
    ]}
    assert _run(f"h.historyPoints({json.dumps(answer)}, 'sensor.cop')") == [
        [100000, 2.6], [150500, 2.7], [200000, None],
    ]
    assert _run("h.historyPoints({}, 'sensor.cop')") == []


@node
def test_the_days_are_read_as_their_means_from_the_start_of_each_day():
    answer = {"sensor.cop": [{"start": 2000, "mean": 2.5}, {"start": 1000, "mean": None}]}
    assert _run(f"h.dayPoints({json.dumps(answer)}, 'sensor.cop')") == [
        [1000, None], [2000, 2.5],
    ]


@node
def test_the_left_axis_takes_round_steps_and_a_bar_chart_starts_at_zero():
    assert _run("h.niceScale(2.55, 2.74, 5)")["ticks"] == [2.55, 2.6, 2.65, 2.7, 2.75]
    assert _run("h.niceScale(2.5, 3.2, 5, true)")["ticks"] == [0, 1, 2, 3, 4]
    # Nothing to draw, or one value only, still gets an axis.
    assert _run("h.niceScale(null, null, 5)")["ticks"] == [0, 0.2, 0.4, 0.6, 0.8, 1]
    assert _run("h.niceScale(3, 3, 5)")["low"] < 3 < _run("h.niceScale(3, 3, 5)")["high"]


@node
def test_the_temperature_axis_has_the_left_axis_steps_in_round_degrees():
    """Its labels stand at the grid lines of the coefficient, and read true."""
    scale = _run("h.alignedScale(-1, 8.2, 4)")
    assert len(scale["ticks"]) == 5 and scale["low"] <= -1 and scale["high"] >= 8.2
    assert all(tick % scale["step"] == 0 for tick in scale["ticks"])
    # A sensor that reads whole degrees and moves one of them over a day is
    # not drawn as a change of weather across the whole height.
    assert _run("h.widened(0, 1, 4)") == [-1.5, 2.5]
    assert _run("h.widened(-3, 8, 4)") == [-3, 8]
    assert _run("h.alignedScale(...h.widened(0, 1, 4), 4)")["ticks"] == [-2, 0, 2, 4, 6]


@node
def test_a_label_has_the_decimals_its_step_needs_and_the_locales_comma():
    steps = "[0.25, 0.05, 0.5, 5].map(h.decimalsFor)"
    assert _run(steps) == [2, 2, 1, 0]
    assert _run("h.figure(2.567, 'sv', 2)") == "2,57"
    assert _run("h.figure(null, 'sv', 1)") == "–"


@node
def test_a_history_is_drawn_as_steps_and_a_gap_lifts_the_pen():
    path = _run("h.stepPath([[0, 1], [10, null], [20, 2]], (t) => t, (v) => v * 10, 30)")
    assert path == "M0.0,10.0L10.0,10.0M20.0,20.0L30.0,20.0"
    line = _run("h.dayLine([[0, 1], [10, null], [20, 2]], (t) => t, (v) => v)")
    assert line == "M0.0,1.0M20.0,2.0"
    assert _run("[h.valueAt([[0, 1], [10, 2]], 5), h.valueAt([[0, 1], [10, 2]], -1)]") == [1, None]


def _in_stockholm(expression: str):
    program = (
        f"const h = require({json.dumps(str(CHART))});\n"
        f"process.stdout.write(JSON.stringify({expression}));\n"
    )
    out = subprocess.run(
        ["node", "-e", program], capture_output=True, text=True, check=True,
        env={"TZ": "Europe/Stockholm", "PATH": os.environ.get("PATH", "")},
    )
    return json.loads(out.stdout)


@node
def test_the_days_are_counted_by_the_calendar_across_the_change_of_clock():
    """24 hours a day from before 25 October landed on the evening before."""
    days = _in_stockholm(
        "[29, 30].map((n) => new Date(h.dayAfter(new Date(2026, 9, 24).getTime(), n))"
        ".toString().slice(4, 21))"
    )
    assert days == ["Nov 22 2026 00:00", "Nov 23 2026 00:00"]


def test_the_chart_is_served_and_loaded_under_the_integrations_version():
    card = (COMPONENT / "card.py").read_text(encoding="utf-8")
    assert 'CHART_URL = f"/{DOMAIN}/ctc-ecozenith-chart.js"' in card
    assert "StaticPathConfig(CHART_URL, str(CHART_FILE), False)" in card
    assert 'frontend.add_extra_js_url(hass, f"{CHART_URL}?v={version}")' in card


def test_the_chart_writes_no_html():
    """Every string goes into the page as text: names come from the entity registry."""
    source = CHART.read_text(encoding="utf-8")
    assert "innerHTML" not in source and "insertAdjacentHTML" not in source
    assert 'customElements.define("ctc-ecozenith-cop-chart"' in source
