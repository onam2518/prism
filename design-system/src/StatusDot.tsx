import * as React from 'react';
import './theme.css';
import './components.css';

export type StatusTone = 'ok' | 'warn' | 'error' | 'mock' | 'idle';

export interface StatusDotProps {
  tone?: StatusTone;
  /** ok 일 때 펄스 애니메이션 (실시간 연결 표시) */
  pulse?: boolean;
  children?: React.ReactNode;
}

/** 연결/상태 표시 dot + 라벨. 모델 연결·MOCK 등. */
export function StatusDot({ tone = 'idle', pulse = false, children }: StatusDotProps) {
  const toneClass = tone === 'idle' ? '' : `ds-statusdot--${tone}`;
  return (
    <span className={`ds-statusdot ${toneClass} ${pulse ? 'ds-statusdot--pulse' : ''}`.trim()}>
      <span className="ds-statusdot__dot" />
      {children}
    </span>
  );
}
