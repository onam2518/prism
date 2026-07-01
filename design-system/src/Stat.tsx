import * as React from 'react';
import './theme.css';
import './components.css';

export interface StatProps {
  value: React.ReactNode;
  label: string;
  /** 값을 Primary 강조 */
  accent?: boolean;
  /** 증감 표시 (예: "+12%") */
  delta?: string;
  deltaDirection?: 'up' | 'down';
}

/** 단일 집계 타일. StatGrid 로 묶어 배치 요약에 사용. */
export function Stat({ value, label, accent = false, delta, deltaDirection = 'up' }: StatProps) {
  return (
    <div className="ds-stat">
      <div className={`ds-stat__value ${accent ? 'ds-stat__value--accent' : ''}`.trim()}>{value}</div>
      <div className="ds-stat__label">{label}</div>
      {delta && <div className={`ds-stat__delta ds-stat__delta--${deltaDirection}`}>{delta}</div>}
    </div>
  );
}

export interface StatGridProps extends React.HTMLAttributes<HTMLDivElement> {}

/** Stat 타일 자동 그리드. */
export function StatGrid({ className = '', ...props }: StatGridProps) {
  return <div className={`ds-stat-grid ${className}`.trim()} {...props} />;
}
