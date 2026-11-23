# Privacy LLM: UI/UX Handoff for a Legibility Redesign

**Written 2026-11-23**, from the code as committed at `06935af` (pushed to `origin/main`). Use this to start a separate chat that plans and builds a UI improvement. It is a map of what exists, a measured legibility audit, the constraints a redesign must respect, and a suggested plan. Where this file and the source disagree, the source wins. `CLAUDE.md` holds the standing project rules and `HANDOFF_PHASE2.md` the product, architecture and roadmap.

Everything in the audit (section 6) was measured from the real CSS and token values; nothing is an opinion about taste. Items marked **estimate** were not measured in a browser.

---

## 0. How to use this document

Suggested opening message for the new chat:

> Read UI_HANDOFF.md, then CLAUDE.md. We are planning a legibility-focused UI redesign. Do not change code yet. First propose a plan with acceptance criteria, using section 8 of the handoff as a starting point, and ask me about the open decisions in section 10.

What a new chat should do first:
1. Read this file, `CLAUDE.md`, and skim `static/style.css` (about 23 KB, 145 lines, minified-style rules) and `static/index.html`.
2. Look at the live UI in light and dark themes at three widths (phone 375, tablet 768, desktop 1280) using the scratch-copy method in section 9. Do not rely on this document alone for visual judgement.
3. Agree acceptance criteria (section 8) before touching CSS.

Standing rules that matter here (from `CLAUDE.md`): no React or build tooling; plain HTML, CSS and browser JavaScript; commit locally when a change set is finished and tests pass, push only when asked; never read `data/`, `.env`, `config.json` or `sensitive_terms.txt`; never weaken the artifact sandbox.

---

## 1. Goal and success definition

**Goal:** make the interface easier to read and scan, for long sessions of reading meeting analyses, chat replies, and generated documents. Legibility first; visual style second; no feature changes.

**Done looks like (proposal, to confirm in section 10):**
- Every piece of body text meets WCAG 2.2 AA contrast (4.5:1) in both themes; large text 3:1.
- Nothing a user must read is smaller than a chosen minimum (suggest 13px for secondary text, 15–16px for body).
- Interactive boundaries (inputs, buttons, cards that matter) meet 3:1 non-text contrast, and the keyboard focus ring does too.
- Message line length is comfortable (suggest 60–80 characters).
- Touch targets are at least 44×44 CSS px on phone widths, 24×24 minimum anywhere (WCAG 2.2 target size).
- The page reflows at 320px width and at 200% zoom without horizontal scrolling or lost content.
- Light, dark and "follow the system" all pass the same checks.
- No change to behaviour, privacy messaging or the artifact sandbox.

---

## 2. Constraints a redesign must respect (non-negotiable)

**Stack and process**
- Python and FastAPI serve static files. UI is plain HTML, CSS and ES-module JavaScript under `static/`. No React, no bundler, no CSS framework, no npm. Node is not installed in the dev shell, so the `tests/*.cjs` browser checks cannot run; verify UI manually (section 9).
- Single user, single machine, loopback only (`127.0.0.1:8000`), no login.
- Files under `static/` and `prompts/` are read fresh (hard refresh the page); Python changes need a server restart. Restarting interrupts Telegram and drops any in-memory meeting capture, so ask first.

**Security headers (set in `app.py`, do not weaken)**
- Main app CSP: `default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'self'`.
  - **No inline `<style>` blocks, no `style="…"` attributes in HTML, no inline scripts, no CDN assets.** Setting `element.style.x = …` from JavaScript is allowed (CSSOM), but put presentation in `style.css`.
  - **Web fonts must be self-hosted** (`font-src` falls back to `'self'`). Today there are no web fonts at all (system fonts only). Adding one means bundling the file under `static/` and a licence note.
- Artifact preview runs in an iframe from `/artifact-frame` with its own CSP (`default-src 'none'`, inline scripts and styles only, no network) and `sandbox="allow-scripts"` without `allow-same-origin`. **Do not change this policy or add `allow-same-origin`.** Anything inside the artifact iframe is the model's content, not app chrome; app styles do not reach it.

