import * as React from 'react';
import './theme.css';
import './components.css';

export interface SkeletonProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: 'text' | 'title' | 'block' | 'avatar';
  width?: number | string;
  height?: number | string;
}

/** 로딩 스켈레톤. 최종 치수로 두고 shimmer. */
export function Skeleton({ variant = 'text', width, height, className = '', style, ...props }: SkeletonProps) {
  return (
    <div
      className={`ds-skeleton ds-skeleton--${variant} ${className}`.trim()}
      style={{ width, height, ...style }}
      aria-hidden="true"
      {...props}
    />
  );
}
