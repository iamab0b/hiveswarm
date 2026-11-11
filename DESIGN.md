# Hiveswarm design

How the app looks and behaves, in enough detail that a person or an agent can add a page without asking. The web app (`webapp/`) and the desktop shell follow it; pull requests that touch the UI are reviewed against it.

## Principles

0. **The app is a terminal.** What you see first, and most of the time, is real terminals: the chat agent, the lead, the agents. The chrome around them is one 40 px header and one 28 px title line per tile; everything that is not a terminal lives on the other tab.
1. **Calm by default, loud only when you are needed.** The app watches agents for hours. Everything that does not need a decision from you is neutral; the one thing that does gets the single accent colour. Nothing glows, pulses or tints a whole card.
2. **One accent, three signals.** Honey (`--accent`) marks *needs you* and the primary action. Green means finished and verified, red means failed or destructive. There is no fourth colour: "running", "queued", "working" and "idle" are text next to a neutral dot.
3. **Agents are names, not colours.** An agent is identified by its name in a neutral chip; a 6 px hue dot helps scanning in dense lists and tells chart series apart, never a border, a background or a heading colour.
4. **Hierarchy comes from type and spacing, not boxes.** Few font sizes, one border colour, generous gutters. Sections are separated by whitespace or a hairline, not by nested cards.
5. **Say it in plain words.** Sentence case, no exclamation marks, no emoji, no uppercase shouting. Labels say what the thing is; empty states say what will appear and how to make it appear.

## Tokens

Defined once in `webapp/src/index.css` (`@theme` plus the `:root` and `.dark` blocks) and used everywhere through Tailwind classes (`bg-surface`, `text-muted`, `border-border` …). Never hard-code a colour in a component.

### Colour

| token | light | dark | use |
|---|---|---|---|
| `bg` | `#f7f7f8` | `#0c0c0e` | page background |
| `surface` | `#ffffff` | `#131316` | cards, header, panels |
| `surface-2` | `#f2f2f4` | `#1a1a1f` | hover rows, inputs, nested blocks |
| `surface-3` | `#e9e9ec` | `#232329` | pressed state, active segment |
| `border` | `#e5e5e8` | `#26262c` | every hairline |
| `border-strong` | `#d0d0d6` | `#36363e` | focused inputs, dialogs |
| `fg` | `#141417` | `#ededf0` | text |
| `muted` | `#5f5f68` | `#9a9aa4` | secondary text |
| `dim` | `#8c8c96` | `#64646e` | metadata, placeholders |
| `accent` | `#b7820e` | `#e2ab3a` | needs you, primary action, focus ring, active nav |
| `success` | `#1d8f5a` | `#3ec98a` | done, verified, approve |
| `danger` | `#c93a4b` | `#f0636f` | failed, destructive |

`accent-fg`, `success`/`danger` are used for text, dots and the 2 px left rule of an attention panel; as a background they only appear at 10 % (`bg-accent/10`) behind the one primary button or a selected row. `warn` and `info` are aliases kept for compatibility: `warn` resolves to the accent, `info` to `muted`.

### Type

Inter for text, JetBrains Mono for identifiers, commands, logs and diffs. Sizes (px / line-height):

| role | size | class |
|---|---|---|
| page title | 16 / 24, semibold, tracking −0.01em | `text-title` |
| section label | 12 / 16, medium, `muted` | `text-label` |
| body | 13 / 20 | default |
| secondary | 12 / 18, `muted` | `text-xs` |
| meta | 11 / 16, `dim` | `text-meta` |
| mono | 12 / 18 | `mono` |

Numbers that line up in columns use `num` (tabular figures). Nothing is uppercase-tracked except keyboard keys. Bold is for the one word that matters in a line, never a whole line.

### Space, radius, elevation

- 4 px grid. Page gutter 24 px, section gap 24 px, card padding 16 px, row height 36 px, control height 32 px (`sm` 28, `xs` 24).
- Radius: 6 px controls and chips, 8 px cards and panels, 12 px dialogs and the command palette.
- Elevation: cards are a 1 px `border` on `surface`; dialogs, popovers and toasts add `--shadow-pop`. No other shadows; no inset rules.
- Motion: 150 ms ease-out for appearing and hover; list insertions may slide 4 px. No springs, no bounce, nothing animates continuously except the dot next to a running lane (`live-dot`, opacity only).

## Components

Primitives live in `webapp/src/components/ui/` and follow shadcn's conventions (a `cva` variant map, `cn()` for class merging, Radix for behaviour). Compose from them before writing a new one.

