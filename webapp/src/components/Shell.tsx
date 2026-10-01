import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Command } from "cmdk";
import { toast, Toaster } from "sonner";
import { AnimatePresence, motion } from "motion/react";
import { Activity, Bell, ChevronRight, Hexagon, Inbox, Moon, Plus, Search, Sun, Wifi, WifiOff } from "lucide-react";
import { useStore } from "@/lib/store";
import { agentColor, agentLabel, cn, firstLine, isLive, liveAttention, sessionOf, short } from "@/lib/utils";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { Tip, TooltipProvider } from "./ui/fields";
import { GoalDialog, HelpDialog } from "./dialogs";
import { InboxList } from "@/pages/Inbox";
import { taskHref } from "./bits";

const NAV = [
  { to: "/lead", label: "Lead", key: "1" },
  { to: "/", label: "Swarm", key: "2" },
  { to: "/inbox", label: "Inbox", key: "3" },
  { to: "/hive", label: "Hive", key: "4" },
  { to: "/tasks", label: "Tasks", key: "5" },
  { to: "/stats", label: "Stats", key: "6" },
  { to: "/agents", label: "Agents", key: "7" },
];

export function Shell() {
  const start = useStore((s) => s.start);
  const connected = useStore((s) => s.connected);
  const error = useStore((s) => s.error);
  const summary = useStore((s) => s.summary);
  const inbox = useStore((s) => s.inbox);
  const notices = useStore((s) => s.notices);
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);
  const [goalOpen, setGoalOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [help, setHelp] = useState(false);
  const nav = useNavigate();
  const loc = useLocation();
  const shownNotices = useRef<Set<string>>(new Set());

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
      const go = () => nav(n.taskId ? `/t/${n.taskId}` : "/inbox");
      const opts = { description: n.body, action: { label: "Open", onClick: go }, duration: 9000 };
      if (n.kind === "permission" || n.kind === "failed") toast.error(n.title, opts);
      else if (n.kind === "question" || n.kind === "usage_limit") toast.warning(n.title, opts);
      else if (n.kind === "done") toast.success(n.title, opts);
      else toast(n.title, opts);
    }
  }, [notices, nav]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable || target.closest(".xterm"));
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
        return;
      }
      if (typing) return;
      if (e.key === "g" && !e.metaKey && !e.ctrlKey) { e.preventDefault(); setGoalOpen(true); return; }
      if (e.key === "?") { e.preventDefault(); setHelp((v) => !v); return; }
      if (e.key === "i") { e.preventDefault(); setDrawer((v) => !v); return; }
      if (e.key === "n") {
        e.preventDefault();
        const live = inbox.filter((it) => it.attention.kind !== "failed");
        const list = live.length ? live : inbox;
        if (!list.length) { toast("Nothing needs you right now"); return; }
        const cur = loc.pathname.split("/")[2];
        const ids = list.map((it) => it.id);
        const i = cur ? ids.indexOf(cur) : -1;
        const next = list[(i + 1) % list.length];
        nav(next.kind === "session" ? `/sessions/${next.id}` : `/tasks/${next.id}`);
        return;
      }
      const navHit = NAV.find((n) => n.key === e.key && !e.metaKey && !e.ctrlKey && !e.altKey);
      if (navHit && !loc.pathname.startsWith("/sessions/") && !loc.pathname.startsWith("/lead")) { nav(navHit.to); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [inbox, loc.pathname, nav]);

  const t = summary?.tasks || {};
  const running = (t.running || 0) + (t.verifying || 0) + (t.claimed || 0);
  const queued = (t.pending || 0) + (t.classified || 0) + (t.assigned || 0);
  const attention = inbox.filter((i) => i.attention.kind !== "failed").length;

  return (
    <TooltipProvider>
      <div className="flex h-full min-h-0 flex-col">
        <header className="flex h-12 shrink-0 items-center gap-4 border-b border-border bg-surface px-4">
          <NavLink to="/" className="flex items-center gap-2">
            <Hexagon className="h-4 w-4 text-fg" strokeWidth={2} />
            <span className="text-[13px] font-semibold tracking-tight">Hiveswarm</span>
          </NavLink>
          <nav className="flex h-full items-center gap-0.5">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.to === "/"}
                className={({ isActive }) => cn(
                  "relative flex h-full items-center gap-1.5 px-2.5 text-[13px] transition-colors duration-150",
                  isActive ? "text-fg after:absolute after:inset-x-2.5 after:bottom-0 after:h-0.5 after:rounded-full after:bg-accent" : "text-muted hover:text-fg",
                )}
              >
                {n.label}
                {n.to === "/inbox" && inbox.length ? (
                  <span className={cn("num rounded-full px-1.5 text-[11px] leading-4", attention ? "bg-accent text-accent-fg font-semibold" : "bg-surface-3 text-muted")}>{inbox.length}</span>
                ) : null}
              </NavLink>
            ))}
          </nav>
          <div className="flex-1" />
          <div className="hidden items-center gap-3 text-[12px] text-muted md:flex">
            <span className="flex items-center gap-1.5"><span className={cn("h-1.5 w-1.5 rounded-full", running ? "bg-fg live-dot" : "bg-dim")} /><span className="num text-fg">{running}</span> running</span>
            <span><span className="num text-fg">{queued}</span> queued</span>
            <span><span className="num text-fg">{t.done || 0}</span> done</span>
            {t.failed ? <span><span className="num text-danger">{t.failed}</span> failed</span> : null}
          </div>
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="sm" onClick={() => setPaletteOpen(true)} className="text-muted">
              <Search className="h-3.5 w-3.5" /> <span className="hidden lg:inline">Search</span> <kbd className="key">⌘K</kbd>
            </Button>
            <Tip label="Keys"><Button variant="ghost" size="icon-sm" onClick={() => setHelp(true)} className="text-muted">?</Button></Tip>
            <Tip label={theme === "dark" ? "Light theme" : "Dark theme"}>
              <Button variant="ghost" size="icon-sm" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} className="text-muted">
                {theme === "dark" ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
              </Button>
            </Tip>
            <Tip label={connected ? `connected · ${summary?.dispatcher_alive ? "dispatcher alive" : "dispatcher down"}` : error || "reconnecting…"}>
              <span className={cn("flex h-7 w-7 items-center justify-center rounded-md", connected ? "text-muted" : "text-danger")}>
                {connected ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}
              </span>
            </Tip>
            <Button variant="ghost" size="icon-sm" onClick={() => setDrawer((v) => !v)} className={cn("text-muted", attention && "text-accent")}>
              <Bell className="h-3.5 w-3.5" />
            </Button>
          </div>
          <Button variant="accent" size="sm" onClick={() => setGoalOpen(true)}>
            <Plus className="h-3.5 w-3.5" /> New goal <kbd className="key border-accent-fg/20 bg-accent-fg/10 text-accent-fg">g</kbd>
          </Button>
        </header>
        <div className="relative flex min-h-0 flex-1">
          <main className="min-w-0 flex-1 overflow-hidden">
            <Outlet />
          </main>
          <AnimatePresence>
            {drawer ? (
              <motion.aside
                initial={{ x: 24, opacity: 0 }}
                animate={{ x: 0, opacity: 1 }}
                exit={{ x: 24, opacity: 0 }}
                transition={{ duration: 0.15, ease: "easeOut" }}
                className="absolute right-0 top-0 z-30 flex h-full w-[440px] max-w-[92vw] flex-col border-l border-border bg-surface shadow-[var(--shadow-pop)]"
              >
                <div className="flex h-11 items-center justify-between border-b border-border px-4">
                  <div className="text-[13px] font-semibold">Inbox <span className="num font-normal text-dim">{inbox.length}</span></div>
                  <Button variant="ghost" size="sm" onClick={() => setDrawer(false)}>close <kbd className="key">i</kbd></Button>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto p-3">
                  <InboxList compact onNavigate={() => setDrawer(false)} />
                </div>
              </motion.aside>
            ) : null}
          </AnimatePresence>
        </div>
        <GoalDialog open={goalOpen} onOpenChange={setGoalOpen} defaultProject={null} />
        <Palette open={paletteOpen} onOpenChange={setPaletteOpen} onGoal={() => setGoalOpen(true)} />
        <HelpDialog open={help} onOpenChange={setHelp} />
        <Toaster
          theme={theme}
          position="bottom-right"
          closeButton
          toastOptions={{ className: "!bg-surface !text-fg !border-border-strong !shadow-[var(--shadow-pop)] !text-[12.5px] !rounded-lg" }}
        />
      </div>
    </TooltipProvider>
  );
}

