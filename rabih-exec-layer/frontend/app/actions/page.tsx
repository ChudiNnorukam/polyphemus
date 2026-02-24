import { getActions } from "@/lib/api";
import { DataTable, StatusBadge, ConfidenceBadge, formatDate } from "@/components/DataTable";
import { StatsBar } from "@/components/StatsBar";
import type { Action } from "@/lib/api";

export default async function ActionsPage() {
  let actions: Action[] = [];
  try {
    const data = await getActions({ limit: 100 });
    actions = data.actions;
  } catch {
    // backend not running yet
  }

  const columns = [
    {
      key: "action_text",
      header: "Action",
      render: (r: Action) => <span className="leading-relaxed">{r.action_text}</span>,
    },
    {
      key: "assignee_id",
      header: "Owner",
      render: (r: Action) => (
        <span className="text-gray-500 text-xs">{r.assignee_id ?? "unassigned"}</span>
      ),
    },
    {
      key: "due_date",
      header: "Due",
      render: (r: Action) => (
        <span className="text-gray-400 text-xs">
          {r.due_date ? formatDate(r.due_date) : "—"}
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (r: Action) => <StatusBadge value={r.status} />,
    },
    {
      key: "channel_name",
      header: "Channel",
      render: (r: Action) => (
        <span className="text-gray-400 text-xs">#{r.channel_name}</span>
      ),
    },
    {
      key: "created_at",
      header: "Created",
      render: (r: Action) => (
        <span className="text-gray-400 text-xs whitespace-nowrap">{formatDate(r.created_at)}</span>
      ),
    },
    {
      key: "confidence",
      header: "Confidence",
      render: (r: Action) => <ConfidenceBadge value={r.confidence} />,
    },
  ];

  return (
    <div>
      <StatsBar />
      <div className="mb-4">
        <h1 className="text-lg font-semibold text-gray-900">Open Actions</h1>
        <p className="text-sm text-gray-500 mt-0.5">Tasks assigned in Slack threads</p>
      </div>
      <DataTable
        columns={columns}
        data={actions}
        emptyMessage="No actions extracted yet."
      />
    </div>
  );
}
