# Brand Alignment Theme Redesign

## Goal

Redesign the amplifier-distro theme to align with the withamplifier brand identity, replacing the current "Neon Edition" aesthetic with a premium, cohesive design system that supports both light and dark modes.

## Background

amplifier-distro is a FastAPI server serving four static HTML apps: a dashboard, onboarding wizard, settings panel, and voice interface. These apps currently use a shared "Neon Edition" theme (`amplifier-theme.css`) with CSS custom properties for light/dark mode.

The current theme has several problems:

- **Light mode is broken** — hardcoded dark-only hex values (e.g., `#3f4a5c` hover backgrounds) bleed through, and backgrounds use pure white instead of warm whites
- **Brand misalignment** — the Monoton font, magenta neon glow effects, and dark-first color palette don't match the withamplifier marketing site's premium Apple/Gemini-inspired identity
- **Token fragmentation** — each app duplicates variable definitions in inline `<style>` blocks instead of importing shared tokens, and uses old names (`--bg-primary`, `--text-primary`, `--accent`) that don't map to the withamplifier design system
- **Hardcoded values** — the voice app and settings app have dozens of hardcoded hex colors that ignore theme tokens entirely

The withamplifier site (a Next.js app) has a mature design system with semantic color tokens, Apple motion curves, generous radii, and lift shadows. The goal is to adopt that system for the distro apps, adapted for functional app UI density.

## Approach

Adopt withamplifier's design tokens AND its premium visual personality (generous radii, Apple motion curves, lift shadows), scaled down for app-density contexts. All four apps will reference a single rewritten `amplifier-theme.css` instead of maintaining their own token definitions. Every hardcoded hex value gets replaced with a semantic token reference.

Reference files in the withamplifier repo (`/home/robotdad/Work/laf/withamplifier/`):
- `tailwind.config.js` — canonical color tokens, typography, spacing
- `app/globals.css` — full design system with CSS custom properties, motion system, section classes
- `.design/PARTICLE-COLOR-STRATEGY.md` — particle color emotional arc (reference only, not being ported)

## Architecture

```
amplifier-theme.css (single source of truth)
├── CSS custom properties: colors, typography, radii, shadows, motion
├── @font-face declarations (Syne, Epilogue, JetBrains Mono)
├── Light mode tokens (default)
├── Dark mode tokens ([data-theme="dark"])
└── Shared component base styles

theme-init.js
└── Sets document.documentElement.dataset.theme = 'dark' | 'light'

Per-app HTML/CSS
├── Imports amplifier-theme.css
├── References only var(--token) values
└── Contains only app-specific layout styles
```

All apps import the shared theme. No app defines its own color, font, or shadow values. Theme switching is driven by a `data-theme` attribute on `<html>` instead of the current `.dark` class approach.

## Components

### 1. Shared Token System (`amplifier-theme.css`)

Complete rewrite of the shared theme file. Replaces all current variable names with withamplifier's semantic naming.

**Light mode (default, `data-theme="light"` or no attribute):**

| Token | Value | Purpose |
|-------|-------|---------|
| `--canvas` | `#FDFCFA` | Page background (warm white) |
| `--canvas-warm` | `#FAF8F5` | Secondary surfaces |
| `--canvas-stone` | `#F5F3F0` | Tertiary surfaces, cards |
| `--canvas-mist` | `#EEEAE5` | Borders, dividers |
| `--ink` | `#1A1A1A` | Primary text |
| `--ink-slate` | `#5C5C5C` | Secondary text |
| `--ink-fog` | `#7A7A7A` | Muted/tertiary text |
| `--signal` | `#5B4DE3` | Primary accent (Signal Purple) |
| `--signal-light` | `#7B6FF0` | Hover accent |
| `--signal-dark` | `#4338B8` | Active/pressed accent |
| `--signal-soft` | `rgba(91,77,227,0.06)` | Subtle accent backgrounds |
| `--signal-glow` | `rgba(91,77,227,0.15)` | Hover accent backgrounds |
| `--success` | `#3D8B40` | Status green |
| `--error` | `#C53030` | Status red |
| `--warning` | `#B8860B` | Status amber |

**Dark mode (`data-theme="dark"` on `<html>`):**

