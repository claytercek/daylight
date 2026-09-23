"""Shared pytest fixtures for daylight tests.

`enable_custom_integrations` is intentionally NOT autouse: pure-logic tests
(color_and_brightness, target's manual-control state machine) run with no
`hass` at all. Only tests that exercise the actual integration (coordinator,
switch, config_flow, __init__) should request `enable_custom_integrations`
explicitly.
"""

import pathlib

import pytest

_REPO_ROOT = str(pathlib.Path(__file__).parent.parent)


@pytest.fixture
def hass_config_dir() -> str:
    """Point hass.config.config_dir at the repo root.

    HA's loader mounts `hass.config.config_dir` onto sys.path and imports
    `custom_components` from there (`homeassistant.loader._async_mount_config_dir`).
    The harness's own default config dir doesn't contain our integration, so
    without this override `custom_components.daylight` is never discoverable
    in hass-level tests.
    """
    return _REPO_ROOT
