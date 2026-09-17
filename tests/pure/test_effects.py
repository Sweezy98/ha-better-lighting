"""Effects: the shape a light makes over time.

An effect says nothing about which colour or how bright -- it says what to do
with whichever it is handed. These check that it stays that way, since the
moment an effect knows its own brightness it is a different effect in a dim
room than in a bright one.
"""

from __future__ import annotations

from custom_components.better_lighting.effects import (
    BREATHE,
    BUILT_IN,
    CANDLE,
    FLASH,
    SOLID,
    Effect,
    EffectRequest,
    EffectStep,
    frames,
    from_mapping,
    resolve,
)


class TestFrames:
    def test_a_step_is_a_fraction_of_what_it_was_handed(self) -> None:
        played = list(
            frames(EffectRequest(effect=BREATHE, brightness_pct=50, duration=5))
        )

        assert played[0].brightness_pct == 50
        # Down to 22% of fifty, not to 22.
        assert round(played[1].brightness_pct, 1) == 11.0

    def test_it_stops_when_the_time_is_up(self) -> None:
        """Two seconds means two seconds, not "two seconds rounded up to the
        end of whatever it was doing"."""
        played = list(
            frames(EffectRequest(effect=FLASH, brightness_pct=100, duration=2))
        )

        assert sum(frame.wait for frame in played) == 2

    def test_the_last_frame_does_not_overrun_its_welcome(self) -> None:
        played = list(
            frames(EffectRequest(effect=BREATHE, brightness_pct=100, duration=1.5))
        )

        # Nothing still moving when the room is about to be put back.
        assert played[-1].transition <= played[-1].wait

    def test_one_shot_effects_happen_once(self) -> None:
        played = list(
            frames(EffectRequest(effect=SOLID, brightness_pct=100, duration=30))
        )

        assert len(played) == 1

    def test_a_colour_the_step_names_wins(self) -> None:
        """A candle warms as it dips, whatever colour it was asked for."""
        played = list(
            frames(
                EffectRequest(
                    effect=CANDLE,
                    brightness_pct=100,
                    color={"rgb_color": (255, 255, 255)},
                    duration=3,
                )
            )
        )

        assert any(frame.color == {"color_temp_kelvin": 1900} for frame in played)
        assert any(frame.color == {"rgb_color": (255, 255, 255)} for frame in played)

    def test_no_duration_plays_one_cycle(self) -> None:
        """Which the caller repeats for as long as it wants it: a scene's
        effect runs until the scene ends, and nothing here knows when that
        is."""
        played = list(frames(EffectRequest(effect=BREATHE, brightness_pct=100)))

        assert len(played) == len(BREATHE.steps)

    def test_an_effect_with_no_steps_does_nothing(self) -> None:
        empty = Effect("nothing", "Nothing")

        assert list(frames(EffectRequest(effect=empty, duration=5))) == []


class TestWrittenByHand:
    def test_one_is_read_as_it_was_written(self) -> None:
        effect = from_mapping(
            {
                "effect_id": "blip",
                "name": "Three blips",
                "repeat": False,
                "steps": [{"level": 100, "transition": 0, "hold": 0.1}],
            }
        )

        assert effect.repeat is False
        assert effect.steps == (EffectStep(level=1.0, transition=0.0, hold=0.1),)

    def test_a_name_finds_ours_or_theirs(self) -> None:
        mine = Effect("blip", "Blip")

        assert resolve("breathe", {}) is BREATHE
        assert resolve("blip", {"blip": mine}) is mine
        assert resolve("nothing_like_it", {}) is None
        assert resolve(None, {}) is None

    def test_the_built_in_ones_are_all_different(self) -> None:
        assert len({effect.effect_id for effect in BUILT_IN}) == len(BUILT_IN)
        assert all(effect.steps for effect in BUILT_IN)
