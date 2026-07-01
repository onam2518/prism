import * as React from 'react';
import './theme.css';
import './components.css';

export type CardVariant = 'answer' | 'source' | 'feed';

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  /** answer = 에디토리얼 플랫(기본) · source = 흰 카드 + hover Primary lift · feed = Discover 카드 */
  variant?: CardVariant;
}

/** Prism 카드. 깊이는 그림자보다 따뜻한 보더 + surface 온도차로. */
export function Card({ variant = 'answer', className = '', ...props }: CardProps) {
  return <div className={`ds-card ds-card--${variant} ${className}`.trim()} {...props} />;
}