**Untrusted content**
- Message text, uploaded file text, meeting transcripts and model output are untrusted. Replies are rendered with `marked` then `DOMPurify` (allowed tags are a short list in `static/chat.js`, function `renderMarkdown`); everything else is set with `textContent`. Any new UI that shows such text must keep doing this. Never build UI with `innerHTML` from this content.

**Product messaging**
- The interface repeatedly says what stays local ("Local workspace" badge, footnote "History stays on this device…", download buttons warn the file contains real, unmasked text). Redesigns may reword for clarity but must keep these honest statements visible.

**Accessibility that exists and must not regress:** skip-free landmark structure (`aside`, `main`, `header`, `nav` with labels), `role="log"` on the message list, `aria-live` status line, visible `:focus-visible` outlines, `.sr-only` labels, `aria-expanded` on toggles, Escape handling for the sidebar and dialogs. There are 26 `aria-*` assignments in the JavaScript.

---

## 3. Product context (who this is for)

One executive-style user who reads long, dense output: meeting analyses from noisy transcripts (mostly received through Telegram), chat answers, generated documents ("artifacts") and spreadsheets-as-CSV. They scan, compare, search, copy and pin. They use the web UI at a desktop most of the time, sometimes a phone. Privacy is the product's reason to exist, so trust cues (what is local, what was sent) matter as much as aesthetics.

Behaviour you must understand before redesigning chat: replies **stream** in token by token; the user can **Stop**, **edit and resend** a message, **branch** a conversation from a reply, **pin** messages/files/artifacts so they are always sent, and see a **context note** when older content was left out of a request.

---

## 4. Page inventory

| Route | File(s) | Purpose | Shell |
|---|---|---|---|
| `/` | `index.html`, `chat.js` (module, ~41 KB), `artifacts.js`, `sidebar-resize.js`, `theme.js` | Main workspace: conversation list, chat, meetings, artifact pane | `body.chat-page` |
| `/settings` | `settings.html`, `settings.js` | Model, API key, cost ceiling, sensitive terms, instructions, upload review, context mode, diagnosis | `body.workspace-page` |
| `/models` | `models.html`, `models.js` | Live OpenRouter catalogue with price filters, favourites, per-model spend; table on desktop, cards on phone | `workspace-page`, `.models-wide` |
| `/adviser` | `adviser.html`, `adviser.js` | Adviser chat with a persona library | `workspace-page` |
| `/memory` | `memory.html` | Retired placeholder (Memory is intentionally retired; the page and nav entry remain) | `workspace-page` |
| `/artifact-frame` | generated by `app.py` | Sandboxed iframe document for artifact previews | not styled by the app |

Files: `style.css` is the only stylesheet for every page. `navigation.js` is the shared mobile hamburger for the non-chat pages. `theme.js` (loaded in `<head>` so there is no flash) cycles Auto → Light → Dark and stores the choice. Vendored libraries live in `static/vendor/` (marked, DOMPurify, mermaid).

**Not currently present:** the `/actions` page and the structured meeting record view (line chips, unverified flags, action checkboxes). They were built and then rolled back (commit `13c4c7b` adds them; `3167978` reverts). `git show 13c4c7b -- static/` is a working reference for those components if they return.

---

## 5. Layout system

**Two shells**
- **Chat shell** (`.chat-page`): `aside#sidebar` + `main.conversation` (header, `#messages` scroll area, `footer` composer) + `#artifact-pane`. The page does not scroll; `#messages` does.
- **Workspace shell** (`.workspace-page`): `aside#sidebar` (navigation only) + `.workspace-main` (header + `.workspace-scroll` containing `main.settings-content`).

**Breakpoints in `style.css`** (min-width unless noted): 560 (model filter grid), **760** (the main one: sidebar becomes a persistent column, hamburger hidden, header grows, message padding grows, resizer appears), 900 (register padding), 1000/1100 (adviser and project grids), **1100** (artifact pane docks beside the chat), `max-width:760` (models table becomes cards, model picker goes full screen), 761–1150 (models table drops a column), `print` (hides chrome).

