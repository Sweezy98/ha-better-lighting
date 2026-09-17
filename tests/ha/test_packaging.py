"""Packaging consistency.

Most of these guard the same class of mistake: something declared in one place
and forgotten in another. Two of them have already caught real bugs -- a
missing entity translation silently collapses an entity's name to its device's,
so two entities in one room end up as `switch.kitchen` and `switch.kitchen_2`
and every automation referring to them breaks.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest
import yaml

COMPONENT = pathlib.Path(__file__).parents[2] / "custom_components" / "better_lighting"
STRINGS = json.loads((COMPONENT / "strings.json").read_text())
SERVICES_YAML = yaml.safe_load((COMPONENT / "services.yaml").read_text())
MANIFEST = json.loads((COMPONENT / "manifest.json").read_text())


def test_translations_match_strings() -> None:
    """Custom integrations load translations/, not strings.json."""
    english = json.loads((COMPONENT / "translations" / "en.json").read_text())
    assert english == STRINGS


def test_german_covers_exactly_the_same_strings() -> None:
    """A translation with holes in it is worse than none at all.

    Home Assistant falls back to English per missing key, so a partial file
    produces a form that is half one language and half the other.
    """
    english = json.loads((COMPONENT / "translations" / "en.json").read_text())
    german = json.loads((COMPONENT / "translations" / "de.json").read_text())

    def flatten(node, prefix=""):
        if isinstance(node, dict):
            merged = {}
            for key, value in node.items():
                merged |= flatten(value, f"{prefix}.{key}")
            return merged
        return {prefix: node}

    flat_en, flat_de = flatten(english), flatten(german)
    assert set(flat_en) == set(flat_de)

    # Placeholders are substituted by Home Assistant, so losing one in
    # translation produces a message with a hole in it.
    placeholder = re.compile(r"\{[a-z_]+\}")
    for key, value in flat_en.items():
        assert set(placeholder.findall(value)) == set(
            placeholder.findall(flat_de[key])
        ), f"{key} lost or gained a placeholder in translation"


def test_every_service_is_declared_everywhere() -> None:
    source = (COMPONENT / "services.py").read_text()
    registered = set(re.findall(r'^SERVICE_\w+ = "(\w+)"', source, re.M))
    assert registered
    assert registered == set(SERVICES_YAML), "services.yaml is out of step"
    assert registered == set(STRINGS["services"]), "strings.json is out of step"


def test_every_service_field_is_described() -> None:
    for name, schema in SERVICES_YAML.items():
        described = set(STRINGS["services"][name].get("fields", {}))
        declared = set(schema.get("fields") or {})
        assert declared <= described, f"{name} has undescribed fields"


def test_every_subentry_type_has_strings() -> None:
    source = (COMPONENT / "config_flow.py").read_text()
    used = set(re.findall(r"SubentryType\.(\w+)\.value: \w+SubentryFlow", source))
    declared = {name.upper() for name in STRINGS["config_subentries"]}
    assert used == declared


def test_every_entity_translation_key_has_a_name() -> None:
    """A missing one collapses the entity's name onto its device's."""
    declared = {
        f"{platform}.{key}"
        for platform, entries in STRINGS["entity"].items()
        for key in entries
    }
    for path in COMPONENT.glob("*.py"):
        source = path.read_text()
        for key in re.findall(r'_attr_translation_key = "(\w+)"', source):
            platform = path.stem
            assert f"{platform}.{key}" in declared, (
                f"{path.name} uses translation key {key!r} with no name in strings.json"
            )


def test_every_translated_selector_has_options() -> None:
    """A select whose options are not translated shows raw slugs.

    Free-text selects are exempt: their values are the device's own vocabulary
    rather than ours, so they deliberately carry no translation key.
    """
    source = (COMPONENT / "const.py").read_text()
    used = {
        match.group(2)
        for match in re.finditer(r"_select\(\s*([^,]+),\s*\"(\w+)\"([^)]*)\)", source)
        if "custom=True" not in match.group(3)
    }
    declared = set(STRINGS["selector"])
    assert used <= declared, f"untranslated selectors: {sorted(used - declared)}"


def test_every_selector_wraps_its_values_in_options() -> None:
    """hassfest rejects a selector block that is not {"options": {...}}.

    Checking the key existed was not enough: a selector whose values sat at the
    top level passed that and failed in CI, which is a poor division of labour.
    """
    for name, block in STRINGS["selector"].items():
        assert set(block) == {"options"}, (
            f"selector.{name} must hold exactly an 'options' mapping, got {sorted(block)}"
        )
        # Empty is legitimate for a select whose choices only exist at
        # runtime -- which scenes are defined, which rooms -- since there is
        # nothing fixed to label.
        assert isinstance(block["options"], dict), (
            f"selector.{name}.options must be a mapping"
        )
        assert all(isinstance(value, str) for value in block["options"].values()), (
            f"selector.{name}.options must map each value to a label"
        )


def test_manifest_is_complete() -> None:
    for key in (
        "domain",
        "name",
        "version",
        "documentation",
        "issue_tracker",
        "codeowners",
        "config_flow",
        "iot_class",
        "integration_type",
    ):
        assert MANIFEST.get(key), f"manifest is missing {key}"
    assert MANIFEST["domain"] == "better_lighting"
    # One hub entry owns everything, so Home Assistant should hide "add entry".
    assert MANIFEST["single_config_entry"] is True


def test_hacs_declares_the_supported_floor() -> None:
    hacs = json.loads((COMPONENT.parents[1] / "hacs.json").read_text())
    assert hacs["homeassistant"] == "2026.1.0"


def test_repair_issues_are_described() -> None:
    source = (COMPONENT / "repairs.py").read_text()
    used = set(re.findall(r'^ISSUE_\w+ = "(\w+)"', source, re.M))
    assert used == set(STRINGS["issues"])


def test_the_panel_javascript_ships_with_the_integration() -> None:
    """HACS copies files and runs no build, so the panel must be plain JS."""
    panel = COMPONENT / "www" / "better_lighting_panel.js"
    assert panel.is_file(), "panel script missing from the integration"
    source = panel.read_text()
    assert 'customElements.define("better-lighting-panel"' in source
    # Nothing that would need bundling or fetching at runtime.
    assert "import " not in source.split("*/")[-1], "panel must have no imports"


def test_every_menu_option_has_a_step_behind_it() -> None:
    """A menu entry naming a step that does not exist is a dead end."""
    source = (COMPONENT / "config_flow.py").read_text()
    defined = set(re.findall(r"async def async_step_(\w+)\(", source))
    # These are Home Assistant's own, not ours to define.
    defined |= {"finish", "done"}

    for area in ("config_subentries", "options"):
        block = STRINGS.get(area, {})
        groups = block.values() if area == "config_subentries" else [block]
        for group in groups:
            for step_id, step in (group.get("step") or {}).items():
                for option in step.get("menu_options") or {}:
                    assert option in defined, (
                        f"{area}.{step_id} offers {option!r}, which has no step"
                    )


def test_every_panel_field_has_a_label() -> None:
    """A field the panel cannot name shows a raw slug like `down_press_action`."""
    from custom_components.better_lighting import panel_schema

    schema = panel_schema.schema("en")
    labels = schema["labels"]["data"]
    unlabelled = sorted(
        {
            field["key"]
            for form in schema["forms"].values()
            for group in form
            for field in group["fields"]
            if field["key"] not in labels
        }
    )
    assert not unlabelled, f"panel fields with no label: {unlabelled}"


def test_the_panel_translates_as_completely_as_the_forms() -> None:
    """German is generated from the same files, so it must be as complete."""
    from custom_components.better_lighting import panel_schema

    english = panel_schema.schema("en")["labels"]
    german = panel_schema.schema("de")["labels"]
    assert set(german["data"]) == set(english["data"])
    assert set(german["options"]) == set(english["options"])


def test_the_panel_only_calls_commands_that_exist() -> None:
    """A typo in either half is a button that silently does nothing."""
    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()
    backend = (COMPONENT / "panel.py").read_text()

    called = set(re.findall(r'_call\("([a-z_]+)"', panel_js))
    registered = set(re.findall(r'f"\{DOMAIN\}/([a-z_]+)"', backend))
    assert called <= registered, (
        f"panel calls unknown commands: {sorted(called - registered)}"
    )


def test_the_panel_and_the_backend_agree_on_the_wildcard() -> None:
    """The "every light in this room" key must be the same string in both."""
    from custom_components.better_lighting.scenes import ALL_LIGHTS

    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()
    match = re.search(r'^const ALL = "(.*)";', panel_js, re.M)
    assert match, "the panel does not define the wildcard"
    assert match.group(1) == ALL_LIGHTS


def test_the_scene_editor_uses_home_assistants_own_light_dialog() -> None:
    """Its colour wheel is the one people already know; ours would not be."""
    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()
    assert "hass-more-info" in panel_js


def test_the_panel_covers_every_settings_surface() -> None:
    """The panel is meant to replace the flows, not shadow most of them.

    Marker-based rather than behavioural: this cannot prove a screen works,
    only that one exists, which is exactly the regression worth catching --
    a surface added to the flows and forgotten on the page.
    """
    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()
    surfaces = {
        "global settings": '"hub"',
        "every room at once": "_paintRooms",
        "every mode at once": "_paintModes",
        "colour presets": "_paintPresets",
        "add a room": "add-room",
        "room settings": "_paintRoomSection",
        "delete a room": "delete_zone",
        "scenes": "_paintEditor",
        "a light taken as it is": "_captureOne",
        "switches": '"switch"',
        "what a switch cycles": "_paintSwitchOrder",
        "light calibration": '"calibration"',
        "modes": "_paintMode",
        "mode rules": "_paintRules",
        "importing a Home Assistant scene": "_paintImport",
        "noticing a new version": "_checkVersion",
        "saying so in Home Assistant's own toast": "hass-notification",
        "diagnostics": "_paintDiagnostics",
        "the adaptive curve, drawn": "_paintCurve",
        "the curve in Home Assistant's own chart": "_haChart",
        "the way back to Home Assistant on a phone": "hass-toggle-menu",
        "a mode's rules, on a screen of their own": "_paintRuleList",
    }
    missing = sorted(
        name for name, marker in surfaces.items() if marker not in panel_js
    )
    assert not missing, f"the panel cannot reach: {missing}"

    # And a marker naming a method has to be a method, not prose: an
    # unterminated comment once swallowed a whole screen while leaving its
    # name in the file for this test to find.
    code = re.sub(r"/\*.*?\*/", "", panel_js, flags=re.S)
    for marker in surfaces.values():
        if not marker.startswith("_paint"):
            continue
        assert re.search(rf"^  (?:async )?{marker}\(", code, re.M), (
            f"{marker} is named in the panel but not defined in it"
        )


def test_the_panel_has_no_untranslated_chrome() -> None:
    """Every word the panel writes itself must come from its string table."""
    from custom_components.better_lighting import panel_schema

    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()
    # Strip comments and CSS, which are not shown to anybody.
    body = re.sub(r"/\*.*?\*/", "", panel_js, flags=re.S)
    body = re.sub(r"^\s*//.*$", "", body, flags=re.M)
    body = re.sub(r"<style>.*?</style>", "", body, flags=re.S)

    english = set(
        re.findall(r">([A-Z][a-z][^<>{}$]{4,60})<", body)
        + re.findall(r'"([A-Z][a-z][^"<>{}$]{4,60})"', body)
    )
    # The product's name, and the name of a key on the keyboard -- which is
    # what the browser calls it, not what anybody reads.
    allowed = {"Better Lighting", "Escape"}
    assert english <= allowed, (
        f"untranslated panel strings: {sorted(english - allowed)}"
    )

    # And every key it asks for exists.
    used = set(re.findall(r'_t\("([a-z_]+)"\)', panel_js))
    available = set(panel_schema.ui_strings("en"))
    assert used <= available, (
        f"panel asks for unknown strings: {sorted(used - available)}"
    )


def test_the_panel_strings_are_translated_as_completely_as_the_forms() -> None:
    from custom_components.better_lighting import panel_schema

    assert set(panel_schema.ui_strings("de")) == set(panel_schema.ui_strings("en"))
    german = panel_schema.ui_strings("de")
    english = panel_schema.ui_strings("en")
    untranslated = sorted(
        key
        for key, value in german.items()
        if value == english[key] and len(value) > 6 and value != "—"
    )
    assert not untranslated, f"still English in German: {untranslated}"


def test_home_assistants_controls_are_used_but_not_relied_on() -> None:
    """They are lazily registered and were never promised to a custom panel.

    So every borrowed control must be created only behind a check that it
    exists, with a plain control to fall back on. If the recipe that loads
    them stops working, the page gets plainer rather than breaking.
    """
    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()

    # Whatever is created by name must be guarded by name.
    for tag in set(re.findall(r'createElement\("(ha-[\w-]+)"\)', panel_js)):
        assert f'customElements.get("{tag}")' in panel_js, (
            f"{tag} is created without checking it exists"
        )
    # And the one created through a variable is guarded through that variable.
    if re.search(r"createElement\(tag\)", panel_js):
        assert "customElements.get(tag)" in panel_js

    # Every borrowed control we rely on is actually reached for somewhere.
    for tag in ("ha-selector", "ha-entity-picker", "ha-switch", "ha-icon-picker"):
        assert tag in panel_js, f"{tag} is not used"

    # And the fallbacks are still there.
    for fallback in ("_plainSelect", "_plainText", 'input.type = "checkbox"'):
        assert fallback in panel_js, f"{fallback} is gone"


def test_the_panel_string_table_has_no_unread_entries() -> None:
    """A table with entries nobody asks for is one people stop trusting."""
    from custom_components.better_lighting import panel_schema

    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()
    used = set(re.findall(r'_t\(\s*"([a-z_]+)"', panel_js))
    # A few are looked up through a variable -- the label of each built-in
    # effect is chosen from a table by name -- so a bare mention of the key
    # counts as asking for it.
    mentioned = set(re.findall(r'"([a-z_]+)"', panel_js))
    available = set(panel_schema.ui_strings("en"))
    assert available - (used | mentioned) == set(), (
        f"nobody asks for: {sorted(available - used - mentioned)}"
    )
    assert used <= available


def test_the_panel_never_wires_an_element_it_does_not_render() -> None:
    """querySelector returning null throws, and takes the rest of the paint
    with it.

    That is how a missing nav row emptied the whole content pane: the row was
    never added, its listener was, and the exception landed between painting
    the sidebar and painting everything else. A string edit that half-lands is
    invisible until somebody opens the page, so it is checked here instead.
    """
    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()

    # Selectors dereferenced straight away -- no `?.` -- must be rendered
    # somewhere in the file. Attribute selectors and ids are checkable; a bare
    # tag name is not worth chasing.
    for selector in set(
        re.findall(r'querySelector\(\s*"([^"]+)"\s*\)\.addEventListener', panel_js)
    ):
        if attribute := re.search(r"\[([\w-]+)", selector):
            token = f"{attribute.group(1)}="
        elif selector.startswith("#"):
            token = f'id="{selector[1:]}"'
        else:
            continue
        assert token in panel_js, (
            f"the panel wires {selector!r} but never renders it (looked for {token!r})"
        )


def test_the_panel_has_one_way_back_rather_than_several() -> None:
    """Sub-pages carried their own back buttons, which said where you were
    going only by accident. The crumb strip replaces all of them."""
    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()

    assert "_paintCrumbs" in panel_js and "_trail" in panel_js
    assert "crumb-back" in panel_js
    # And none of the old ones survived the change.
    assert 'id="back"' not in panel_js, "a page still has its own back button"


def test_every_room_screen_has_an_icon_in_the_menu() -> None:
    """The menu is a column of words without them, and a room's screens are
    the half of it people move between most."""
    from custom_components.better_lighting import panel_schema

    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()
    icons = set(re.findall(r"^  (\w+): \"mdi:", panel_js, flags=re.M))
    wanted = {section.value for section in panel_schema.ZONE_SECTIONS} | {
        "scenes",
        "switches",
        "calibrations",
    }
    assert wanted <= icons, f"no icon for: {sorted(wanted - icons)}"


def test_screen_names_carry_no_decoration() -> None:
    """The flow menus lead with an emoji, which reads badly in a breadcrumb
    trail and duplicates the icon the panel draws beside it."""
    from custom_components.better_lighting import panel_schema

    for language in ("en", "de"):
        for name, label in panel_schema.labels(language)["sections"].items():
            assert label[:1].isalnum(), f"{language}/{name} still decorated: {label!r}"


def test_the_trail_knows_every_screen_the_panel_can_open() -> None:
    """A view the trail has no case for lands on the room branch, which names
    the wrong thing and offers the wrong way back."""
    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()
    trail = panel_js[
        panel_js.index("  _trail() {") : panel_js.index("  _paintCrumbs()")
    ]

    kinds = set(re.findall(r'kind:\s*"(\w+)"', panel_js))
    handled = set(re.findall(r'case "(\w+)":', trail))
    # "room" is the default branch, since it is the one with sections.
    assert kinds - handled == {"room"}, (
        f"the trail has no case for: {sorted(kinds - handled - {'room'})}"
    )


def test_every_select_option_has_a_name() -> None:
    """A value with no label renders as its own slug.

    Checking that the selector block exists was not enough: `brighten` and
    `dim` were added to an enum whose block was already there, so both showed
    as raw words in every language including English.
    """
    from custom_components.better_lighting import const

    tables = (
        const.HUB_SPECS,
        const.ZONE_SPECS,
        const.MODE_SPECS,
        const.CONTROLLER_SPECS,
        const.LIGHT_PROFILE_SPECS,
        const.ZONE_SCENE_SPECS,
        const.COLOR_PRESET_SPECS,
        const.mode_rule_specs([]),
    )
    missing = []
    for table in tables:
        for spec in table:
            config = getattr(spec.selector, "config", {}) or {}
            # A free-text select publishes the device's vocabulary, not ours.
            if not (key := config.get("translation_key")) or config.get("custom_value"):
                continue
            named = STRINGS["selector"].get(key, {}).get("options", {})
            for option in config.get("options") or ():
                value = option if isinstance(option, str) else option["value"]
                if value not in named:
                    missing.append(f"{key}.{value}")
    assert not missing, f"unnamed select options: {sorted(set(missing))}"


def test_every_screen_is_one_container() -> None:
    """Screens were a stack of cards, which reads as several pages that
    happen to be underneath each other -- and left the buttons somewhere
    below the fold. One card per screen, built in one place."""
    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()

    # The content pane is only ever written by the helper that builds a page.
    assert panel_js.count("main.innerHTML = `") == 1, (
        "a screen is painting the content pane directly instead of using _page"
    )
    assert "_page(body, left" in panel_js
    # And the shape the helper produces is the one the stylesheet dresses.
    for marker in ('class="card page"', "page-body", "page-foot"):
        assert marker in panel_js, f"the page frame is missing {marker}"


def test_a_click_on_an_icon_still_counts_as_a_click_on_its_button() -> None:
    """Every button in a list carries an icon now, so the click lands on the
    icon and event.target has none of the button's attributes. That is how
    removing a scene from a switch's list quietly stopped working."""
    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()

    assert "event.target.dataset.up" not in panel_js
    assert "event.target.dataset.remove" not in panel_js
    assert panel_js.count("event.currentTarget.dataset") >= 2


