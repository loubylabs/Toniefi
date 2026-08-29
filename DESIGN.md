---
name: TonieFi Circulation Desk
description: A contemporary library workroom for preparing, organising, and sending story collections.
colors:
  bookcloth: "#12372b"
  bookcloth-deep: "#0b2a20"
  paper: "#f4f6ee"
  paper-raised: "#ffffff"
  ink: "#17201c"
  action: "#b9f227"
  info: "#334fcb"
  rule: "#cdd3c8"
  focus: "#234cff"
  success: "#24752f"
  warning: "#9a5b00"
  failure: "#c72c26"
  ink-muted: "#58625d"
  paper-muted: "#e9ece4"
  bookcloth-ink: "#f5f8ef"
  bookcloth-muted: "#c8d8cf"
typography:
  display:
    fontFamily: '"Avenir Next", Avenir, "Segoe UI", Helvetica, Arial, sans-serif'
    fontSize: "clamp(2.25rem, 4vw, 4rem)"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "-0.04em"
  headline:
    fontFamily: '"Avenir Next", Avenir, "Segoe UI", Helvetica, Arial, sans-serif'
    fontSize: "clamp(1.75rem, 3.2vw, 2.75rem)"
    fontWeight: 790
    lineHeight: 1.1
    letterSpacing: "-0.035em"
  title:
    fontFamily: '"Avenir Next", Avenir, "Segoe UI", Helvetica, Arial, sans-serif'
    fontSize: "1.25rem"
    fontWeight: 790
    lineHeight: 1.15
    letterSpacing: "-0.025em"
  body:
    fontFamily: '"Avenir Next", Avenir, "Segoe UI", Helvetica, Arial, sans-serif'
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  body-small:
    fontFamily: '"Avenir Next", Avenir, "Segoe UI", Helvetica, Arial, sans-serif'
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: '"Avenir Next", Avenir, "Segoe UI", Helvetica, Arial, sans-serif'
    fontSize: "0.75rem"
    fontWeight: 700
    lineHeight: 1.5
    letterSpacing: "normal"
  brand:
    fontFamily: 'Charter, "Iowan Old Style", Georgia, serif'
    fontSize: "2rem"
    fontWeight: 750
    lineHeight: 1
    letterSpacing: "-0.035em"
  jacket:
    fontFamily: 'Charter, "Iowan Old Style", Georgia, serif'
    fontSize: "clamp(1.5rem, 3vw, 2.5rem)"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "-0.04em"
  path:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
rounded:
  stamp: "0.25rem"
  working: "0.5rem"
  pill: "999px"
spacing:
  1: "0.25rem"
  2: "0.5rem"
  3: "0.75rem"
  4: "1rem"
  5: "1.5rem"
  6: "2rem"
  7: "3rem"
  8: "4rem"
components:
  button-action:
    backgroundColor: "{colors.action}"
    textColor: "{colors.bookcloth-deep}"
    typography: "{typography.body}"
    rounded: "{rounded.working}"
    padding: "0.5rem 1rem"
    height: "2.75rem"
  button-prepare:
    backgroundColor: "{colors.info}"
    textColor: "{colors.paper-raised}"
    typography: "{typography.title}"
    rounded: "{rounded.working}"
    padding: "0.5rem 1rem"
    height: "3.75rem"
  button-secondary:
    backgroundColor: "{colors.paper-raised}"
    textColor: "{colors.bookcloth}"
    typography: "{typography.body}"
    rounded: "{rounded.working}"
    padding: "0.5rem 1rem"
    height: "2.75rem"
  button-danger:
    backgroundColor: "{colors.failure}"
    textColor: "{colors.paper-raised}"
    typography: "{typography.body}"
    rounded: "{rounded.working}"
    padding: "0.5rem 1rem"
    height: "2.75rem"
  field:
    backgroundColor: "{colors.paper-raised}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.working}"
    padding: "0.5rem 0.75rem"
    height: "2.75rem"
  status-stamp:
    backgroundColor: "{colors.paper-raised}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.label}"
    rounded: "{rounded.stamp}"
    padding: "0.25rem 0.5rem"
    height: "1.75rem"
  paper-record:
    backgroundColor: "{colors.paper-raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.working}"
    padding: "1rem"
---

# Design System: TonieFi Circulation Desk

## Overview

**Creative North Star: "Circulation Desk"**

TonieFi is a working circulation desk for stories. The interface translates a contemporary library workroom into an operating surface: deep bottle-green bookcloth forms the service chrome, cool ruled paper holds the work, chartreuse marks immediate action, periwinkle carries preparation and information, and full-color cover jackets preserve each collection's identity. The system is dense enough for several independent jobs while staying calm, legible, and materially specific.

