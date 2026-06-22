import * as React from 'react';
import './theme.css';
import './components.css';

export type BadgeVariant = 'neutral' | 'violet' | 'solar' | 'success' | 'danger';

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  /** 앞에 상태 점(dot) 표시 */
  dot?: boolean;
}

/** Prism 배지/상태칩. solar 는 단일 액센트라 절제해서 사용. */
export function Badge({ variant = 'neutral', dot = false, className = '', children, ...props }: BadgeProps) {
  return (
    <span className={`ds-badge ds-badge--${variant} ${className}`.trim()} {...props}>
      {dot && <span className="ds-badge__dot" />}
      {children}
    </span>
  );
}
