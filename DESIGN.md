# CodeCairn Memory Hub visual system

## 1. Theme

Quiet native utility. The Hub should feel like an everyday system surface, not
a presentation deck. Evidence and recall decisions carry the visual interest;
the shell stays neutral.

## 2. Color roles

- `canvas`: `oklch(0.965 0.004 250)`, system grouped background
- `surface`: `oklch(0.995 0.002 250)`, primary content surface
- `sidebar`: `oklch(0.945 0.006 250)`, navigation layer
- `inspector`: `oklch(0.975 0.004 250)`, secondary detail layer
- `label`: `oklch(0.235 0.008 250)`, primary text
- `secondary-label`: `oklch(0.52 0.01 250)`, metadata
- `separator`: `oklch(0.86 0.006 250)`, dividers
- `accent`: `oklch(0.62 0.18 252)`, selection and actions only
- `success`: `oklch(0.62 0.14 150)`, verified or healthy state
- `warning`: `oklch(0.72 0.15 78)`, pending state

## 3. Typography

Use the native Apple system stack for product text and SF Mono or Menlo for
identifiers. Page titles are 34px, weight 700. Section titles are 20px, weight
650. Body text is 13px with 1.5 line height. Metadata is 11px.

## 4. Components

Navigation uses a 10px selected surface with a quiet blue tint. Buttons use a
10px radius and scale to 0.97 while pressed. Text-heavy information defaults to
rows and grouped lists. Cards are reserved for the causal journey and compiled
recall context.

## 5. Layout

Desktop uses a 224px navigation rail and a flexible workspace. The 360px
inspector exists only inside the Memories workbench, where it has a selected
object to explain. Content uses a 24px spacing token. Compact layouts collapse
the inspector below the memory list; phone layouts use a bottom tab bar.

## 6. Depth

Depth comes from background steps and 1px separators. The only shadow is a
subtle elevation on grouped white surfaces. There is no decorative background,
glass blur, gradient, or dark feature panel.

## 7. Guardrails

- Keep one blue accent.
- Do not use serif display type.
- Do not decorate states that can be expressed with text and a status dot.
- Do not turn every section into a card.
- Keep source identifiers visually secondary.
- Preserve evidence, evolution, and recall explanations.
- Present Doctor as a point-in-time snapshot, never as daemon presence.
- Do not invent recall history, last-used time, remote connectivity, or task
  effect.

## 8. Responsive behavior

At 1180px the sidebar becomes compact. At 900px the inspector moves below the
workspace. At 680px navigation becomes a bottom tab bar with safe-area padding.
Every interactive target remains at least 40px.

## 9. Prompt guide

- Build a grouped list on `surface` with 1px `separator`, 10px radius, 13px
  labels, 11px metadata, and no shadow.
- Build a selected navigation row using `accent` at 10% opacity, 10px radius,
  13px weight 600, and a monochrome 16px icon.
- Build an inspector section on `inspector`, with a 20px title, 11px metadata,
  native segmented tabs, and cardless evidence rows.
