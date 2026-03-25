# Design System — RAD Agent Mission Control

## Product Context
- **What this is:** An operations dashboard that shows the status of RAD Global's automated procurement agents — starting with the CanadaBuys sourcing agent
- **Who it's for:** Internal RAD Global team — procurement ops, management, and anyone who wants to check if the agents are doing their job
- **Space/industry:** Defence & government procurement, B2G sourcing
- **Project type:** Gamified internal dashboard / mission control

## Aesthetic Direction
- **Direction:** Retro-Futuristic Command Center — NASA flight ops meets game HQ
- **Decoration level:** Expressive — radar sweeps, pulsing indicators, grid overlays, CRT scan lines
- **Mood:** Commanding, alive, and fun. The dashboard should feel like mission control for procurement — important work is happening, and the interface celebrates that. Not corporate-boring, not childish — confident and energizing.
- **Reference sites:** SpaceX launch dashboard, Datadog dark mode, Vercel's status page

## Avatar — RAD Agent
A stylized robot operative (SVG-based, not raster) with four emotional states:

| State | Visual | Border Color | Details |
|-------|--------|--------------|---------|
| **Sleeping** | Closed eyes (—), dimmed colors, Zzz floating, coffee cup with steam | `#4A5280` (muted) | Between scheduled runs. Antenna pulses slowly. |
| **Working** | Glowing cyan eyes, typing hands bouncing, spark particles, progress bar filling | `#00D4FF` (cyan) | Active scraping/submission. Eyes pulse with activity. |
| **Success** | Happy eyes (^_^), arms raised, confetti particles, "MISSION COMPLETE" stamp | `#00FF88` (green) | Run completed with zero errors. Celebration pose. |
| **Error** | Wide worried eyes (O_O), red pulsing border, ⚠ warning triangles flashing | `#FF3B5C` (red) | Errors encountered. "RED ALERT" bar at bottom. |

### Gamification Elements
- **XP bar:** Fills as tenders are processed (lifetime counter). Levels up every ~100 tenders.
- **Level titles:** Rookie (1) → Field Agent (3) → Senior Operative (7) → Commander (10) → Legend (15)
- **Mission Briefings:** Run logs are presented as mission debriefs, not boring tables
- **Achievement badges:** Unlockable milestones displayed in a shelf:
  - First Launch — first production run
  - Century — 100 tenders processed
  - Sharpshooter — 10 consecutive zero-error runs
  - Speed Demon — run completed under 3 minutes
  - Weekly Warrior — 30+ tenders in a single weekly scan
  - Thousand — 1,000 tenders processed
  - Iron Streak — 30 consecutive error-free runs
  - Night Owl — manual run after midnight
- **Locked badges:** Shown at 40% opacity with a lock icon and progress toward unlock

## Typography
- **Display/Hero:** Orbitron (weight 600–900) — geometric, techy, used for mission headers, agent name, metric values, section titles. `letter-spacing: 2–3px; text-transform: uppercase;`
- **Body:** Outfit (weight 300–700) — clean geometric sans, readable at small sizes. Used for descriptions, log entries, agent row names.
- **Data/Tables/Mono:** JetBrains Mono (weight 400–700) — solicitation numbers, timestamps, metric labels, schedule strings, status pills. Enable `font-feature-settings: 'tnum' 1` for tabular numbers.
- **Loading:** Google Fonts — `https://fonts.googleapis.com/css2?family=Orbitron:wght@400;500;600;700;800;900&family=Outfit:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap`
- **Scale (rem):** 0.625 (10px label) · 0.6875 (11px mono) · 0.75 (12px small) · 0.8125 (13px body) · 0.875 (14px body-lg) · 1.125 (18px heading) · 2 (32px metric) · 1.75 (28px hero)

## Color
- **Approach:** Expressive — dark base with high-contrast glowing accents. Color is a primary design tool here.

### Core Palette
| Token | Hex | Usage |
|-------|-----|-------|
| `--bg-deep` | `#050816` | Page background, deepest layer |
| `--bg-base` | `#0A0E27` | Primary background |
| `--bg-surface` | `#111738` | Nested surfaces (log entries, badge cards) |
| `--bg-card` | `#151B3D` | Cards, panels, sections |
| `--bg-elevated` | `#1A2248` | Hover states, elevated panels |
| `--cyan` | `#00D4FF` | Primary accent — active states, links, working agent |
| `--orange` | `#FF6B35` | Brand accent — CTAs, alerts, warnings, RAD brand tie-in |
| `--green` | `#00FF88` | Success — completed runs, new tenders, earned badges |
| `--red` | `#FF3B5C` | Error — failed submissions, red alerts |
| `--yellow` | `#FFD93D` | Streaks, achievements, special highlights |
| `--text-primary` | `#E8ECF4` | Main body text |
| `--text-secondary` | `#8892B0` | Descriptions, secondary info |
| `--text-muted` | `#4A5280` | Timestamps, sleeping state, locked badges |
| `--border` | `rgba(0, 212, 255, 0.1)` | Card borders, dividers |