| Token | Value | Notes |
|-------|-------|-------|
| `--canvas` | `#171717` | Elevated dark base |
| `--canvas-warm` | `#1E1E1E` | Secondary surface |
| `--canvas-stone` | `#262626` | Card/panel surface |
| `--canvas-mist` | `#333333` | Borders, dividers |
| `--ink` | `#FAFAFA` | Primary text |
| `--ink-slate` | `#A3A3A3` | Secondary text |
| `--ink-fog` | `#737373` | Muted text |
| `--signal` | `#7B6FF0` | Lighter purple for dark bg contrast |
| `--signal-light` | `#9589F5` | Hover |
| `--signal-soft` | `rgba(123,111,240,0.12)` | Subtle bg on dark |
| `--signal-glow` | `rgba(123,111,240,0.20)` | Hover bg on dark |

### 2. Typography & Logo

**Font stack (all apps):**
- **Headings (h1–h6, wordmark):** Syne, 400–800 weight
- **Body text, UI labels, forms:** Epilogue, 400–700 weight
- **Code blocks, monospace labels, terminal output:** JetBrains Mono, 400–500 weight

All three fonts bundled as local WOFF2 files in `static/fonts/`. Loaded via `@font-face` declarations in the shared theme CSS. No Google Fonts CDN dependency.

**Typography tokens:**
- Tight tracking on display headings: `-0.03em`
- `text-wrap: balance` on headings to prevent orphans
- `-webkit-font-smoothing: antialiased` globally
- Fixed sizing scale (not fluid `clamp()` — app UI has fixed layouts, not responsive marketing sections)

**Logo treatment:**
- Current Monoton + magenta neon glow (`#ff00de`) replaced with **Syne wordmark "amplifier"** in `--signal` purple
- No `text-shadow` glow — clean, confident, matches the withamplifier brand voice
- Appears in the header bar of each app (dashboard, wizard, settings, voice)
- The `&` ampersand logo icon can be used alongside the wordmark where appropriate

**Removed:**
- Monoton font import (Google Fonts CDN link)
- All `text-shadow` neon glow effects
- `neon-logo-sm` CSS class and variants

### 3. Visual Feel — Spacing, Radii, Shadows, Motion

**Border radii:**

| Element | Radius | Source |
|---------|--------|--------|
| Cards, panels | `20px` | Adapted from withamplifier `--radius-card: 24px` (tighter for app context) |
| Buttons | `14px` | From `--radius-button` |
| Inputs, toggles | `10px` | New — between button and subtle |
| Badges, tags | `6px` | From `--radius-subtle` |
| Pill buttons | `980px` | From `--radius-pill` |

**Shadows (three elevation levels, matching withamplifier names):**
- `--shadow-lift` — subtle resting state, cards sitting on the page
- `--shadow-elevate` — hover state, card lifts slightly
- `--shadow-float` — active/modal state, floating above content
- In dark mode, shadows shift to subtle light-edge borders instead (shadows are invisible on dark backgrounds)

**Motion (Apple easing from withamplifier):**
- `--ease-out: cubic-bezier(0.22, 1, 0.36, 1)` — dramatic deceleration for page transitions, card hovers
- `--ease-spring: cubic-bezier(0.175, 0.885, 0.32, 1.275)` — slight overshoot for toggles, buttons
- `--duration-fast: 200ms` — micro-interactions (hover, focus)
- `--duration-normal: 400ms` — component transitions
- No reveal-on-scroll animations (marketing-only pattern)

