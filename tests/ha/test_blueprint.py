"""The shipped blueprint really produces a valid automation."""

from __future__ import annotations

import pathlib

import pytest
from homeassistant.components.automation.config import PLATFORM_SCHEMA
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.util.yaml import loader as ha_yaml

BLUEPRINT = (
    pathlib.Path(__file__).parents[2]
    / "blueprints"
    / "automation"
    / "better_lighting_home_cinema.yaml"
)


@pytest.fixture
def blueprint() -> Blueprint:
    data = ha_yaml.parse_yaml(BLUEPRINT.read_text())
    return Blueprint(data, expected_domain="automation", schema=BLUEPRINT_SCHEMA)


def test_metadata_is_well_formed(blueprint: Blueprint) -> None:
    assert blueprint.domain == "automation"
    assert blueprint.name
    assert set(blueprint.inputs) == {
        "media_player",
        "mode_select",
        "playing_state",
        "paused_state",
        "stop_delay",
    }


def test_it_substitutes_into_a_valid_automation(blueprint: Blueprint) -> None:
    """The real check: a filled-in blueprint has to pass the automation schema.

    A blueprint cannot be validated with its placeholders still in place, so
    validating the metadata alone would prove very little.
    """
    inputs = BlueprintInputs(
        blueprint,
        {
            "use_blueprint": {
                "path": BLUEPRINT.name,
                "input": {
                    "media_player": "media_player.cinema",
                    "mode_select": "select.home_cinema_state",
                    "playing_state": "playing",
                    "paused_state": "paused",
                    "stop_delay": 30,
                },
            }
        },
    )
    inputs.validate()
    config = PLATFORM_SCHEMA(inputs.async_substitute())

    assert len(config["triggers"]) == 3
    # Every branch ends in a select.select_option on the mode entity.
    options = [
        step["data"]["option"]
        for branch in config["actions"][0]["choose"]
        for step in branch["sequence"]
    ]
    assert options == ["playing", "paused", "off"]


def test_only_required_inputs_have_no_default(blueprint: Blueprint) -> None:
    """Anything without a default must be something only the user can know."""
    required = {
        name
        for name, spec in blueprint.inputs.items()
        if isinstance(spec, dict) and "default" not in spec
    }
    assert required == {"media_player", "mode_select"}