function Palette({ open, onOpenChange, onGoal }: { open: boolean; onOpenChange: (o: boolean) => void; onGoal: () => void }) {
  const tasks = useStore((s) => s.tasks);
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const live = useMemo(() => tasks.filter(isLive), [tasks]);
  const go = (fn: () => void) => { onOpenChange(false); setQ(""); fn(); };
  return (
    <Command.Dialog open={open} onOpenChange={onOpenChange} label="Command palette" className="fixed left-1/2 top-[14vh] z-[80] w-[92vw] max-w-xl -translate-x-1/2 overflow-hidden rounded-xl border border-border-strong bg-surface shadow-[var(--shadow-pop)]" overlayClassName="fixed inset-0 z-[79] bg-black/50">
      <div className="flex items-center gap-2 border-b border-border px-3">
        <Search className="h-4 w-4 text-dim" />
        <Command.Input value={q} onValueChange={setQ} placeholder="Jump to a task, a session or an action" className="h-11 w-full bg-transparent text-[13px] outline-none placeholder:text-dim" />
        <kbd className="key">esc</kbd>
      </div>
      <Command.List className="max-h-[50vh] overflow-y-auto p-1.5 [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[11px] [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-dim">
        <Command.Empty className="p-4 text-center text-[12px] text-dim">Nothing matches</Command.Empty>
        <Command.Group heading="Actions">
          <Item onSelect={() => go(onGoal)} icon={<Plus className="h-3.5 w-3.5" />} label="New goal" kbd="g" />
          <Item onSelect={() => go(() => nav("/inbox"))} icon={<Inbox className="h-3.5 w-3.5" />} label="Open inbox" kbd="2" />
          <Item onSelect={() => go(() => nav("/hive"))} icon={<Activity className="h-3.5 w-3.5" />} label="Open the Hive feed" kbd="3" />
        </Command.Group>
        {live.length ? (
          <Command.Group heading="In flight">
            {live.map((t) => {
              const a = liveAttention(t);
              return (
                <Item key={t.id} value={`${t.id} ${t.spec} ${t.project}`} onSelect={() => go(() => nav(taskHref(t)))}
                  icon={<span className="h-2 w-2 rounded-full" style={{ background: agentColor(t.claimed_by || sessionOf(t).agent) }} />}
                  label={<span className="flex min-w-0 items-center gap-2"><span className="mono text-muted">{short(t.id)}</span><span className="truncate">{firstLine(t.spec, 70)}</span>{a ? <Badge tone="accent">needs you</Badge> : null}</span>}
                  right={agentLabel(t.claimed_by || sessionOf(t).agent)} />
              );
            })}
          </Command.Group>
        ) : null}
        <Command.Group heading="All tasks">
          {tasks.slice(0, 200).map((t) => (
            <Item key={t.id} value={`${t.id} ${t.spec} ${t.project} ${t.state}`} onSelect={() => go(() => nav(taskHref(t)))}
              icon={<ChevronRight className="h-3.5 w-3.5 text-dim" />}
              label={<span className="flex min-w-0 items-center gap-2"><span className="mono text-muted">{short(t.id)}</span><span className="truncate">{firstLine(t.spec, 70)}</span></span>}
              right={t.state} />
          ))}
        </Command.Group>
      </Command.List>
    </Command.Dialog>
  );
}

function Item({ onSelect, icon, label, right, kbd, value }: { onSelect: () => void; icon?: React.ReactNode; label: React.ReactNode; right?: React.ReactNode; kbd?: string; value?: string }) {
  return (
    <Command.Item value={value} onSelect={onSelect} className="flex cursor-default items-center gap-2.5 rounded-md px-2 py-1.5 text-[13px] text-fg data-[selected=true]:bg-surface-2">
      <span className="flex w-4 justify-center text-muted">{icon}</span>
      <span className="min-w-0 flex-1">{label}</span>
      {right ? <span className="text-meta">{right}</span> : null}
      {kbd ? <kbd className="key">{kbd}</kbd> : null}
    </Command.Item>
  );
}