**Key dimensions**
- Sidebar: overlay under 760px, `min(300px, 88vw)`; persistent at 760px+, default `270px`, user-resizable (CSS variable `--sidebar-w`, saved by `sidebar-resize.js`).
- Header: 65px (phone), 77px with 32px side padding (desktop).
- Messages: `#messages` padding 22/18px (phone), 35/38px (desktop). Each `.message` is `max-width:760px`, centred, `font-size:15px`, `line-height:1.7`, `white-space:pre-wrap`.
- Composer: `footer` max 840px, a rounded card with textarea, Attach, Send/Stop.
- Settings: `.settings-content` max 730px, cards of 14px radius, 22px padding.
- Artifact pane: fixed full-screen overlay under 1100px; at 1100px+ a docked column `clamp(400px, 44vw, 780px)`.

---

## 6. Design tokens and the measured legibility audit

### 6.1 Tokens (all in `:root` of `style.css`; dark overrides are repeated three times: `prefers-color-scheme` block, and `[data-theme=dark]`)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--paper` | #faf9f6 | #14181a | page background |
| `--ink` | #243d38 | #e4ebe7 | body text |
| `--muted` | #6d7770 | #96a39d | secondary text |
| `--line` | #dedfd7 | #2b3431 | default borders, input borders |
| `--line2` | #d9d4c8 | #34403c | card/menu borders |
| `--green` | #275c4a | #7cc3a0 | brand, links, primary button |
| `--on-green` | #ffffff | #0e1a14 | text on primary button |
| `--pale` | #edf1e9 | #1e2724 | chips, subtle fills |
| `--card` | #ffffff | #1b2220 | cards, composer, menus |
| `--side` | #f0f1eb | #171d1b | sidebar |
| `--user` / `--user-line` | #eaf0e8 / #dbe4d7 | #223029 / #2c3d34 | your message bubble |
| `--comp-line` | #c7d1c2 | #3a4a43 | composer border |
| `--warn` | #fff5db | #3a321a | notices (truncation, conflicts) |
| `--danger` / `--danger-bg` | #944536 / #f7e7e2 | #e08f7f / #3a2420 | destructive UI |
| `--error` | #a13c2c | #ef8f80 | error text |
| `--focus` | #9cbfad | #4f8a74 | focus ring colour |
| `--accent2` | #7a6a3a | #d1bd7a | secondary accent |

Typography today: body is the system UI stack (`system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`). **Georgia serif** is used for large display headings (page `h1` at 40px, the welcome heading, brand). **Monospace** (`ui-monospace, Consolas`) for code and the api-text panels. No web fonts. Radii: 8–15px; shadows are rare and soft.

### 6.2 Contrast, computed from the tokens above (WCAG ratios)

Text (needs 4.5:1; large text 3:1):

| Pair | Light | Dark |
|---|---|---|
| ink on paper | 11.08 | 14.75 |
| ink on card | 11.67 | 13.37 |
| ink on your message bubble | 10.07 | 11.38 |
| **muted on paper** | **4.41 (fails AA)** | 6.83 |
| muted on card | 4.64 | 6.19 |
| **muted on sidebar** | **4.09 (fails AA)** | 6.53 |
| **muted on pale (chips)** | **4.06 (fails AA)** | 5.85 |
| green on paper | 7.34 | 8.65 |
| on-green on green (primary button) | 7.73 | 8.64 |
| error on paper | 6.24 | 7.58 |
| danger on its background | 5.53 | 5.78 |
| accent2 on paper | 5.05 | 9.59 |

Non-text boundaries (need 3:1):

| Pair | Light | Dark |
|---|---|---|
| **`--line` (input and hairline borders) vs paper** | **1.28** | **1.39** |
| **`--line2` vs paper** | **1.40** | **1.65** |
| **`--comp-line` (composer border) vs card** | **1.58** | **1.73** |
| **focus ring colour vs paper** | **1.91** | 4.44 |

