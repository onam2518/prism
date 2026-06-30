import * as React from 'react';
import './theme.css';
import './components.css';

export interface IconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** 접근성 라벨 + 호버 툴팁 텍스트 (필수) */
  label: string;
  /** plain = 투명 · bordered = 1px 보더 */
  variant?: 'plain' | 'bordered';
  active?: boolean;
  size?: 'sm' | 'md';
  tipPos?: 'top' | 'right' | 'bottom';
}

/** 아이콘 전용 버튼. 라벨은 숨기고 호버 시 툴팁으로 표시(군더더기 축소). */
export function IconButton({
  label,
  variant = 'plain',
  active = false,
  size = 'md',
  tipPos,
  className = '',
  children,
  type = 'button',
  ...props
}: IconButtonProps) {
  const cls = [
    'ds-iconbtn',
    variant === 'bordered' && 'ds-iconbtn--bordered',
    active && 'ds-iconbtn--active',
    size === 'sm' && 'ds-iconbtn--sm',
    className,
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <button type={type} aria-label={label} data-tip={label} data-tip-pos={tipPos} className={cls} {...props}>
      {children}
    </button>
  );
}

export interface TooltipProps {
  label: string;
  pos?: 'top' | 'right' | 'bottom';
  children: React.ReactNode;
}

/** 아무 요소에 호버 툴팁을 붙이는 래퍼. CSS [data-tip] 기반, JS 0. */
export function Tooltip({ label, pos, children }: TooltipProps) {
  return (
    <span data-tip={label} data-tip-pos={pos} style={{ display: 'inline-flex' }}>
      {children}
    </span>
  );
}
