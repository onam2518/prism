import * as React from 'react';
import './theme.css';
import './components.css';

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {}

/** Prism 카드. 그림자 없이 surface + 헤어라인으로 깊이 표현. */
export function Card({ className = '', ...props }: CardProps) {
  return <div className={`ds-card ${className}`.trim()} {...props} />;
}
