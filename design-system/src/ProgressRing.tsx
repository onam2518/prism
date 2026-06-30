import * as React from 'react';
import './theme.css';
import './components.css';

export interface ProgressRingProps {
  /** 0–100 */
  value: number;
  size?: number;
  /** 중앙 라벨. 미지정 시 "NN%" */
  label?: string;
}

/** 도넛 진행률 (conic-gradient). 품질 분포(G 비율 등) 시각화. */
export function ProgressRing({ value, size = 96, label }: ProgressRingProps) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div
      className="ds-ring"
      style={{ ['--ds-ring-size' as string]: `${size}px`, ['--ds-ring-pct' as string]: pct }}
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <span className="ds-ring__label">{label ?? `${pct}%`}</span>
    </div>
  );
}