def test_the_menu_can_be_opened_on_a_phone() -> None:
    """Home Assistant hides its own sidebar on a narrow screen and the menu
    slides over the content there, so the button that opens it has to be in
    the header -- reachable from the bottom of a long page, not only the top.
    """
    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()

    assert 'id="drawer"' in panel_js and 'id="scrim"' in panel_js
    assert "_setDrawer" in panel_js
    # Both buttons exist and mean different things: ours opens this menu,
    # Home Assistant's opens the one that leads out of the panel.
    assert "hass-toggle-menu" in panel_js
    # And choosing something closes it again.
    assert "const chosen = () => this._setDrawer(false);" in panel_js


def test_leaving_a_changed_screen_goes_through_one_gate() -> None:
    """Every way out asks the same question about unsaved work, and asks it
    in one place rather than each exit remembering to."""
    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()

    assert "_leave(go)" in panel_js and "_dirty" in panel_js
    # The menu, the trail and the back button all go through it.
    assert "this._leave(crumbs[Number(step.dataset.crumb)].go)" in panel_js
    assert (
        "back.onclick = parent?.go ? () => this._leave(parent.go) : null;" in panel_js
    )
    assert "this._leave(() => {" in panel_js
    # And the browser's own back and forward, which cannot go through _leave
    # because the move has already happened by the time we hear about it --
    # so it asks, and puts the entry back when the answer is no.
    assert "_handlePop" in panel_js and "history.pushState" in panel_js

    # Save and Cancel start disabled and are enabled by a change.
    assert 'id="save" disabled' in panel_js and 'id="cancel" disabled' in panel_js
    assert "saveButton.disabled = !this._dirty;" in panel_js

    # And nothing is deleted without being asked about.
    for removal in re.findall(
        r'querySelector\("#(?:remove|delete)"\)\?\.addEventListener\('
        r'"click", async \(\) => \{\s*(.*?)\n',
        panel_js,
    ):
        assert "_confirm()" in removal, f"a delete with no confirmation: {removal!r}"


