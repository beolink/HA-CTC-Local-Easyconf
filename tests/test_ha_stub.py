"""The stand-ins for Home Assistant step aside, rather than fail, under the real thing.

The ordinary suite loads the coordinator and the entity platforms against
tests/ha_stub.py when Home Assistant is not installed, and that is what CI
runs. In an environment that does have Home Assistant, such as the one the
optional test_homeassistant.py needs, install() used to return early and leave
the tests that build on it to fall over the real classes: the stub package
had no CtcConfigEntry for the platforms to import, the real timer helper asked
a stand-in hass for its event loop, and the real coordinator base class asked
for the frame helper. The suite has to be green in both worlds with the same
command.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import sys

import pytest

import conftest
from conftest import load
import ha_stub


def _real_homeassistant_is_loaded() -> bool:
    # The stand-in package is built from types.ModuleType and has no file.
    return hasattr(sys.modules.get("homeassistant"), "__file__")


# ------------------------------------------------------------ install()


def test_install_says_which_world_the_test_is_in():
    first = ha_stub.install()
    assert isinstance(first, bool)
    assert first is (not _real_homeassistant_is_loaded())
    # Calling it again changes nothing and answers the same.
    assert ha_stub.install() is first
    assert ha_stub.stubbed is first


def test_the_platforms_load_in_either_world():
    ha_stub.install()
    assert sys.modules["ctc_ecozenith"].CtcConfigEntry is not None
    assert load("binary_sensor").CtcDerivedBinary
    assert load("number").CtcControlNumber
    assert load("select").CtcControlSelect
    assert load("switch").CtcDebugReportSwitch


def test_skip_unless_stubbed_only_skips_under_a_real_core():
    if ha_stub.install():
        ha_stub.skip_unless_stubbed()  # must come back without skipping
        return
    with pytest.raises(pytest.skip.Exception) as skipped:
        ha_stub.skip_unless_stubbed()
    assert ha_stub.SKIP_REASON in str(skipped.value)


# --------------------------------------------------------- the timer record


def test_the_timer_recorder_is_what_the_event_helper_hands_out(monkeypatch):
    ha_stub.record_timers(monkeypatch)
    assert ha_stub.tracked == []
    from homeassistant.helpers.event import async_track_time_interval

    async def action(_now) -> None:
        pass

    unsub = async_track_time_interval(object(), action, timedelta(seconds=60))
    assert ha_stub.tracked == [(action, timedelta(seconds=60))]
    unsub()
    assert ha_stub.tracked == []


def test_the_control_manager_reaches_the_recorder_through_the_helper(monkeypatch):
    ha_stub.record_timers(monkeypatch)
    coordinator = load("coordinator")

    class Client:
        async def async_write(self, address: int, raw: int) -> None:
            pass

    manager = coordinator.CtcControlManager(hass=object(), client=Client(), clock=lambda: 0.0)
    asyncio.run(manager.async_set(1002, 450))
    assert len(ha_stub.tracked) == 1
    manager.async_release_all()
    assert ha_stub.tracked == []


# -------------------------------------------------------- conftest.load()


def test_a_module_that_fails_to_load_is_not_left_half_made(monkeypatch, tmp_path):
    (tmp_path / "broken_on_purpose.py").write_text("from . import NotThere\n", encoding="utf-8")
    monkeypatch.setattr(conftest, "COMPONENT", tmp_path)
    for _ in range(2):
        # The second attempt must fail the same way, not with an AttributeError
        # on a module that is in sys.modules but never finished loading.
        with pytest.raises(ImportError):
            load("broken_on_purpose")
        assert "ctc_ecozenith.broken_on_purpose" not in sys.modules
    (tmp_path / "broken_on_purpose.py").write_text("WHOLE = True\n", encoding="utf-8")
    assert load("broken_on_purpose").WHOLE is True
    sys.modules.pop("ctc_ecozenith.broken_on_purpose", None)


# ------------------------------------------------- pytest-asyncio's mode


def test_pytest_asyncio_runs_in_auto_mode_where_it_is_loaded(request):
    config = request.config
    if not hasattr(config.option, "asyncio_mode"):
        pytest.skip("pytest-asyncio is not loaded, so there is no mode to set")
    chosen = any(arg.startswith("--asyncio-mode") for arg in config.invocation_params.args)
    if chosen or config.getini("asyncio_mode") != "strict":
        pytest.skip("the mode was chosen explicitly, which conftest respects")
    assert config.option.asyncio_mode == "auto"
