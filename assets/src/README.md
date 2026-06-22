# Banner & screenshot sources

These files regenerate the images in `assets/`. They are not needed to run the
project — they exist so the branding is reproducible.

- `hero.html` — the banner's hero header (title, tagline, feature chips).
- `terminal.html` — the styled terminal-monitor screenshot.
- `crop.py` — auto-trims whitespace from raw dashboard captures.
- `compose.py` — stacks the hero over a framed dashboard shot into `banner.png`.

## Regenerating

The dashboard screenshots are captured from the real UI in **demo mode** (no
hardware needed), then rendered to PNG with headless Chrome and Pillow:

```bash
# 1. run the dashboard with sample data
python3 dashboard.py --demo        # serves http://127.0.0.1:8040

# 2. capture dark + light (set "default_theme" in config.json per shot)
google-chrome --headless=new --hide-scrollbars --force-device-scale-factor=2 \
  --window-size=1440,1180 --screenshot=dashboard-dark.png \
  --virtual-time-budget=3500 http://127.0.0.1:8040

# 3. render the hero + terminal, trim, and compose
google-chrome --headless=new --force-device-scale-factor=2 --window-size=1280,360 \
  --default-background-color=00000000 --screenshot=hero.png \
  --virtual-time-budget=2000 file://$PWD/assets/src/hero.html
python3 assets/src/crop.py
python3 assets/src/compose.py
```