def test_movement_can_be_turned_off() -> None:
    """Animations are a courtesy. Somebody who has asked their machine for
    fewer of them has asked this page too."""
    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()

    assert "prefers-reduced-motion" in panel_js
    # Including the one that is done in script rather than in the stylesheet,
    # which a stylesheet rule cannot reach.
    assert 'window.matchMedia?.("(prefers-reduced-motion: reduce)").matches' in panel_js


def test_nothing_is_deleted_from_a_list_without_being_asked_about() -> None:
    """Row actions put Delete one tap from a list of things you open, so the
    confirmation matters more there than it does on a settings screen."""
    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()

    wiring = panel_js[panel_js.index("_wireRowActions(main, {") :]
    assert "_wireRowActions" in panel_js
    # The one place the buttons are wired asks, so every list that uses them
    # asks -- which is the reason they are wired in one place.
    handler = panel_js[
        panel_js.index("  _wireRowActions(") : panel_js.index("  _empty(")
    ]
    assert "if (!(await this._confirm())) return;" in handler
    # And a click on one is not also a click into the row.
    assert wiring.count('closest("[data-delete],[data-duplicate]")') >= 4


def test_every_stylesheet_in_the_panel_is_closed_exactly_once() -> None:
    """An extra </style> ends the sheet early and the rest of it is drawn as
    text across the top of the page -- which is what happened, and which no
    test could see because nothing here renders the page.
    """
    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()

    assert panel_js.count("<style>") == panel_js.count("</style>"), (
        "a stylesheet is opened or closed more times than the other"
    )


