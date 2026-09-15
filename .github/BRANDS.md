# Submitting the brand images

HACS and the Home Assistant frontend take an integration's icon and logo from the
[home-assistant/brands](https://github.com/home-assistant/brands) repository, not from this
one. Until that pull request is accepted, `.github/workflows/validate.yaml` skips the brands
check.

To complete it:

1. Fork `home-assistant/brands`.
2. Create `custom_integrations/better_lighting/`.
3. Add `icon.png` (256×256, transparent background) and, optionally, `logo.png`
   (max 256px tall).
4. Open a pull request.
5. Once merged, delete the `ignore: brands` line from `.github/workflows/validate.yaml`.

The images are the one part of this project that has to be drawn rather than written, so they
are deliberately left to a human rather than approximated.
