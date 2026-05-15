import type { CSSProperties, ReactNode } from 'react';
import type { Tone } from '../types/reports';

export function Badge({ children, tone = 'neutral', title }: { children: ReactNode; tone?: Tone; title?: string }) {
  return <span className={`badge ${tone}`} title={title}>{children}</span>;
}

export function Button({ children, variant = 'default', onClick, disabled, title }: { children: ReactNode; variant?: 'default' | 'ghost' | 'danger' | 'success'; onClick?: () => void; disabled?: boolean; title?: string }) {
  return <button className={`btn ${variant}`} onClick={onClick} disabled={disabled} title={title}>{children}</button>;
}

export function Card({ children, className = '', style }: { children: ReactNode; className?: string; style?: CSSProperties }) {
  return <section className={`card ${className}`} style={style}>{children}</section>;
}

export function CardHeader({ title, subtitle, action }: { title: string; subtitle?: string; action?: ReactNode }) {
  return (
    <div className="card-header">
      <div>
        <h3>{title}</h3>
        {subtitle ? <p>{subtitle}</p> : null}
      </div>
      {action}
    </div>
  );
}

export function MetricCard({ label, value, hint, tone = 'neutral' }: { label: string; value: ReactNode; hint?: string; tone?: Tone }) {
  return (
    <Card className="metric-card">
      <div className="metric-top"><span>{label}</span><Badge tone={tone}>{tone}</Badge></div>
      <div className="metric-value">{value}</div>
      {hint ? <div className="metric-hint">{hint}</div> : null}
    </Card>
  );
}

export function EmptyState({ title, body }: { title: string; body?: string }) {
  return <div className="empty"><strong>{title}</strong>{body ? <p>{body}</p> : null}</div>;
}

export function JsonViewer({ value, maxHeight = 460 }: { value: unknown; maxHeight?: number }) {
  return <pre className="json" style={{ maxHeight }}>{JSON.stringify(value, null, 2)}</pre>;
}

export function KeyValueGrid({ items }: { items: Array<[string, ReactNode]> }) {
  return (
    <div className="kv-grid">
      {items.map(([key, value]) => (
        <div className="kv" key={key}><span>{key}</span><strong>{value}</strong></div>
      ))}
    </div>
  );
}

export function ChipList({ values, tone = 'cyan', empty = 'None' }: { values?: Array<string | number | null | undefined>; tone?: Tone; empty?: string }) {
  const clean = (values ?? []).filter((item): item is string | number => item !== null && item !== undefined && String(item).length > 0);
  if (!clean.length) return <span className="muted">{empty}</span>;
  return <div className="chips">{clean.map((item) => <Badge tone={tone} key={String(item)}>{String(item)}</Badge>)}</div>;
}

export function SectionTitle({ kicker, title, subtitle, right }: { kicker?: string; title: string; subtitle?: string; right?: ReactNode }) {
  return (
    <div className="page-header">
      <div>
        {kicker ? <p className="kicker">{kicker}</p> : null}
        <h2>{title}</h2>
        {subtitle ? <p>{subtitle}</p> : null}
      </div>
      {right}
    </div>
  );
}