Not computed (do this in the new chat): the effect of the **`opacity`** rules stacked on muted text (values .5, .55, .65, .7, .75, .8 appear on small labels, for example `.attachment-head small{opacity:.7}`), and contrast of text over `--warn`, `--pale` and hover fills.

### 6.3 Type size inventory (from `style.css`)

Font-size declarations by value: **10px ×5**, **11px ×3**, **12px ×12**, 13px ×14, 14px ×11, 15px ×2, 16px ×1, 17px ×1, 18px ×3, 24px ×2, 42px ×1, 46px ×1, plus a few relative values (.72rem to 1.6em). So **20 declarations are 12px or smaller.** Body message text is 15px.

Where the small sizes are used (selector, size):
- 10px: `.eyebrow` (uppercase, letter-spaced section labels), `.message .speaker` ("YOU" / "ASSISTANT" labels), `.footnote` (the privacy reminder under the composer), `#sessions small` (conversation date and channel in the sidebar), `.group-label` (sidebar group headings).
- 11px: `.local-badge`, `.models-table .badge`, `.adviser-message>p`.
- 12px: `.settings-card label` (**every form label in Settings**), `.hint` (explanatory text everywhere), `.message-actions button` (Copy, Edit, Branch, Pin), `.code-block .copy-code`, `.model-label`, `.model-card p`, `.models-table .model-id`, `.archived-group summary`, `.artifact-bar label`, `.artifact-card small`, `.welcome .privacy-note`.
- The context note under replies and pin buttons use ~12.8px (`.8rem`) and `.78rem` (about 12.5px).

Line height is 1.7 on messages (good), 1.5–1.65 elsewhere, and 1.3 in one place. Uppercase transform appears in only two rules, but both are 10px and letter-spaced (the eyebrows and speaker labels).

### 6.4 Findings, ranked by impact on legibility

1. **Secondary text is too small and too faint at once.** Muted grey at 10–12px, in light mode under 4.5:1 on three of its four backgrounds. This is the dominant legibility problem: dates, labels, hints, hints under settings, the sidebar metadata, "YOU/ASSISTANT" and the privacy footnote all fall here.
2. **Form labels are 12px.** Settings is a long form where every label is the smallest text on the page.
3. **Borders are nearly invisible.** Input, card and composer boundaries are 1.3–1.7:1. Fields do not read as fields.
4. **Light-theme focus ring is 1.9:1.** Keyboard users cannot see where they are. Dark is borderline (4.4).
5. **Line length is long.** 760px at 15px is roughly 95–100 characters per line (**estimate**). Comfortable reading is about 60–80.
6. **Action buttons under messages are tiny** (12px text, small padding), as are the pin buttons, the context-note toggle and sidebar row buttons. These are also below recommended touch size on phone widths even though the base `button` rule sets `min-height:44px` (several rules override it with `min-height:0`).
7. **Hierarchy is mostly colour and size, not weight.** Headings, labels and metadata are close in visual weight; the Georgia display type is used for a few headings only, so most pages read as a flat list of cards.
8. **Pixel units block text scaling.** Almost all font sizes are `px`. Browser zoom works, but a changed default font size in the browser or OS does not scale the UI. Moving to `rem` is part of any legibility pass.
9. **Density is high on Settings and Models.** Long single column of cards with 12px labels; the Models page is a dense table with 7 columns on desktop and a card layout under 760px.
10. **Contrast is better in dark mode, so the two themes are not equivalent.** Anything fixed for light must be re-checked in dark.

What already works (keep): body ink contrast is strong in both themes; messages have generous line height; the primary button has very high contrast; the structure and ARIA landmarks are sound; the dark theme is well balanced.

---

## 7. Component inventory (what a redesign touches)

Each entry: where it is built, notes for legibility.

