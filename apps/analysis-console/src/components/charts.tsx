import { fmtMs, titleCase } from '../lib/format';
import type { Tone } from '../types/reports';

export function DistributionBars({ data }: { data?: Record<string, number> }) {
  const entries = Object.entries(data ?? {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, value]) => value));
  if (!entries.length) return <div className="empty small">No distribution data.</div>;
  return (
    <div className="dist-list">
      {entries.map(([label, value]) => (
        <div className="dist-row" key={label}>
          <div className="dist-label"><span>{titleCase(label)}</span><strong>{value}</strong></div>
          <div className="bar"><span style={{ width: `${(value / max) * 100}%` }} /></div>
        </div>
      ))}
    </div>
  );
}

export function Sparkline({ values }: { values: number[] }) {
  const width = 420;
  const height = 92;
  const max = Math.max(1, ...values);
  const points = values.map((value, index) => {
    const x = values.length === 1 ? 0 : (index / (values.length - 1)) * width;
    const y = height - (value / max) * (height - 10) - 5;
    return `${x},${y}`;
  }).join(' ');
  return (
    <svg className="sparkline" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Latency sparkline">
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      {values.map((value, index) => {
        const x = values.length === 1 ? 0 : (index / (values.length - 1)) * width;
        const y = height - (value / max) * (height - 10) - 5;
        return <circle key={index} cx={x} cy={y} r="4" />;
      })}
    </svg>
  );
}

export function LatencyBudget({ value }: { value?: number }) {
  const ms = value ?? 0;
  const pct = Math.min(100, (ms / 3000) * 100);
  const tone: Tone = ms < 250 ? 'green' : ms < 1000 ? 'cyan' : ms < 3000 ? 'yellow' : 'red';
  return (
    <div className="budget">
      <div className="budget-top"><span>{fmtMs(ms)}</span><b>{tone}</b></div>
      <div className="bar"><span className={tone} style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

export function WaterfallRows({ rows }: { rows: Array<{ label: string; ms: number; status?: string }> }) {
  const max = Math.max(1, ...rows.map((row) => row.ms));
  return (
    <div className="waterfall">
      {rows.map((row) => (
        <div className="waterfall-row" key={row.label}>
          <span>{row.label}</span>
          <div className="bar"><span style={{ width: `${(row.ms / max) * 100}%` }} /></div>
          <strong>{fmtMs(row.ms)}</strong>
        </div>
      ))}
    </div>
  );
}
