import { getStats } from "@/lib/api";

export async function StatsBar() {
  let stats = { decisions: 0, open_actions: 0, risks: 0, drifting: 0 };
  try {
    stats = await getStats();
  } catch {
    // backend not available yet
  }

  const chips = [
    { label: "Decisions",    value: stats.decisions,    color: "text-blue-700 bg-blue-50 border-blue-200" },
    { label: "Open Actions", value: stats.open_actions, color: "text-gray-700 bg-gray-50 border-gray-200" },
    { label: "Risks",        value: stats.risks,        color: "text-yellow-700 bg-yellow-50 border-yellow-200" },
    { label: "Drifting",     value: stats.drifting,     color: "text-red-700 bg-red-50 border-red-200" },
  ];

  return (
    <div className="flex gap-3 mb-6">
      {chips.map((c) => (
        <div
          key={c.label}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg border text-sm font-medium ${c.color}`}
        >
          <span className="text-xl font-semibold">{c.value}</span>
          <span className="text-xs opacity-75">{c.label}</span>
        </div>
      ))}
    </div>
  );
}