**Sidebar (`aside#sidebar`)**: brand, 10px eyebrow, main nav (Meetings & chat, Adviser chat, Memory, Settings), `+ New conversation`, search box, then conversation rows built in `chat.js` (`refreshSessions`). Rows show title plus a 10px `small` (channel and date), a rename pencil, a "…" menu (`.row-menu`: pin, archive, export) and a delete ×. Groups: pinned, normal, an "Archived" `<details>`. Theme toggle ("Theme: Auto") sits at the bottom. Resizable on desktop (`#sidebar-resizer`).

**Header**: hamburger (phone), editable-looking title (`h1#chat-title`), Export, keyboard-shortcuts button (⌨), "Local workspace" badge (hidden on phone).

**Message list (`#messages`)**
- User bubble (`.message.user .body`, tinted, 14px radius) and assistant text (no bubble). Speaker label above each. A "Pinned" badge and left accent bar appear on pinned messages.
- Assistant body is Markdown (headings, lists, tables wrapped for scroll, code blocks with Copy buttons).
- Under each assistant reply: model label (`.model-label`, 12px), then optional blocks: truncated-reply note with "Regenerate compactly", **context note** (muted `<details>` line "Sent N messages… · M left out", expands to a list with Pin buttons), and the action row (Copy, Pin, Edit/Branch).
- Attachments on a user message render as a collapsed `<details>` "Attached: file (view extracted text)" and per-file Pin buttons.
- Edit mode swaps the body for a textarea with Save/Cancel.
- Meeting sessions show a "Full conversation history" heading and the transcript in a collapsible panel; analysis is long Markdown prose today.
- Empty state: centred "Space to think freely." with a privacy note.
- `#jump-latest` pill appears when you scroll up during a stream.

**Composer (`footer`)**: status line (`#status`, announces progress and errors), attachment chips (`.attachment`, with an optional OCR review textarea), a rounded form card (auto-sized textarea, Attach, Send, Stop), and the 10px footnote.

**Artifacts (`artifacts.js`)**: in a reply, each artifact is a card button (`.artifact-card`: title, type label, "click to open", pin button). Clicking opens `#artifact-pane` with title, version select, Preview/Code tabs, "Run scripts" checkbox, Copy, Download, "Ask the model to fix it" (Mermaid errors), and close. Preview is the sandboxed iframe; Code is a `<pre>`. This toolbar is crowded (up to 8 controls) and wraps awkwardly on narrow widths.

**Dialogs**: keyboard shortcuts (`<dialog id="shortcuts">`), model picker (full-screen on phone), OCR text review (inline in attachment chip).

**Settings (`settings.html`)**: nine cards: Monthly cost ceiling, LLM/OpenRouter (key, model, meeting model), Sensitive terms (large textarea), Custom instructions, File uploads, Context (two selects), Chat diagnosis. A save button and a status line.

**Models (`models.html/js`)**: filter toolbar (search, sort, provider, four price inputs, three checkboxes), a table (desktop) or stacked cards (phone) with Use/Meetings/Favourite buttons and a spend column.

**Adviser (`adviser.html/js`)**: persona library list and editor, conversation list, chat area using the older `.adviser-message` styles (these differ from the main chat styles; a consolidation opportunity).

---

## 8. Suggested plan for the redesign chat

**Phase 0: baseline (small, do first)**
- Capture screenshots of every page and the states listed in section 9, light and dark, at 375, 768 and 1280 widths. Save them outside the repo (or in a git-ignored folder).
- Run a real contrast and target-size pass in the browser (axe-style) to confirm section 6, including the `opacity` and `--warn`/`--pale` cases.
- Agree the acceptance criteria and open decisions in section 10.

