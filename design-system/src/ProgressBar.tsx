import * as React from 'react';
import './theme.css';
import './components.css';

export type ProgressTone = 'primary' | 'success' | 'warning' | 'error';

export interface ProgressBarProps {
  /** 0–100. indeterminate 면 무시 */
  value?: number;
  /** 좌측 라벨 (DistributionBar 용도) */
  label?: string;
  /** 우측 퍼센트/값 표시 */
  showPercent?: boolean;
  tone?: ProgressTone;
  indeterminate?: boolean;
}

/** 진행률·분포 막대. label 을 주면 DistributionBar 로 쓰임. */
export function ProgressBar({
  value = 0,
  label,
  showPercent = false,
  tone = 'primary',
  indeterminate = false,
}: ProgressBarProps) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div className={`ds-progress ${indeterminate ? 'ds-progress--indeterminate' : ''}`.trim()}>
      {(label || showPercent) && (
        <div className="ds-progress__head">
          {label && <span className="ds-progress__label">{label}</span>}
          {showPercent && !indeterminate && <span className="ds-progress__pct">{pct}%</span>}
        </div>
      )}
      <div
        className="ds-progress__track"
        role="progressbar"
        aria-valuenow={indeterminate ? undefined : pct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={`ds-progress__fill ds-progress__fill--${tone}`}
          style={{ width: indeterminate ? undefined : `${pct}%` }}
        />
      </div>
    </div>
  );
}