def test_the_panel_asks_in_its_own_dialog() -> None:
    """The browser's own confirm box is a different application interrupting
    this one. Home Assistant's dialog cannot be reached from a panel loaded on
    its own, so the page has one built from its own parts."""
    panel_js = (COMPONENT / "www" / "better_lighting_panel.js").read_text()

    assert "window.confirm" not in panel_js and "window.alert(" not in panel_js
    assert "_ask({" in panel_js
    # Backing out has to be possible without answering the question.
    assert 'event.key === "Escape"' in panel_js


def test_the_panel_parses_as_a_module() -> None:
    """A broken panel is a blank page, and nothing else here would notice.

    The suite reads this file as text, so a stray character makes every
    assertion about its contents pass while the browser refuses to run any of
    it. Parsed with the same grammar the browser uses -- as a module, because
    `node --check` on a .js file parses it as CommonJS and quietly accepts
    things a module rejects, which is exactly how a mangled comment shipped.
    """
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if node is None:
        pytest.skip("no node to parse with")

    source = (COMPONENT / "www" / "better_lighting_panel.js").read_text()
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as handle:
        handle.write(source)
        copy = handle.name
    try:
        done = subprocess.run(
            [node, "--check", copy], capture_output=True, text=True, check=False
        )
    finally:
        pathlib.Path(copy).unlink(missing_ok=True)
    assert done.returncode == 0, done.stderr
