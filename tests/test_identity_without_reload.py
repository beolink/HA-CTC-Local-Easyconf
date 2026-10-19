"""Identity and release check without a reload (R7): the two rules, and the call sites.

Every write of the options reloads the entry, and the background task used to
write the identity it had found through the options. A reload closes Modbus
for the controller's settle time, puts the daily report's delay back to the
start, asks GitHub again and shows the CTC page as "no heat pump is running".
Now the identity goes into the device, the sensors and the runtime directly,
the write of it alone reloads nothing, and the release check remembers its
answer across reloads and leaves the notice alone when GitHub does not answer.
The run under a real core is in test_homeassistant_identity.py; here are the
two rules on their own and the order held in the source.
"""

from __future__ import annotations

import pathlib

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"

DAY = 86400.0


# ------------------------------------------------------- the release check


def test_github_is_asked_once_a_day_on_the_loops_clock(updates):
    assert updates.check_is_due(None, 1000.0, DAY), "inget minne: fråga"
    assert not updates.check_is_due(1000.0, 1000.0 + 3600, DAY), (
        "en omladdning en timme senare frågar inte"
    )
    assert updates.check_is_due(1000.0, 1000.0 + DAY, DAY)
    assert updates.check_is_due(1000.0, 1000.0 + 2 * DAY, DAY)


def test_a_timer_that_fires_a_moment_early_still_counts_as_the_next_day(updates):
    # The daily timer and the loop's clock drift apart by seconds, not hours;
    # a tick a few seconds short of the day must not leave a day without a check.
    assert updates.check_is_due(1000.0, 1000.0 + DAY - 10, DAY)
    assert not updates.check_is_due(1000.0, 1000.0 + DAY - updates.CHECK_SLACK_SECONDS - 1, DAY)


def test_no_answer_leaves_the_notice_and_the_memory_alone():
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    check = source.split("async def _async_check_release")[1].split("\ndef ")[0]
    # The answer is judged before anything is written: a quiet GitHub returns
    # without a delete and without being remembered as a check.
    asked, answered = check.split("latest = await async_latest_release")
    assert "if latest is None:" in answered
    nothing = answered.split("if latest is None:")[1].split("_RELEASE_CHECKED[")[0]
    assert "async_delete_issue" not in nothing, "utan svar rörs inte ärendet"
    assert "return" in nothing
    # And the question is asked only when the memory says it is due.
    assert "check_is_due(" in asked
    assert "if latest and newer(" not in check, "None får inte falla i else-grenen"


# --------------------------------------------- the identity written alone


def test_only_the_identity_differing_is_told_apart_from_a_real_change(identity):
    before = {"fast_interval": 30, "identity": {"serial": "1"}}
    assert identity.only_identity_differs(
        before, {"fast_interval": 30, "identity": {"serial": "1", "mac": "x"}}
    )
    assert identity.only_identity_differs(
        {"fast_interval": 30}, {"fast_interval": 30, "identity": {"serial": "1"}}
    )
    assert identity.only_identity_differs(before, before)
    assert not identity.only_identity_differs(
        before, {"fast_interval": 45, "identity": {"serial": "1"}}
    )
    assert not identity.only_identity_differs(
        before, {"fast_interval": 30, "slow_pages": [], "identity": {"serial": "1"}}
    )
    assert not identity.only_identity_differs(before, {"identity": {"serial": "1"}}), (
        "ett borttaget fält är en ändring"
    )


def test_the_background_adopts_the_identity_instead_of_writing_it_for_a_reload():
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    catch_up = source.split("async def _async_catch_up")[1].split("\ndef ")[0]
    assert "_async_adopt_identity(hass, entry, runtime, identity)" in catch_up
    assert "changed[CONF_IDENTITY]" not in catch_up, "identiteten går inte via omladdningen längre"
    adopt = source.split("def _async_adopt_identity")[1].split("\nasync def ")[0]
    # Everything that reads the identity is told, and the options come last.
    for told in (
        "runtime.identity = merged",
        "registry.async_update_device(",
        "async_dispatcher_send(hass, identity_signal(entry.entry_id))",
        "_async_review_issues(hass, entry, runtime)",
    ):
        assert told in adopt, told
        assert adopt.index(told) < adopt.index("async_update_entry("), f"{told} före skrivningen"
    # One rule for the listener and for the task that writes from the inside.
    rule = source.split("def _takes_a_reload")[1].split("\nasync def ")[0]
    # The debug report is taken out of both sides first: it is applied in
    # place by its reporter, and never takes a reload (debug_report.py).
    assert (
        "only_identity_differs(\n"
        "        without_the_debug_report(runtime.applied_options), "
        "without_the_debug_report(options)\n"
        "    )"
    ) in rule
    assert "dict(entry.data) != runtime.applied_data" in rule, "ett värdbyte laddar fortfarande om"
    reload = source.split("async def _async_reload")[1].split("\nasync def ")[0]
    assert "_takes_a_reload(entry, runtime, entry.options)" in reload
    assert "_takes_a_reload(entry, runtime, options)" in catch_up, (
        "bakgrundsuppgiften avgör med lyssnarens regel om dess skrivning laddar om"
    )


def test_the_identity_sensors_read_live_and_follow_the_signal():
    source = (COMPONENT / "sensor.py").read_text(encoding="utf-8")
    sensor = source.split("class CtcIdentitySensor")[1].split("\nclass ")[0]
    assert "_attr_native_value" not in sensor, "värdet läses ur runtime, inte vid skapandet"
    assert "def native_value" in sensor
    assert "async_dispatcher_connect(self.hass, self._signal, self.async_write_ha_state)" in sensor
    setup = source.split("async def async_setup_entry")[1].split("\nclass ")[0]
    assert "async_dispatcher_connect(hass, signal, _identity_filled_in)" in setup