| component | file | notes |
|---|---|---|
| `Button` | `ui/button.tsx` | `primary` (inverted fg/bg, one per view), `accent` (attention actions only), `secondary` (bordered), `ghost`, `destructive` (red text; red fill only inside a confirm dialog), `link`. Sizes `xs sm md lg icon icon-sm icon-xs`. Optional `kbd` hint. |
| `Badge` / `StateBadge` | `ui/badge.tsx` | A dot plus a word. `StateBadge` maps a task state to one of four dots: accent (needs you), success, danger, neutral. No filled pills. |
| `Card`, `CardHeader`, `CardTitle`, `CardSection` | `ui/card.tsx` | A bordered `surface` block. `CardHeader` is a 36 px row with a title and actions. |
| `Input`, `Textarea`, `Select`, `Switch`, `Segmented`, `Tabs`, `Tip`, `Kbd`, `Label` | `ui/fields.tsx` | Inputs are `surface-2` with a hairline; focus shows a 2 px accent ring. |
| `Dialog…` | `ui/dialog.tsx` | 12 px radius, header / body / footer, footer has one primary button on the right. |
| `Markdown` | `ui/markdown.tsx` | react-markdown + GFM, styled with tokens; code blocks in mono on `surface-2`. |
| `PageHeader` | `bits.tsx` | Title, optional count, optional subtitle, actions on the right. No icon in the title. |
| `AgentChip` | `bits.tsx` | Neutral chip: hue dot + name. `lead` adds a crown glyph; `session` a terminal glyph. |
| `AttentionPanel` | `Attention.tsx` | The one loud element: `surface-2` panel, 2 px left rule in accent (red for a high-risk approval), a label (*Needs approval*, *Asks you*, *Waiting for you* …), the text, then actions. Options render as numbered secondary buttons; free text as a single input plus button. |
| `EventLine` / `EventStream` | `EventStream.tsx` | Log lines: time in `dim`, a glyph column, mono body. Only *you*, *failed* and *done* lines carry colour. `dense quiet` inside a tile. |
| `Terminal` | `Terminal.tsx` | xterm.js on an agent's tmux window. `attach` is the real terminal; `mini` draws the agent's screen at its own size, scaled to the tile, read-only. The terminal's own colours are the only place the palette is not the tokens. |
| `Tile` | `Tile.tsx` | One agent in a workspace: a 28 px title line (role word, agent chip, model, title, state, then zoom / details / stop as ghost icon buttons), the attention panel when it needs you, then its terminal or its stream. Focus is `surface-2` on the title line and brighter text, never a coloured border. |
| `Workspace` | `Workspace.tsx` | Chat, lead and agents as columns separated by hairlines that drag; the agents stack by weight (a terminal 3, headless 1.4, ×1.7 when it needs you), in two columns past four, the quietest as title lines past eight. Finished work is a 32 px strip of chips. |
| `DetailOverlay` | `Detail.tsx` | A task's or agent's record over the workspace: tabs for overview, events, diff, standing orders; its actions in the header. Opened with `openDetail(id)`, never a route. |
| `Chart` helpers | `ui/chart.tsx` | recharts with the tokens applied: hairline grid, no gradients, series in `--chart-1…4`, tooltip on `surface` with a border. |

## Patterns

- **Two tabs.** Terminals is a workspace filling the window: no page header, no padding, tiles edge to edge with hairlines between them. Control is one scrolling page, `mx-auto max-w-[1200px] px-6 py-5`, of sections with a `text-label` title in `fg` and one line of `muted` explanation, 40 px apart.
- **Workspaces are switched by key or from Control.** The header never offers a workspace as a button or a tab; it names the one you are in and, in accent, any other that is waiting for you, with its key. All app keys are Ctrl+Shift+something so a terminal keeps every key it needs.
- **Tiles.** The title line identifies (role, agent, what it is doing) and stays one line at any width: as a tile narrows, the step timer, the model and finally the chip give way (container queries on the tile), the title truncates, the state and the buttons stay. A tile that needs you shows the attention panel under its title line and grows; that panel is the only accent in a workspace. An agent sitting at its own prompt is *idle*, not *needs you*.
- **Placeholders.** Where a tile is not there yet (the chat agent is starting, no lead is running), the same space holds a centred title, one `muted` sentence and at most one button.
- **Lists.** Rows of 36 px with a hairline between them inside one card; hover is `surface-2`; the first column is what identifies the row, the last column is time or an action. Tables use the same rows with a `text-label` header and tabular numbers right-aligned.
- **Stats.** Numbers first (a row of `Stat` blocks), then a chart, then the table. Charts answer one question each and have a one-line caption.
- **Dialogs.** Title, one line of description, fields with `Label`s, footer with *Cancel* (ghost) and one primary. Destructive confirmations use the `destructive` button and name the thing being destroyed in the title.
- **Keyboard.** The header carries a muted legend of the workspace keys; the `?` dialog lists them all. Inside a terminal every other key belongs to the agent.
- **Empty states.** Centred, a title in body size and one sentence in `muted` saying what will appear and what to do; no illustration.
- **Theming.** Both themes come from the same tokens; the toggle is in the header. Screenshots in the docs are taken dark.

## Don't

- Don't colour a border, a heading or a background by agent, state or kind.
- Don't put an icon in a page title or a section label.
- Don't introduce a fifth colour (no blue "info", no purple).
- Don't use uppercase tracking for labels, or `font-bold` for whole sentences.
- Don't nest cards. A card contains rows, text or a panel, not more cards.
- Don't animate on a loop, glow, or blur.
- Don't write "ASKS YOU!" or "Oops". Say *Asks you* and *Could not reach the hub*.

## Checking a change

`npm run build` must pass (`tsc -b` is part of it). `python tests/screenshots.py` regenerates `docs/screenshots/`; look at them. `pytest -m e2e` drives both tabs in Chromium (the chat agent starting a project, tiles, focus, zoom, the Control sections) and fails on console errors. The before/after of the first pass that introduced this document is under `docs/screenshots/before/`.
