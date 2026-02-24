import { getDecisions } from "@/lib/api";
import { DataTable, ConfidenceBadge, formatDate } from "@/components/DataTable";
import { StatsBar } from "@/components/StatsBar";
import type { Decision } from "@/lib/api";

export default async function DecisionsPage() {
  let decisions: Decision[] = [];
  try {
    const data = await getDecisions({ limit: 100 });
    decisions = data.decisions;
  } catch {
    // backend not running yet
  }

  const columns = [
    {
      key: "extracted_at",
      header: "Date",
      render: (r: Decision) => (
        <span className="text-gray-400 text-xs whitespace-nowrap">{formatDate(r.extracted_at)}</span>
      ),
    },
    {
      key: "text",
      header: "Decision",
      render: (r: Decision) => <span className="leading-relaxed">{r.text}</span>,
    },
    {
      key: "attributed_to",
      header: "By",
      render: (r: Decision) => (
        <span className="text-gray-500 text-xs">{r.attributed_to ?? "—"}</span>
      ),
    },
    {
      key: "channel_name",
      header: "Channel",
      render: (r: Decision) => (
        <span className="text-gray-400 text-xs">#{r.channel_name}</span>
      ),
    },
    {
      key: "confidence",
      header: "Confidence",
      render: (r: Decision) => <ConfidenceBadge value={r.confidence} />,
    },
    {
      key: "linked_actions",
      header: "Actions",
      render: (r: Decision) =>
        r.linked_actions > 0 ? (
          <span className="text-blue-600 text-xs font-medium">{r.linked_actions}</span>
        ) : (
          <span className="text-gray-300 text-xs">—</span>
        ),
    },
  ];

  return (
    <div>
      <StatsBar />
      <div className="mb-4">
        <h1 className="text-lg font-semibold text-gray-900">Decision Log</h1>
        <p className="text-sm text-gray-500 mt-0.5">Decisions extracted from Slack threads</p>
      </div>
      <DataTable
        columns={columns}
        data={decisions}
        emptyMessage="No decisions extracted yet. Make sure the Slack bot is installed and processing channels."
      />
    </div>
  );
}