This world is operational before it is decorative. Thin rules structure records, square stamps name state in words, and shallow lift separates movable or persistent surfaces. Texture belongs to bookcloth and authored cover fallbacks. Paper remains clean enough for scanning. The shipped Command Desk records FORM seed `17e3c753`.

**Key Characteristics:**

- A persistent bookcloth service index beside a cool paper workspace.
- Compact service labels, square status stamps, and thin ruled records.
- Full-color cover jackets with a bookcloth monogram fallback.
- Chartreuse action ink and periwinkle preparation ink used by role.
- Truthful state language that stays visible during background work and failure.

## Colors

The palette pairs a dark green library binding with cool paper, near-black green ink, one vivid action color, one vivid information color, and explicit semantic inks.

### Primary

- **Bottle-Green Bookcloth** (#12372b): The binding color for authored cover fallbacks, icon plaques, counts, and structural accents.
- **Deep Bookcloth** (#0b2a20): The darker service-index field, mobile navigation field, notification ground, and dialog backdrop source.

### Secondary

- **Chartreuse Action Ink** (#b9f227): The high-attention action color for general affirmative controls, active navigation icons, count badges, and book-jacket monograms. It is always paired with deep bookcloth text when used as a fill.

### Tertiary

- **Periwinkle Information Ink** (#334fcb): The preparation action, queued and extracting state, determinate progress, informational links, and intake guidance.
- **Electric Focus Ink** (#234cff): The universal keyboard focus outline. It remains visually distinct from both action and semantic states.

### Neutral

- **Cool Utility Paper** (#f4f6ee): The page field and the base layer inside expanded work areas.
- **Raised White Paper** (#ffffff): Inputs, dialogs, notices, cards, and records that sit above the ruled workspace.
- **Deep Green Ink** (#17201c): Primary text and heavy section rules.
- **Muted Green Ink** (#58625d): Supporting copy, metadata, hints, and inactive control text.
- **Pale Rule** (#cdd3c8): Thin dividers, field borders, and record boundaries.
- **Muted Paper** (#e9ece4): Hover fills, meter tracks, and quiet inset surfaces.
- **Bookcloth White** (#f5f8ef): Primary text on the service index and other deep fields.
- **Bookcloth Mist** (#c8d8cf): Secondary text and inactive navigation on deep fields.

### Semantic

- **Success Stamp Ink** (#24752f): Ready, completed, available, and connected outcomes.
- **Warning Stamp Ink** (#9a5b00): Forging, caution, unavailable-tool, and disclosure emphasis.
- **Failure Stamp Ink** (#c72c26): Failed, destructive, invalid, and stale-error outcomes.

**The Paired-Ink Rule.** Chartreuse identifies a direct affirmative action or current destination. Periwinkle identifies preparation, information, or a job in motion. Do not exchange their roles.

**The Contrast Pairing Rule.** Use the established foreground and ground pairs. Deep ink on paper is 15.29:1, muted ink on paper is 5.80:1, bookcloth white on deep bookcloth is 14.31:1, bookcloth mist on deep bookcloth is 10.38:1, and deep bookcloth on chartreuse is 11.56:1.

**The State-Is-Words Rule.** Semantic color accompanies a text label, icon, border, or explanation. Color never carries state alone.

## Typography

**Display Font:** Avenir Next with Avenir, Segoe UI, Helvetica, and Arial fallbacks

**Body Font:** Avenir Next with Avenir, Segoe UI, Helvetica, and Arial fallbacks

**Jacket Font:** Charter with Iowan Old Style and Georgia fallbacks

**Path Font:** The system monospace stack for filesystem values only

**Character:** The sans face is compact, direct, and highly scannable across controls and records. Charter supplies the library voice only where the interface behaves like a binding or jacket: the TonieFi wordmark and authored cover fallbacks.

### Hierarchy

- **Display** (800, fluid 2.25rem to 4rem, 1 line-height): Route and Desk headlines with tight tracking and a short measure.
- **Headline** (790, fluid 1.75rem to 2.75rem, 1.1 line-height): Collection titles, loading states, and major section introductions.
- **Title** (790, 1.25rem, 1.15 line-height): Section headings, row titles, and dialog headings.
- **Body** (400, 1rem, 1.5 line-height): Primary explanatory copy and standard controls. Explanatory measures stop near 62 to 65 characters.
- **Body Small** (400, 0.875rem, 1.5 line-height): Metadata, field help, compact actions, and secondary descriptions.
- **Label** (700, 0.75rem, 1.5 line-height): Status stamps, definition labels, counters, captions, and timecodes. Stamps and definition terms may be uppercase.
- **Brand** (750, 2rem, 1 line-height): The wordmark only. It compacts to 1.65rem with the service index at tablet widths.
- **Jacket** (800, fluid 1.5rem to 2.5rem, 1 line-height): Initials inside bookcloth cover fallbacks.
- **Path** (400, 0.875rem, 1.5 line-height): Filesystem paths in system settings only.

**The Jacket Voice Rule.** Serif type appears on the wordmark and bookcloth cover fallbacks. Operational headings, buttons, labels, tables, and forms stay sans.

**The Tight-Heading Rule.** Major headings use negative tracking, short measures, and line heights from 1 to 1.15. Supporting prose returns to the 1.5 body rhythm.

## Layout

The desktop application shell is a two-column grid with a 17.5rem sticky service index and a flexible workspace. The workspace uses fluid outer padding from 1.5rem to 3.5rem. Its paper ruling repeats every 3rem. Desk itself uses a 1.45fr intake column and a 0.8fr live-work column with a fluid gap from 1rem to 2.5rem. The work cart is sticky, viewport-bounded, and separated by a thin vertical rule so active work stays visible beside intake.

At 1279.98px and below, the collection page collapses to one main column and places its two planning panels side by side. At 1199.98px and below, the service index compacts to 14rem. At 1199px and below, Desk becomes a vertical flow, the work cart moves below intake, and its records form a two-column grid. From 760px through 1199px, navigation remains in the left service index.

Below 759.98px, the service index gives way to a fixed four-slot bottom bar. Desk, Library, and Creative Tonies remain first-class destinations; Activity and Settings sit in the labeled More menu. Content becomes one column, cover records retain a narrow jacket column, forms and row controls reflow, and all core controls meet a 44px minimum touch height. The workspace reserves the bottom bar plus safe-area inset. The document keeps a 20rem minimum width and prevents horizontal page scrolling.

Spacing follows the 0.25rem through 4rem scale in the frontmatter. The dominant rhythm is 0.75rem inside compact records, 1rem inside standard controls and panels, 1.5rem between related groups, 2rem around major panels, and 3rem between major workflow regions.

**The Persistent-Work Rule.** On wide Desk views, intake and live work share the first viewport. At narrower widths the cart follows intake in document order and keeps the same records and state detail.

**The One-Paper-Field Rule.** Compose lists from ruled rows and bordered records on the workspace field. Avoid stacks of unrelated cards inside cards.

## Elevation & Depth

Depth is a hybrid of material layering and restrained shadow. The ruled paper field is flat. Raised paper records, dialogs, notifications, mobile menus, accepted source slips, and persistent utilities receive lift according to their need to separate from the workspace. Bookcloth depth comes from low-contrast crossing fibers, not gloss or blur.

### Shadow Vocabulary

- **Raised Paper** (`0 0.5rem 1.5rem rgba(11, 42, 32, 0.12), 0 0.125rem 0.4rem rgba(11, 42, 32, 0.08)`): Loading panels, focused covers, audio controls, and common raised surfaces.
- **Quiet Record** (`0 0.35rem 0.9rem rgba(11, 42, 32, 0.08), 0 0.1rem 0.25rem rgba(11, 42, 32, 0.06)`): Library rows, work-cart records, Tonie rows, and settings sections.
- **Preparation Action** (`0 0.45rem 1rem rgba(51, 79, 203, 0.2), 0 0.125rem 0.35rem rgba(51, 79, 203, 0.16)`): The count-aware Prepare action only.
- **Overlay** (`0 1rem 3rem rgba(11, 42, 32, 0.28)`): Confirmation dialogs above a deep green translucent backdrop.

**The Paper-First Rule.** Borders and tonal layers establish structure before shadow. Shadow confirms a raised role; it does not turn every container into a floating card.

**The Bookcloth Fiber Rule.** Bookcloth uses two subtle repeating linear gradients over a solid green field. Texture remains subordinate to labels, icons, and focus outlines.

## Shapes

The working silhouette is gently squared. Standard fields, buttons, panels, covers, dialogs, and navigation items use the 0.5rem working radius. Status stamps, inset groups, compact navigation items, and small error panels use the 0.25rem stamp radius. Count badges, progress tracks, and capacity meters use the 999px pill radius because their geometry communicates quantity rather than container identity.

Thin 1px pale rules are the default structural edge. Heavy 2px deep-ink rules terminate major regions such as the work-cart heading and the collection page header. Dashed borders belong to empty states. Full-color covers stay square or gently rounded and use `object-fit: cover`; authored fallbacks use bookcloth fiber and serif initials.

**The Stamp-Corner Rule.** State labels stay compact and square-cornered at 0.25rem. Do not turn them into soft filled pills.

**The Meter-Pill Rule.** Reserve full pills for counts and progress. Ordinary buttons and records keep working or stamp corners.

## Components

### Buttons

- **Shape:** Gently squared, with the working radius and a 1px border. Standard controls are at least 2.75rem high.
- **Action:** Chartreuse fill, deep bookcloth text, and a deep bookcloth edge. Hover shifts to the shipped darker chartreuse.
- **Prepare:** Periwinkle fill, white text, 1.25rem type, 3.75rem height, and a focused periwinkle shadow. This count-aware control closes the intake tray.
- **Secondary:** Raised white paper, bottle-green text, and a bottle-green edge. Hover moves to muted paper.
- **Danger:** Failure-red fill and border with white text. It is reserved for confirmed destructive action.
- **Focus:** Every button uses the global 0.1875rem electric-blue outline with a 0.1875rem offset.
- **Mobile:** Core buttons and icon buttons grow to at least 44px high. Primary actions usually expand to the available width.

### Status Stamps

- **Style:** White paper, a 1px current-color edge, the stamp radius, uppercase 0.75rem text, and 800 weight.
- **State:** Queued, Extracting and Sending use information ink. Forging and Warning use warning ink. Ready, Sent and Success use success ink. Failed and Failure use failure ink.
- **Content:** The visible phase names are `Queued`, `Extracting`, `Forging`, `Ready to send`, `Sending`, `Sent`, and `Failed`.

### Cards / Containers

- **Corner Style:** The working radius on records and panels; the stamp radius on inset groups.
- **Background:** Translucent or opaque raised paper over the cool paper workspace.
- **Shadow Strategy:** Quiet records use shallow green-tinted lift. Structural panels can remain border-only.
- **Border:** Thin pale rules organize records. Semantic borders may tint an entire work-cart row while the stamp repeats the state in words.
- **Internal Padding:** Compact records use 0.75rem, standard records use 1rem or 1.5rem, and settings or dialog panels use 2rem.

### Inputs / Fields

- **Style:** Raised white paper, a thin pale rule, working corners, 0.5rem by 0.75rem padding, and a 2.75rem minimum height.
- **Inline Editing:** Row fields may rest on transparent paper with a transparent border, then reveal raised paper and a pale rule on hover or focus.
- **Focus:** The universal electric-blue outline sits outside the field. Invalid fields also receive a failure-red border and adjacent error text.
- **Disabled:** Disabled controls use 0.55 opacity and the blocked cursor. Explanatory copy must say why when the disabled state is consequential.

### Navigation

- **Desktop:** A sticky bookcloth service index uses a vertical list of 3.75rem rows. Inactive items use bookcloth mist. Hover adds a quiet paper wash. The current route adds a translucent chartreuse field, a faint chartreuse border, bookcloth-white text, and a chartreuse icon.
- **Mobile:** A fixed bottom bar uses four equal slots and respects all safe-area insets. The More control opens a raised bookcloth menu for Activity and Settings.
- **State:** Current destinations expose `aria-current="page"`. Activity may add a compact chartreuse count badge backed by current data.

### Live Work Cart

Work-cart records pair a cover jacket with a compact operational body. Each record keeps title or source, a square status stamp, progress copy, real percentage only when available, useful facts, and its next safe action. Active indeterminate work animates a periwinkle or success-colored meter. Failed work keeps the real error and Retry. Once any work is ready, one Open Library action appears below the cart for the whole batch; it never links per row.

### Library Selection Bar

Ticking a finished collection's row in the Library reveals a raised paper bar beneath the list. Its heading states the count selected and the total duration, with a Refresh targets control beside it. Each capacity group is a compact record: a heading naming the group, its exact chapter membership as a ruled list, a Creative Tonie picker with no preselected option, and a two-choice effect control (**Append to the back**, the default, or **Replace everything**). Picker options carry the Tonie's name, its household, and its free space. The Send action stays disabled, and a validation line explains why, until every group names a distinct Tonie whose free space fits the group.

### Tonie Jackets

Use the Creative Tonie's own `imageUrl` wherever a Tonie is named: its row on the Creative Tonies screen, the Library's target cards, the Library's send receipt, and every irreversible confirmation. Where no artwork exists, render the bookcloth square with chartreuse serif initials, matching the Cover Jackets fallback. The picture carries `alt=""`, because the Tonie's name always sits beside it. This is not decoration: the Tonie Cloud ships every Creative Tonie named "Creative Tonie", so two boxes in one household read identically, and the figure is the only thing that tells them apart before an operator renames them.

### Tonie Rename

An expanded Creative Tonie offers its name as an inline field, capped at the upstream 100 characters, saved on change against the name the browser had on screen. A Tonie with no chapters still offers it, because that is the Tonie most in need of a name. The field states plainly that renaming also changes the Tonie in the myTonies app and does not touch its chapters. An emptied name is refused in the browser and never reaches the network.

### Live Send Panel

A Tonie row that is the target of a queued or running send carries a panel beneath its summary, whether or not the row is expanded. It shows the phase stamp, the worker's own progress sentence verbatim, a real percentage when the worker reported one, and a meter in information ink. An unknown percentage stays indeterminate; it is never estimated, and a percentage is never parsed back out of a progress sentence.

### Library Send Receipt

On a successful submit the selection bar does not clear. It becomes a receipt listing each capacity group's target jacket and name, its chapter count, its live phase and its meter, with one Done action for the whole batch. A send that has left the queue reads "Activity holds its result" rather than claiming an outcome the bar never saw.

### Cover Jackets

Use real full-color cover art when available. If no cover exists, render a bottle-green bookcloth fallback with crossing fibers and chartreuse serif initials. Never substitute invented art, counts, durations, storage values, or connection claims.

### Creative Tonies Chapter Selection

Each chapter row on a Tonie carries a checkbox using the standard field styling. The list heading offers Select all beside a danger button labeled Remove N selected, disabled at zero selected. Bulk removal is one whole-list save behind the standard irreversible-action dialog. Clear all chapters remains as the separate one-step wipe.

### Truthful State Surfaces

`Configured` means a complete credential pair exists. `Connected` appears only after a successful connection test in the current browser session. A failed test reads `Connection failed`. Locally saved credentials are described as plain text in SQLite, and two-factor limitations remain visible. Stale remote or local data stays on screen with an explicit stale label and Retry action. `Ready to send` is reserved for forged collections. Irreversible Tonie Cloud changes name the target and state that there is no undo before confirmation.

**The Retained-Truth Rule.** On refresh failure, keep the last known record visible, mark it stale, show the real error where useful, and offer an explicit retry.

**The Safe-Next-Action Rule.** Every empty, failed, extracted, forged, disconnected, or stale state names the next safe action without implying completion that has not occurred.

## Do's and Don'ts

### Do:

- **Do** preserve the Circulation Desk split between bookcloth service chrome and cool paper work surfaces.
- **Do** use thin rules, compact labels, authored jacket fallbacks, and restrained lift to keep dense workflows scannable.
- **Do** keep real cover jackets prominent enough to preserve collection identity across Desk, Library, and the collection page.
- **Do** pair every semantic color with visible words, an icon, a border, or an explanatory message.
- **Do** keep the 0.1875rem focus outline visible and provide 44px controls plus safe-area spacing below 759.98px.
- **Do** use the 150ms to 220ms state-transition family for ordinary feedback, the 650ms source-slip motion for accepted batch intake, and the 1.4s loop only for explicitly indeterminate progress.
- **Do** honor reduced motion by collapsing animation and transition duration to 0.01ms and replacing the moving indeterminate meter with a static centered bar.
- **Do** preserve truthful labels for configured, connected, stale, forged, failed, and irreversible states.

### Don't:

- **Don't** replace the circulation desk with a generic dark dashboard, top-tab shell, or linear wizard.
- **Don't** spread bookcloth texture across paper work areas or let texture compete with text and focus.
- **Don't** exchange chartreuse action ink with periwinkle preparation ink.
- **Don't** turn square status stamps into filled pills or use color as the only state signal.
- **Don't** invent cover art, counts, durations, storage totals, sync times, account claims, or progress percentages.
- **Don't** leave a stale percentage on screen when the current phase cannot measure itself; report no percentage and let the meter go indeterminate.
- **Don't** name a Creative Tonie in a destructive confirmation without also showing its figure.
- **Don't** label extracted work ready, label configured credentials connected, or hide stale data behind an empty state.
- **Don't** remove explicit keyboard alternatives for drag ordering or animate accepted slips and progress against reduced-motion preference.