### Glow Effects
Each accent color has a dim (15% opacity fill) and glow (30–40% opacity shadow) variant:
- `--cyan-dim: rgba(0, 212, 255, 0.15)` / `--cyan-glow: rgba(0, 212, 255, 0.4)`
- `--orange-dim: rgba(255, 107, 53, 0.15)` / `--orange-glow: rgba(255, 107, 53, 0.4)`
- `--green-dim: rgba(0, 255, 136, 0.15)` / `--green-glow: rgba(0, 255, 136, 0.3)`
- `--red-dim: rgba(255, 59, 92, 0.15)` / `--red-glow: rgba(255, 59, 92, 0.4)`

### Semantic
| Purpose | Color | Token |
|---------|-------|-------|
| Success | `#00FF88` | `--green` |
| Warning | `#FF6B35` | `--orange` |
| Error | `#FF3B5C` | `--red` |
| Info | `#00D4FF` | `--cyan` |

### Dark Mode
This IS the dark mode. If a light mode is ever needed: invert surfaces to white/gray, reduce accent saturation by 20%, use dark text on light backgrounds.

## Spacing
- **Base unit:** 4px
- **Density:** Comfortable — enough room to breathe without wasting space
- **Scale:** 2xs(2px) xs(4px) sm(8px) md(16px) lg(24px) xl(32px) 2xl(48px) 3xl(64px)

## Layout
- **Approach:** Grid-disciplined — structured columns for the dashboard, no creative asymmetry
- **Grid:** Single column on mobile, 2-col on tablet, max 4-col for metrics on desktop
- **Max content width:** 1280px, centered
- **Border radius:** Hierarchical — sm:8px (buttons, status pills), md:12px (inner cards, badges), lg:16px (metric cards), xl:20px (section panels)
- **Key layout regions:**
  1. Header — logo + title + live status badge
  2. Agent hero — avatar (280px fixed) + status detail panel (fluid)
  3. Metrics — 4-column grid of animated counter cards
  4. Bottom — mission log (fluid) + achievements shelf (340px fixed)

## Motion
- **Approach:** Expressive — animation is a core part of the experience
- **Easing:** enter(ease-out) exit(ease-in) move(ease-in-out)
- **Duration:** micro(50–100ms) short(150–250ms) medium(250–400ms) long(400–700ms)
- **Key animations:**
  - Radar sweep — 6s linear infinite rotate (speeds up to 2s during working state)
  - Scan line — 8s linear infinite translateY across full viewport
  - Pulse — 2s ease-in-out infinite on live status dot
  - XP bar fill — 2s ease-out on load
  - Counter roll-up — 2s cubic-bezier on metric values (triggered by IntersectionObserver)
  - Avatar Zzz — staggered 2s opacity cycle (0.4s offset between each Z)
  - Working sparks — 0.6–0.8s opacity + translateY cycles, 8 particles scattered wide
  - Card hover — 0.2s translateY(-2px) + border-color highlight
  - Metric card top-accent — 2px gradient bar (cyan/green/orange/yellow per card)

### Working State — Enhanced Motion (active during agent runs)
The working state is the most watched moment. The entire dashboard comes alive:

**Avatar enhancements:**
- Chest screen shows animated progress bar (fills over estimated 90s)
- Eyes pulse brightness (opacity 0.7 → 1.0 on 1.5s cycle)
- 8 spark particles scattered across wider area with varied timing
- Robot body gently bobs up/down 2px on 3s ease-in-out cycle
- Typing hands alternate bounce with offset rhythm

**Dashboard-wide effects:**
- Metric cards show skeleton shimmer animation (diagonal gradient sweep)
- Radar sweep speeds up from 6s → 2s rotation
- Status text auto-cycles through stages every ~25s: Launching → Connecting to portal → Scraping tenders → Processing results → Updating dashboard
- Thin progress bar under header fills over estimated run duration (90s)

**Data transitions (on completion):**
- Numbers fade out (0.3s) → update → fade in (0.3s) instead of snapping
- New mission log entry slides in from right with cyan highlight glow (0.5s)
- Achievement unlock gets 1s golden pulse burst animation
- Status badge crossfades between states (0.3s)

## Background Effects
- **Radial gradient overlays:** Subtle cyan glow at top center, orange glow at bottom-right
- **Grid overlay:** 60px × 60px lines at 2% cyan opacity — mission control aesthetic
- **Scan line:** Full-width 4px horizontal bar, 8% cyan opacity, sweeps vertically

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-24 | Initial design system created | Created by /design-consultation for the RAD Agent Mission Control dashboard |
| 2026-03-24 | Retro-Futuristic Command Center aesthetic | Defence/procurement brand + user request for gamification → mission control metaphor bridges both |
| 2026-03-24 | Orbitron + Outfit + JetBrains Mono | Orbitron for techy display impact, Outfit for clean readability, JetBrains Mono for data/code |
| 2026-03-24 | Dark-only palette with glowing accents | Command center = dark by default; glow effects make data pop and feel alive |
| 2026-03-24 | SVG robot avatar with 4 states | SVG keeps it lightweight, states add personality without requiring external assets |
| 2026-03-24 | XP + achievements gamification | Makes agent monitoring engaging instead of a chore — team will actually check it |
