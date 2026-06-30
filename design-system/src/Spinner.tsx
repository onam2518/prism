import * as React from 'react';
import './theme.css';
import './components.css';

export interface SpinnerProps {
  size?: number;
  /** 접근성 라벨 */
  label?: string;
}

/** 인디터미닛 스피너. 버튼/인라인 로딩. */
export function Spinner({ size = 20, label = '로딩 중' }: SpinnerProps) {
  return (
    <span
      className="ds-spinner"
      style={{ ['--ds-spinner-size' as string]: `${size}px` }}
      role="status"
      aria-label={label}
    />
  );
}
