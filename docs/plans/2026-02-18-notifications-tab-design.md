# Design: Promote Notifications to Top-Level Tab

**Date:** 2026-02-18
**Status:** Approved

## Problem

The Configuration tab contains image cards followed by the Notifications section.
With many container cards, users must scroll past all cards to reach notification
settings — this is cumbersome.

## Solution

Promote Notifications to its own top-level tab alongside Images, giving it
equal standing in the nav and eliminating the need to scroll.

## Tab Bar

```
Updates | Images | Notifications | History | Log
```

## Changes

### `templates/index.html`

- Rename "Configuration" tab button label to "Images" (keep `data-tab="config"` and `id="config"` unchanged to avoid JS churn)
- Add `<button class="tab-button" data-tab="notifications">Notifications</button>` to the `.tabs` div
- Add a new `<div id="notifications" class="tab-pane">` containing:
  - An unsaved-banner instance (mirroring the one in the Images pane)
  - An `<h2>Notifications</h2>` heading
  - A Save button (matching Images pane style)
  - The two `.notification-block` divs (ntfy.sh, Webhook) moved from `#notifications-section`
- Remove `#notifications-section` and its wrapper from the `#config` pane

### `static/js/app.js`

- `markUnsaved()` / `markSaved()`: update selectors to target both unsaved-banner instances (switch from `#unsaved-banner` ID to a shared class, e.g. `.unsaved-banner`, or target both IDs explicitly)
- `saveConfig()`: ensure it hides both banners on success
- Tab-switching logic requires no changes (generic `data-tab` pattern handles the new tab automatically)

### `static/css/style.css`

- Remove `.notifications-section` top-border and margin-top rules (these were layout glue for the old in-pane position)
- Keep all `.notification-block`, `.notification-actions`, `.notifications-heading` styles as-is

## Save Behavior

Both tabs call the same underlying `saveConfig()` function. The Notifications
tab has its own Save button and unsaved banner for UX clarity; the separation
is purely visual — both tabs write to the same `config.json`.
