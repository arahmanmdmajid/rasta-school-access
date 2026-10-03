---
title: Rasta School Access
emoji: 🚶
colorFrom: yellow
colorTo: green
sdk: static
app_file: index.html
pinned: false
license: mit
short_description: Where children in Pakistan live beyond a 15-minute walk of a school
---

# Rasta

**Where should the next school go?** Rasta finds where children in Pakistan live beyond a
15-minute walk of a school, and which sites an NGO should field-verify first.

- `index.html` — the scrollytelling story (the entry point for this Space)
- `map.html` — the interactive district map, hover catchment, shortlist and AI panel
- `districts/` — 29 precomputed Sindh district bundles, plus their ODbL notice

The map, the hover catchment and the shortlist are static and need no backend. Only the
"Ask the map" panel calls the API, which sleeps on a free tier.

Source and full method: https://github.com/arahmanmdmajid/rasta-school-access
