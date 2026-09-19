# Design — 助小商

A locked design system for this app. Every page redesign reads this file before
emitting code. Do not regenerate per page — extend or amend this file when the
system needs to grow.

## Genre
modern-minimal

## Macrostructure family
- Marketing / auth gate (`/login`): **Letter + identity gate** — brand-led first viewport, then identity choice as the sole job. No feature grids.
- App pages · merchant: **Stat-Led workbench** — asymmetric metrics, next-action strip.
- App pages · customer: **Marquee composer** — creation desk is the hero; 「我的信息」入口宽屏右侧栏 / 窄屏下方（`customer-aside` / `customer-footer-nav`）。Structures stay mutually exclusive with merchant rail.
- Content / admin: defer to same Coral tokens; denser tables allowed.

## Theme
Locked to existing `frontend/src/tokens.css` (Coral). Do not invent a second palette.

- `--color-paper`   oklch(96.5% 0.005 50)
- `--color-paper-2` oklch(94% 0.006 50)
- `--color-paper-3` oklch(91% 0.008 50)
- `--color-ink`     oklch(20% 0.01 35)
- `--color-ink-2`   oklch(26% 0.012 40)
- `--color-rule`    oklch(86% 0.008 50)
- `--color-accent`  oklch(64% 0.165 28)  /* Coral — ≤ 5 % of viewport, highlighter not flood */
- `--color-focus`   same as accent
- `--color-danger` / `--color-ok` as in tokens.css

Atmosphere on auth: warm paper wash + soft accent radial (never purple/blue gradient text).

## Typography
- Display: Geist Variable 600, roman only
- Body:    Geist Variable 400–500
- Mono:    Geist Mono Variable (labels / badges)
- Display tracking: -0.025em
- Type scale: tokens.css `--text-*` / `--text-display`

Chinese fallbacks: PingFang SC / HarmonyOS Sans SC / Microsoft YaHei (self-hosted Latin only).

## Spacing
4-point named scale in `tokens.css` (`--spacing-xs` … `--spacing-lg`). Pages must use named tokens, never raw hex or ad-hoc rem for colour.

## Motion
- Easing: `--ease-soft` = cubic-bezier(0.2, 0, 0.2, 1)
- Durations: `--dur-fast` 120ms · `--dur-base` 200ms
- App: 2–3 intentional motions max (identity swap fade, metric settle, sheet enter)
- Reduced-motion: global cut already in `index.css`

## Microinteractions stance
- Silent success (inline notes, no celebratory toasts)
- Focus delay 0 · hover is colour/border only (no scale+shadow stacks)
- Eight button states preserved

## CTA voice
- Primary: filled Coral pill (`btn--primary`)
- Secondary: paper quiet with hairline (`btn--quiet`)
- Text: neutral link-button (`btn--text`)
- Identity picks on login are **not** twin quiet buttons — merchant carries accent edge; customer stays quiet

## Per-page allowances
- Auth gate MAY use a soft atmospheric wash (Tier-A CSS only).
- App pages MUST NOT use enrichment art — function and hierarchy carry the page.
- Accent placement: title underline, primary CTA, merchant badge, one rule — not card floods.

## What pages MUST share
- Wordmark「助小商」
- Coral accent + Geist pairing
- Pill CTA voice + 44px tap floor
- Token-only colours (slop-test gate 48)

## What pages MAY differ on
- Merchant rail vs customer stack (contractual)
- Auth gate atmosphere vs app density
- Nav chrome density (N9 app-bar + optional wordmark)

## Exports
Canonical values live in `frontend/src/tokens.css`. `frontend-admin` mirrors the same Coral system.
