import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export const SERIES = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)"];

const axis = { stroke: "var(--border-strong)", tick: { fill: "var(--dim)", fontSize: 11 }, tickLine: false, axisLine: false } as const;

function Tip({ active, payload, label, format }: { active?: boolean; payload?: { name: string; value: number; color?: string }[]; label?: string; format: (v: number, name: string) => string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-border-strong bg-surface px-2.5 py-1.5 text-[12px] shadow-[var(--shadow-pop)]">
      {label ? <div className="mb-0.5 text-dim">{label}</div> : null}
      {payload.map((p) => (
        <div key={p.name} className="flex items-center gap-2">
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: p.color }} />
          <span className="text-muted">{p.name}</span>
          <span className="num ml-auto text-fg">{format(p.value, p.name)}</span>
        </div>
      ))}
    </div>
  );
}

/** One bar per row, coloured per row (an agent each), with an optional reference formatter. */
export function Bars({ data, x, y, height = 160, format = (v) => String(v), max, colors }: {
  data: Record<string, unknown>[];
  x: string;
  y: string;
  height?: number;
  format?: (v: number, name: string) => string;
  max?: number;
  colors?: string[];
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -4 }} barCategoryGap="30%">
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis dataKey={x} {...axis} />
        <YAxis {...axis} domain={max !== undefined ? [0, max] : undefined} tickFormatter={(v: number) => format(v, y)} width={44} allowDecimals={false} />
        <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<Tip format={format} />} />
        <Bar dataKey={y} radius={[3, 3, 0, 0]} maxBarSize={36} isAnimationActive={false}>
          {data.map((_, i) => <Cell key={i} fill={(colors || SERIES)[i % (colors || SERIES).length]} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Several series over a shared x axis. */
export function Lines({ data, x, series, height = 160, format = (v) => String(v) }: {
  data: Record<string, unknown>[];
  x: string;
  series: { key: string; name?: string }[];
  height?: number;
  format?: (v: number, name: string) => string;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -4 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis dataKey={x} {...axis} />
        <YAxis {...axis} tickFormatter={(v: number) => format(v, "")} width={44} allowDecimals={false} />
        <Tooltip cursor={{ stroke: "var(--border-strong)" }} content={<Tip format={format} />} />
        {series.map((s, i) => (
          <Line key={s.key} type="monotone" dataKey={s.key} name={s.name || s.key} stroke={SERIES[i % SERIES.length]} strokeWidth={1.5} dot={false} isAnimationActive={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function Legend({ items }: { items: { name: string; color?: string }[] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-meta">
      {items.map((it, i) => (
        <span key={it.name} className="inline-flex items-center gap-1.5"><span className="h-1.5 w-1.5 rounded-full" style={{ background: it.color || SERIES[i % SERIES.length] }} />{it.name}</span>
      ))}
    </div>
  );
}