**Phase 1: tokens and type scale (highest legibility return, low risk)**
- Define a small type scale in `rem` (for example 0.8125 / 0.875 / 1 / 1.125 / 1.375 / 2) and a minimum size for any text a user must read. Replace the 10 and 11px rules; raise Settings labels and `.hint`.
- Fix `--muted` in light mode to at least 4.5:1 on paper, side and pale (the darkest affected background is `--side`); re-derive dark values from the same rules.
- Introduce a stronger border token for form controls and the composer (3:1), keep the soft hairline for decoration only. Make the light-theme focus ring 3:1.
- Remove or replace stacked `opacity` on text with explicit colour tokens.
- Consolidate the three copies of the dark palette into one source if possible (CSS-only approach: keep light as `:root`, dark in one `[data-theme=dark]` block and one `@media` block that reuses custom properties).

**Phase 2: reading experience**
- Message measure (about 65–75ch) while keeping wide content (tables, code, artifacts) able to break out and scroll.
- Clearer hierarchy: speaker labels, headings inside replies, spacing between turns, a calmer assistant action row (larger targets, visible on keyboard focus, always visible on touch).
- Restyle the context note and pin controls so they are readable but quiet.
- Meeting analysis is long prose today: a better reading layout (section headings, summary first) is possible without data changes; the rolled-back structured record in `13c4c7b` shows one approach.

**Phase 3: navigation, forms, density**
- Sidebar rows: readable metadata size, larger hit areas, clearer active state.
- Settings: group into labelled sections with a sticky save, larger labels, visible field borders.
- Models: reduce columns, keep sort and filter, make the phone card layout the model for desktop where it reads better.
- Artifact toolbar: group controls (primary: Preview/Code, Copy; overflow menu for the rest).

**Phase 4: verification**
- Re-run the audit, both themes, three widths, 200% zoom and 320px reflow, keyboard-only walk-through, and a screen-reader pass of the chat page.
- Keep Python tests green (`.\.venv\Scripts\python.exe -m unittest discover -s tests`, 162 tests at the time of writing). They do not test CSS, so UI regression is manual.

Each phase should be its own commit set. Phases 1 and 2 are mostly `style.css` plus small markup changes and can ship independently.

---

## 9. Working on the UI: method and gotchas

