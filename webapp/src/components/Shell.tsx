import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { toast, Toaster } from "sonner";
import { Hexagon, Moon, Sun, Wifi, WifiOff } from "lucide-react";
import { useStore } from "@/lib/store";
import { HOME, cn, isLive, roleOf } from "@/lib/utils";
import { appKey } from "@/lib/keys";
import { Button } from "./ui/button";
import { Tip, TooltipProvider } from "./ui/fields";
import { HelpDialog } from "./dialogs";
import { DetailOverlay } from "./Detail";

/**
 * The frame around the two tabs. Terminals is the app: one workspace at a time, Home or a project. Control is
 * everything else. A workspace is switched with a key or from Control's project list, never from here: the header
 * only says where you are and which other workspace is waiting for you.
 */
export function Shell() {
  const start = useStore((s) => s.start);
  const connected = useStore((s) => s.connected);
  const error = useStore((s) => s.error);
  const summary = useStore((s) => s.summary);
  const inbox = useStore((s) => s.inbox);
  const tasks = useStore((s) => s.tasks);
  const notices = useStore((s) => s.notices);
  const projects = useStore((s) => s.projects);
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);
  const closeDetail = useStore((s) => s.closeDetail);
  const [help, setHelp] = useState(false);
  const nav = useNavigate();
  const loc = useLocation();
  const shownNotices = useRef<Set<string>>(new Set());
  const toastsOf = useRef<Record<string, string[]>>({});
  const names = useMemo(() => Object.keys(projects), [projects]);
  const onControl = loc.pathname.startsWith("/control");
  const current = onControl ? null : loc.pathname.startsWith("/p/") ? decodeURIComponent(loc.pathname.slice(3).split("/")[0]) : HOME;
  const lastTerminals = useRef("/");
  if (!onControl) lastTerminals.current = loc.pathname;

  const keyOf = (project: string) => {
    if (project === HOME) return "Ctrl+Shift+H";
    const i = names.indexOf(project);
    return i >= 0 && i < 9 ? `Ctrl+Shift+${i + 1}` : "the Control tab";
  };

  useEffect(() => {
    start();
    if ("Notification" in window && Notification.permission === "default") {
      const ask = () => { Notification.requestPermission().catch(() => undefined); window.removeEventListener("pointerdown", ask); };
      window.addEventListener("pointerdown", ask);
    }
  }, [start]);

  useEffect(() => {
    for (const n of notices) {
      if (shownNotices.current.has(n.id)) continue;
      shownNotices.current.add(n.id);
      const project = useStore.getState().taskById[n.taskId]?.project;
      if (project && project === current) continue;  // its tile is on screen and says the same thing
      if (project) (toastsOf.current[project] ||= []).push(n.id);
      const opts = { id: n.id, description: n.body + (project ? ` · ${keyOf(project)}` : ""), duration: 9000 };
      if (n.kind === "permission" || n.kind === "failed") toast.error(n.title, opts);
      else if (n.kind === "question" || n.kind === "usage_limit") toast.warning(n.title, opts);
      else if (n.kind === "done") toast.success(n.title, opts);
      else toast(n.title, opts);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notices]);

  // arriving in a workspace answers the toasts that pointed there
  useEffect(() => {
    if (!current) return;
    for (const id of toastsOf.current[current] || []) toast.dismiss(id);
    toastsOf.current[current] = [];
  }, [current]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const k = appKey(e);
      if (k) {
        e.preventDefault();
        e.stopPropagation();
        if (e.repeat) return;
        if (k.kind === "home") { closeDetail(); nav("/"); return; }
        if (k.kind === "project") {
          const name = names[k.index];
          if (name) { closeDetail(); nav(`/p/${name}`); } else toast(`There is no project ${k.index + 1} yet`);
          return;
        }
        if (k.kind === "control") { closeDetail(); nav(onControl ? lastTerminals.current : "/control"); return; }
        if (!onControl) window.dispatchEvent(new CustomEvent("hm:tile-key", { detail: k }));
        return;
      }
      const target = e.target as HTMLElement | null;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable || target.closest(".xterm"));
      if (!typing && e.key === "?") { e.preventDefault(); setHelp((v) => !v); }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [names, nav, onControl, closeDetail]);

  // which other workspaces have an agent waiting for you
  const waiting = useMemo(() => {
    const by: Record<string, number> = {};
    for (const it of inbox) {
      if (it.attention.kind === "input" || it.attention.kind === "failed") continue;
      by[it.project] = (by[it.project] || 0) + 1;
    }
    return Object.entries(by).filter(([p]) => p !== current);
  }, [inbox, current]);

  const running = useMemo(() => tasks.filter((x) => isLive(x) && roleOf(x) !== "chat").length, [tasks]);
  const tab = "relative flex h-full items-center px-2.5 text-[13px] transition-colors duration-150";
  const active = "text-fg after:absolute after:inset-x-2.5 after:bottom-0 after:h-0.5 after:rounded-full after:bg-accent";

  return (
    <TooltipProvider>
      <div className="flex h-full min-h-0 flex-col">
        <header className="flex h-10 shrink-0 items-center gap-4 border-b border-border bg-surface px-3">
          <span className="flex items-center gap-2">
            <Hexagon className="h-4 w-4 text-fg" strokeWidth={2} />
            <span className="text-[13px] font-semibold tracking-tight">Hiveswarm</span>
          </span>
          <nav className="flex h-full items-center gap-0.5">
            <NavLink to={lastTerminals.current} className={cn(tab, !onControl ? active : "text-muted hover:text-fg")} data-tab="terminals">Terminals</NavLink>
            <NavLink to="/control" className={cn(tab, onControl ? active : "text-muted hover:text-fg")} data-tab="control">Control</NavLink>
          </nav>
          {!onControl ? (
            <span className="flex min-w-0 items-center gap-2 text-[12px]" data-current-workspace={current === HOME ? "home" : current || ""}>
              <span className="text-dim">workspace</span>
              <span className="truncate font-medium text-fg">{current === HOME ? "Home" : current}</span>
              {current && current !== HOME && names.indexOf(current) >= 0 && names.indexOf(current) < 9 ? <kbd className="key">Ctrl+Shift+{names.indexOf(current) + 1}</kbd> : null}
            </span>
          ) : null}
          {waiting.length ? (
            <span className="flex min-w-0 items-center gap-3 truncate text-[12px]" data-waiting>
              {waiting.slice(0, 3).map(([p, n]) => (
                <span key={p} className="flex shrink-0 items-center gap-1.5 text-accent">
                  <span className="h-1.5 w-1.5 rounded-full bg-accent" />
                  <span className="font-medium">{p === HOME ? "Home" : p}</span> needs you{n > 1 ? ` (${n})` : ""}
                  <kbd className="key">{keyOf(p)}</kbd>
                </span>
              ))}
            </span>
          ) : null}
          <div className="flex-1" />
          <div className="hidden items-center gap-2 text-[11px] text-dim xl:flex" data-key-legend>
            <span><kbd className="key">Ctrl+Shift</kbd> +</span>
            <span><kbd className="key">H</kbd> home</span>
            <span><kbd className="key">1–9</kbd> projects</span>
            <span><kbd className="key">←→↑↓</kbd> focus</span>
            <span><kbd className="key">Z</kbd> zoom</span>
            <span><kbd className="key">P</kbd> control</span>
          </div>
          <span className="hidden items-center gap-1.5 text-[12px] text-muted md:flex">
            <span className={cn("h-1.5 w-1.5 rounded-full", running ? "bg-fg live-dot" : "bg-dim")} /><span className="num text-fg">{running}</span> at work
          </span>
          <div className="flex items-center gap-0.5">
            <Tip label="Keys"><Button variant="ghost" size="icon-sm" onClick={() => setHelp(true)} className="text-muted" aria-label="keys">?</Button></Tip>
            <Tip label={theme === "dark" ? "Light theme" : "Dark theme"}>
              <Button variant="ghost" size="icon-sm" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} className="text-muted" aria-label="theme">
                {theme === "dark" ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
              </Button>
            </Tip>
            <Tip label={connected ? `connected · ${summary?.dispatcher_alive ? "dispatcher alive" : "dispatcher down"}` : error || "reconnecting"}>
              <span className={cn("flex h-7 w-7 items-center justify-center rounded-md", connected ? "text-muted" : "text-danger")}>
                {connected ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}
              </span>
            </Tip>
          </div>
        </header>
        <main className="min-h-0 min-w-0 flex-1 overflow-hidden">
          <Outlet />
        </main>
        <DetailOverlay />
        <HelpDialog open={help} onOpenChange={setHelp} />
        <Toaster
          theme={theme}
          position="bottom-right"
          offset={{ bottom: 44, right: 12 }}
          closeButton
          toastOptions={{ className: "!bg-surface !text-fg !border-border-strong !shadow-[var(--shadow-pop)] !text-[12.5px] !rounded-lg" }}
        />
      </div>
    </TooltipProvider>
  );
}