**Spacing (adapted for app density):**
- Section padding: `40–60px` vertical (vs withamplifier's `80–160px`)
- Card padding: `24–32px` (vs withamplifier's `32–48px`)
- Component gaps follow an 8px grid: `8, 16, 24, 32, 40, 48`

**Skipped from withamplifier (marketing-only patterns):**
- Frosted glass / `backdrop-filter: blur`
- Scroll-snap
- Reveal/stagger scroll animations
- Particle background system
- Semi-transparent section backgrounds

### 4. Theme Switching (`theme-init.js`)

Update from `.dark` class toggle to `data-theme` attribute:

```js
// Before
document.documentElement.classList.add('dark');

// After
document.documentElement.dataset.theme = 'dark';
```

All CSS selectors updated from `:root.dark` / `.dark` to `[data-theme="dark"]`.

## Per-App Changes

### Dashboard (`static/index.html` + `styles.css`)

- App grid cards: `--canvas-stone` background, `--shadow-lift` resting, `--shadow-elevate` + `translateY(-2px)` on hover (withamplifier card-lift pattern)
- Card hover accent: subtle `--signal-soft` background tint (replaces current gradient overlay)
- Logo: Syne wordmark in `--signal`, no glow
- `styles.css` gets replaced — currently duplicates variable definitions instead of importing the shared theme

### Onboarding Wizard (`install_wizard/static/wizard.html`)

- Step indicator dots: `--canvas-mist` default → `--signal` active → `--success` completed (same logic, new tokens)
- Wizard card: `--canvas-stone` background, `20px` radius, `--shadow-lift`
- Provider "detected" state: dashed `--warning` border stays (works semantically), background shifts to `--canvas-warm`
- Module selected state: `--signal-soft` background (replaces hardcoded `rgba(59,130,246,0.1)`)
- Buttons: `.btn-primary` gets `--signal` fill with `--ease-spring` transition

### Settings (`settings/static/settings.html`)

- **Fixes hardcoded hover bug:** `feature-row:hover` and `integration-row:hover` → `var(--canvas-warm)` instead of hardcoded `#3f4a5c`
- Provider icon tints: Anthropic purple → `--signal-soft`, OpenAI green → `--success` with opacity
- Toggle switches: `--canvas-mist` off → `--signal` on, with `--ease-spring` for knob slide
- Status badges: keep green/red/blue semantic meaning, use `--success`, `--error`, `--signal` tokens

### Voice App (`voice/static/index.html`)

- **Fixes hardcoded delegation overlay:** replace `#0d1a2e`, `#1e3a5f`, `#60a5fa` with `--canvas-stone` background, `--canvas-mist` border, `--signal` text
- Status badge backgrounds: semantic tokens with opacity (replaces hardcoded hex values like `#2a2200`)
- Message bubbles: user = `--signal-soft` background, assistant = `--canvas-warm` with `--canvas-mist` border
- Stop/resume buttons: `--error` for stop, `--signal` for resume

### Shared Across All Apps

- Inline `<style>` blocks trimmed — token definitions and shared patterns move to imported `amplifier-theme.css`
- `theme-init.js` updated to use `document.documentElement.dataset.theme`

## Data Flow

Theme state flows in one direction:

```
User preference (localStorage / system media query)
  → theme-init.js reads preference
    → Sets data-theme="light|dark" on <html>
      → CSS [data-theme="dark"] selector activates dark tokens
        → All var(--token) references resolve to dark values
          → UI renders in dark mode
```

No JavaScript runtime needed for token resolution — it's pure CSS custom property inheritance.

## Error Handling

- **Missing font files:** Font stack includes system fallbacks (`system-ui, -apple-system, sans-serif` for body, `monospace` for code) so the UI remains usable if WOFF2 files fail to load
- **Missing theme attribute:** Light mode is the default (`:root` tokens), so if `data-theme` is never set, light mode renders correctly
- **Stale cached CSS:** The theme file path doesn't change, but browsers will pick up new values on reload. Cache-busting via query string can be added if needed during rollout

## Testing Strategy

- **Visual regression:** Manual comparison of all four apps in both light and dark mode before/after
- **Token coverage:** Grep all HTML/CSS files for hardcoded hex color values — the count should drop to near zero
- **Light mode smoke test:** Verify no dark-only hex values remain (the primary bug this fixes)
- **Dark mode contrast:** Verify text readability against WCAG AA contrast ratios for all ink/canvas combinations
- **Font loading:** Confirm local WOFF2 files load correctly with no network requests to Google Fonts
- **Theme toggle:** Verify switching between light and dark mode updates all surfaces, text, accents, and shadows
- **Cross-app consistency:** All four apps should look like they belong to the same product

## Key Files Being Changed

| File | Change |
|------|--------|
| `distro-server/src/amplifier_distro/server/static/amplifier-theme.css` | Complete rewrite (shared token system) |
| `distro-server/src/amplifier_distro/server/static/styles.css` | Rewrite to use new tokens |
| `distro-server/src/amplifier_distro/server/static/theme-init.js` | Update to `data-theme` attribute |
| `distro-server/src/amplifier_distro/server/static/index.html` | Update logo, classes |
| `distro-server/src/amplifier_distro/server/apps/install_wizard/static/wizard.html` | Update tokens and classes |
| `distro-server/src/amplifier_distro/server/apps/settings/static/settings.html` | Fix hardcoded values, update tokens |
| `distro-server/src/amplifier_distro/server/apps/voice/static/index.html` | Fix hardcoded values, update tokens |
| `distro-server/src/amplifier_distro/server/static/fonts/` (new) | WOFF2 font files (Syne, Epilogue, JetBrains Mono) |

## Open Questions

None — all design decisions have been validated.
