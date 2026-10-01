import { agentColor, agentLabel, fmtSecs } from "@/lib/utils";
import type { AgentSummary } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Bars, Legend } from "@/components/ui/chart";

/** The two charts of the Stats page; loaded on demand so the chart library stays out of the first bundle. */
export default function StatsCharts({ agents }: { agents: AgentSummary[] }) {
  const colors = agents.map((a) => agentColor(a.agent));
  const passData = agents.map((a) => ({ agent: agentLabel(a.agent), pass: a.pass_rate === null ? 0 : Math.round(a.pass_rate * 100) }));
  const wallData = agents.map((a) => ({ agent: agentLabel(a.agent), wall: Math.round(a.avg_wall_s || 0) }));
  return (
    <div className="mb-6 grid gap-4 lg:grid-cols-2">
      <Card className="p-4">
        <div className="text-label mb-2">Pass rate by agent</div>
        <Bars data={passData} x="agent" y="pass" max={100} format={(v) => `${v}%`} colors={colors} />
        <div className="mt-2 flex items-center justify-between"><span className="text-meta">verified attempts that passed, all kinds of work</span><Legend items={agents.map((a, i) => ({ name: agentLabel(a.agent), color: colors[i] }))} /></div>
      </Card>
      <Card className="p-4">
        <div className="text-label mb-2">Time per attempt</div>
        <Bars data={wallData} x="agent" y="wall" format={(v) => fmtSecs(v)} colors={colors} />
        <div className="mt-2 text-meta">average wall time from start to verified</div>
      </Card>
    </div>
  );
}
