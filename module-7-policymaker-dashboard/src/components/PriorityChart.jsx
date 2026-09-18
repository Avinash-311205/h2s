/**
 * Bar chart (Recharts) summarising average + max priority score by category.
 *
 * Gives policymakers a one-glance view of which issue types carry the highest
 * urgency across the city, independent of individual hotspots.
 */
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";

function aggregateByCategory(hotspots) {
  const buckets = {};
  for (const h of hotspots) {
    const b = buckets[h.category] || {
      category: h.category,
      avg_score: 0,
      max_score: -Infinity,
      count: 0,
      total: 0,
    };
    b.total += h.priority_score;
    b.max_score = Math.max(b.max_score, h.priority_score);
    b.count += 1;
    buckets[h.category] = b;
  }
  return Object.values(buckets).map((b) => ({
    category: b.category,
    avg_score: Math.round((b.total / b.count) * 10) / 10,
    max_score: b.max_score,
  }));
}

export default function PriorityChart({ hotspots }) {
  const data = aggregateByCategory(hotspots);

  if (data.length === 0) {
    return <p className="empty-note">No hotspot data to chart yet.</p>;
  }

  return (
    <section className="chart">
      <h3 className="chart__title">Priority score by category</h3>
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={data} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis dataKey="category" tick={{ fontSize: 12 }} />
          <YAxis domain={[0, 100]} tick={{ fontSize: 12 }} />
          <Tooltip />
          <Legend />
          <Bar dataKey="avg_score" name="Avg priority" fill="#2563eb" radius={[4, 4, 0, 0]} />
          <Bar dataKey="max_score" name="Max priority" fill="#0f172a" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </section>
  );
}