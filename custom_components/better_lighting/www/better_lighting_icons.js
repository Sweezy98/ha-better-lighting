/**
 * The integration's own icon, as an icon rather than a picture.
 *
 * Home Assistant's sidebar takes an icon name, not an image, so the bulb from
 * the brand icon is redrawn here the way Material Design Icons are drawn: one
 * path, one colour, 24 by 24, inheriting whatever colour the sidebar gives it.
 * It is the same mark -- bulb, the adaptive curve as its filament, two bands
 * for the cap -- with everything that cannot survive at 24 pixels taken out.
 *
 * Registered through `window.customIconsets`, which is how Home Assistant lets
 * anything outside core contribute icons. Named `better-lighting:lamp`.
 */

const LAMP = [
  // The bulb, and the opening inside it wound the other way so a plain
  // nonzero fill leaves a hole rather than a blob.
  "M12 2C8.13 2 5 5.13 5 9c0 2.38 1.19 4.47 3 5.74V17c0 .55.45 1 1 1h6c.55 0" +
    " 1-.45 1-1v-2.26c1.81-1.27 3-3.36 3-5.74 0-3.87-3.13-7-7-7z",
  "M12 4c2.76 0 5 2.24 5 5 0 1.77-.93 3.34-2.33 4.23l-.67.43V16h-4v-2.34l-.67" +
    "-.43C7.93 12.34 7 10.77 7 9c0-2.76 2.24-5 5-5z",
  // The cap.
  "M9 19h6v1.4H9zM10 21.4h4V23h-4z",
  // The filament: the adaptive curve, up through the morning and down again.
  "M9.4 11.9c-.36 0-.6-.4-.44-.72C9.75 9.74 10.8 8.8 12 8.8s2.25.94 3.04 2.38" +
    "c.17.32-.08.72-.44.72-.19 0-.36-.11-.45-.28C13.5 10.4 12.8 9.9 12 9.9s-1.5" +
    ".5-2.15 1.72c-.09.17-.26.28-.45.28z",
].join(" ");

const ICONS = { lamp: LAMP };

window.customIconsets = window.customIconsets || {};
window.customIconsets["better-lighting"] = async (name) => ({
  // An unknown name falls back to the lamp rather than to nothing: a sidebar
  // entry with a blank square is worse than one with the wrong bulb.
  path: ICONS[name] || LAMP,
});
