import * as React from 'react';
import './theme.css';
import './components.css';

export type BadgeVariant =
  | 'neutral'
  | 'pro'
  | 'status'
  | 'citation'
  | 'success'
  | 'error'
  | 'warning'
  // 도메인 메타 칩 (이미지→메타 파이프라인)
  | 'entity'
  | 'intent'
  | 'category'
  | 'reason';

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** pro = 모델/PRO 라벨 · status = New/Beta · citation = 인용 칩 · 그 외 의미색 */
  variant?: BadgeVariant;
  /** 앞에 상태 점(dot) 표시 */
  dot?: boolean;
}

/** Prism 배지/칩. teal 계열은 액션·인용 신호라 절제해서 사용. */
export function Badge({ variant = 'neutral', dot = false, className = '', children, ...props }: BadgeProps) {
  return (
    <span className={`ds-badge ds-badge--${variant} ${className}`.trim()} {...props}>
      {dot && <span className="ds-badge__dot" />}
      {children}
    </span>
  );
}
