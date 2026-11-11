/**
 * The app's own key combinations. All of them are Ctrl+Shift+something, so nothing an agent's terminal needs is
 * taken away: a terminal lets these through to the app and keeps every other key.
 *
 *   Ctrl+Shift+H        the Home chat
 *   Ctrl+Shift+1 … 9    a project's workspace, in the order of the Control tab's list
 *   Ctrl+Shift+arrows   move focus between tiles
 *   Ctrl+Shift+Z        zoom the focused tile to the whole workspace, and back
 *   Ctrl+Shift+D        details of the focused tile
 *   Ctrl+Shift+P        the Control tab, and back to the terminals
 */
export type AppKey =
  | { kind: "home" }
  | { kind: "project"; index: number }
  | { kind: "focus"; dir: "left" | "right" | "up" | "down" }
  | { kind: "zoom" }
  | { kind: "detail" }
  | { kind: "control" };

interface KeyLike { ctrlKey: boolean; shiftKey: boolean; altKey: boolean; metaKey: boolean; code: string }

export function appKey(ev: KeyLike): AppKey | null {
  if (!ev.ctrlKey || !ev.shiftKey || ev.altKey || ev.metaKey) return null;
  const c = ev.code;
  if (c === "KeyH") return { kind: "home" };
  if (c === "KeyZ") return { kind: "zoom" };
  if (c === "KeyD") return { kind: "detail" };
  if (c === "KeyP") return { kind: "control" };
  if (/^Digit[1-9]$/.test(c)) return { kind: "project", index: Number(c.slice(5)) - 1 };
  if (c === "ArrowLeft") return { kind: "focus", dir: "left" };
  if (c === "ArrowRight") return { kind: "focus", dir: "right" };
  if (c === "ArrowUp") return { kind: "focus", dir: "up" };
  if (c === "ArrowDown") return { kind: "focus", dir: "down" };
  return null;
}

export function isAppCombo(ev: KeyLike): boolean {
  return appKey(ev) !== null;
}

export const KEY_HELP: [string, string][] = [
  ["Ctrl+Shift+H", "the Home chat: a chat agent that belongs to no project"],
  ["Ctrl+Shift+1 … 9", "a project's workspace, numbered as on the Control tab"],
  ["Ctrl+Shift+← → ↑ ↓", "move focus to the tile on that side"],
  ["Ctrl+Shift+Z", "zoom the focused tile to the whole workspace, and back"],
  ["Ctrl+Shift+D", "details of the focused tile: events, diff, standing orders, actions"],
  ["Ctrl+Shift+P", "the Control tab, and back to the terminals"],
  ["Shift+Esc", "leave a terminal without sending it a key"],
  ["Ctrl+Shift+C / right-click", "copy the selection / paste"],
];
