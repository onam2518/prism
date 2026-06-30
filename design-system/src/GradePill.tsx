import * as React from 'react';
import './theme.css';
import './components.css';

export interface GradePillProps {
  /** g = 유통가능 · r = 차단 */
  grade: 'g' | 'r';
  /** 라벨 텍스트 (기본: 유통가능/차단) */
  children?: React.ReactNode;
}

/** 품질 등급 필 (G/R). 메타 결과의 유통 판정. */
export function GradePill({ grade, children }: GradePillProps) {
  const label = children ?? (grade === 'g' ? '유통가능' : '차단');
  return (
    <span className={`ds-grade ds-grade--${grade}`}>
      <span className="ds-grade__mark">{grade.toUpperCase()}</span>
      {label}
    </span>
  );
}
