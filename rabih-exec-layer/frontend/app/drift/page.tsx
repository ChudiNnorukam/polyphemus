import { getDrift } from "@/lib/api";
import { DataTable, formatDate } from "@/components/DataTable";
import { StatsBar } from "@/components/StatsBar";
import type { DriftEvent } from "@/lib/api";

function DaysOverdueBadge({ days }: { days: number }) {
  const color =
    days > 7  ? "bg-red-100 text-red-800" :
    days > 3  ? "bg-orange-100 text-orange-800" :
                "bg-yellow-100 text-yellow-800";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${color}`}>
      {days}d silent
    </span>
  );
}

export default async function DriftPage() {
  let events: DriftEvent[] = [];
  try {
    const data = await getDrift({ limit: 100 });
    events = data.drift;
  } catch {
    // backend not running yet
  }

  const columns = [
    {
      key: "action_text",
      header: "Action",
      render: (r: DriftEvent) => <span className="leading-relaxed">{r.action_text}</span>,
    },
    {
      key: "assignee_id",
      header: "Owner",
      render: (r: DriftEvent) => (
        <span className="text-gray-500 text-xs">{r.assignee_id ?? "unassigned"}</span>
      ),
    },
    {
      key: "days_overdue",
      header: "Silent For",
      render: (r: DriftEvent) => <DaysOverdueBadge days={r.days_overdue} />,
    },
    {
      key: "action_created_at",
      header: "Committed",
      render: (r: DriftEvent) => (
        <span className="text-gray-400 text-xs whitespace-nowrap">{formatDate(r.action_created_at)}</span>
      ),
    },
    {
      key: "channel_name",
      header: "Channel",
      render: (r: DriftEvent) => (
        <span className="text-gray-400 text-xs">#{r.channel_name}</span>
      ),
    },
    {
      key: "detected_at",
      header: "Detected",
      render: (r: DriftEvent) => (
        <span className="text-gray-400 text-xs whitespace-nowrap">{formatDate(r.detected_at)}</span>
      ),
    },
  ];

  return (
    <div>
      <StatsBar />
      <div className="mb-4">
        <h1 className="text-lg font-semibold text-gray-900">Drift Alerts</h1>
        <p className="text-sm text-gray-500 mt-0.5">
          Commitments made in Slack with no follow-up activity
        </p>
      </div>
      <DataTable
        columns={columns}
        data={events}
        emptyMessage="No drift detected. All commitments are being followed up on."
      />
    </div>
  );
}