**Run a scratch copy** (never edit or restart the user's live app for UI work):
1. Copy the project to a scratch folder: root `*.py`, `static/`, `prompts/`, and an empty `data/` with a `settings.json` containing `{"model": "test/model"}`.
2. Write sample session JSON files under `data/sessions/` (UTF-8 **without** BOM) for: a plain chat, a chat with an attachment, one with a long assistant reply containing a table and a code block, one with an artifact (several versions), one with a context note (`context` record on an assistant message), and a meeting session (`session_type: "meeting"`, `transcript`, `intelligence` string, `mode: "meeting_ready"`). `HANDOFF_PHASE2.md` section 9 describes the pattern; the session JSON shape is in section 3 there.
3. Run `uvicorn app:app --host 127.0.0.1 --port <spare>` with `TELEGRAM_BOT_TOKEN=` set empty so Telegram does not poll. Do not send real model calls.
4. Drive it with the built-in browser (`navigate`, `computer`, `javascript_tool`, `resize_window` with presets mobile 375 / tablet 768 / desktop, and `colorScheme` light or dark). Delete the scratch folder afterwards.

**Gotchas**
- **Encodings and line endings:** most files are UTF-8 with CRLF. `static/settings.js` is **not valid UTF-8** (it contains a single-byte character); read and write it as latin-1 or edit it with a tool that preserves bytes. Patch with exact string replacement scripts that detect CRLF and preserve it. PowerShell 5.1's `Set-Content -Encoding utf8` writes a BOM; avoid it for source files.
- Reading `static/*.html` in a PowerShell console can show mojibake for `·`, `☰`, `…`; the files themselves are fine UTF-8.
- The CSP blocks inline styles and scripts (section 2). Dev preview in the built-in browser enforces it too.
- The built-in browser's iframes with `sandbox` do not run scripts, and artifact-frame screenshots are often blank on the first capture; take a second one. `javascript_tool` calls time out at about 45 seconds.
- Hard-refresh after CSS or JS changes; no restart is needed for `static/`.
- There is no CSS build step, so `style.css` is hand-maintained: rules are densely packed, several per line. Reformatting it to one rule per line (a pure whitespace change, committed alone) will make the redesign diffs reviewable.
- `chat.js` is large (about 41 KB) and builds nearly all dynamic UI with `document.createElement`. Class names created there are the styling hooks; search it before renaming a class.
- The Python test suite includes API tests that touch `static` only through route status checks. Changing HTML ids used by `chat.js`, `settings.js` or `models.js` will break the pages without failing any Python test.

**States to capture and check** (each in light and dark, phone and desktop): empty chat; short chat; long reply with table, code and lists; streaming reply with Stop and the jump-latest pill; edit mode; error status; truncated-reply note; attachment chips (normal and OCR review); pinned items and context note (collapsed and expanded); artifact card and artifact pane (preview, code, narrow width); sidebar with pinned, normal and archived groups, search results and the row menu; meeting session with transcript panel; Settings top to bottom; Models table and phone cards; Adviser; keyboard shortcuts dialog; focus rings on every control type.

---

## 10. Open decisions for you to make (answer these in the new chat)

1. **Minimum text size and base size.** Suggest 16px body, 14px secondary, nothing below 13px. Is that too large for your screen and how you work?
2. **Message width and density.** Narrower, calmer reading column (about 70ch) versus today's wide column. Do you often read wide tables and code in chat?
3. **Typeface.** Stay on the system stack, or bundle one self-hosted reading font (needs a licence note and a CSP-compliant `@font-face` from `static/`)? Keep Georgia for display headings?
4. **Visual direction.** Keep the warm paper and green identity, or change it? The audit only requires the contrast fixes, not a new look.
5. **Theme behaviour.** Keep Auto/Light/Dark cycling, or add a contrast option?
6. **Sidebar.** Keep a resizable sidebar, and should the default be wider?
7. **Meetings.** Should the meeting reading view be redesigned now (long prose analysis today), and is the rolled-back structured-record UI worth reviving later? Decide this before restyling the meeting panel so the work is not redone.
8. **Scope of consolidation.** Merge the Adviser page's separate message styles into the main chat styles, and retire or hide the placeholder Memory page and nav entry?
9. **Accessibility target.** WCAG 2.2 AA throughout, or AA for text and the chat page only?

---

## 11. Quick reference

**Run the real app:** `Start Privacy LLM.bat`, or `.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000`.

**Python tests:** `.\.venv\Scripts\python.exe -m unittest discover -s tests`

**Key styling hooks (class or id → what it is):** `.chat-page` / `.workspace-page` (shells); `#sidebar`, `#sessions`, `.row-menu`, `.group-label`; `.conversation`, `#messages`, `.message` (`.user`, `.assistant`, `.pinned`), `.speaker`, `.body`, `.model-label`, `.message-actions`; `.truncated-note`, `.context-note`, `.pin-badge`, `.pin-button`, `.file-pins`; `footer`, `#composer`, `#status`, `.attachment`, `.footnote`, `#jump-latest`; `.artifact-card`, `.artifact-row`, `#artifact-pane`, `.artifact-bar`, `.code-block`, `.copy-code`; `.settings-content`, `.settings-card`, `.hint`, `.eyebrow`, `.local-badge`; `.models-*`, `.model-card`, `.model-picker`; `.adviser-*`.

**Where things live:** colours and type in `static/style.css` (`:root` at the top of the token block, dark overrides further down); chat behaviour and DOM building in `static/chat.js`; artifact pane in `static/artifacts.js`; theme toggle in `static/theme.js`; security headers and the artifact-frame policy in `app.py`.

**Related documents:** `CLAUDE.md` (rules), `HANDOFF_PHASE2.md` (product, architecture, roadmap, dev playbook), `PROJECT_HANDOFF_CURRENT.md` (older backend detail; predates most UI work).

**What was not verified while writing this:** no visual review in a browser was done for this document (the audit is computed from CSS and tokens), `opacity` effects and overlay colours were not measured, the line-length figure is an estimate, and mobile layout was not re-checked after the most recent changes.
