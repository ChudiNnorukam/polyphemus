import { getRisks } from "@/lib/api";
import { DataTable, ConfidenceBadge, StatusBadge, formatDate } from "@/components/DataTable";
import { StatsBar } from "@/components/StatsBar";
import type { Risk } from "@/lib/api";

export default async function RisksPage() {
  let risks: Risk[] = [];
  try {
    const data = await getRisks({ limit: 100 });
    risks = data.risks;
  } catch {
    // backend not running yet
  }

  const columns = [
    {
      key: "extracted_at",
      header: "Date",
      render: (r: Risk) => (
        <span className="text-gray-400 text-xs whitespace-nowrap">{formatDate(r.extracted_at)}</span>
      ),
    },
    {
      key: "text",
      header: "Risk",
      render: (r: Risk) => <span className="leading-relaxed">{r.text}</span>,
    },
    {
      key: "raised_by",
      header: "Raised By",
      render: (r: Risk) => (
        <span className="text-gray-500 text-xs">{r.raised_by ?? "—"}</span>
      ),
    },
    {
      key: "channel_name",
      header: "Channel",
      render: (r: Risk) => (
        <span className="text-gray-400 text-xs">#{r.channel_name}</span>
      ),
    },
    {
      key: "confidence",
      header: "Confidence",
      render: (r: Risk) => <ConfidenceBadge value={r.confidence} />,
    },
  ];

  return (
    <div>
      <StatsBar />
      <div className="mb-4">
        <h1 className="text-lg font-semibold text-gray-900">Risk Register</h1>
        <p className="text-sm text-gray-500 mt-0.5">Risks and blockers surfaced in Slack</p>
      </div>
      <DataTable
        columns={columns}
        data={risks}
        emptyMessage="No risks extracted yet."
      />
    </div>
  );
}
