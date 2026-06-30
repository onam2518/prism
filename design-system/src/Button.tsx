import * as React from 'react';
import './theme.css';
import './components.css';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'pill';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** primary = teal CTA · secondary = 보더 · ghost = 조용한 툴바 · pill = 포커스모드/필터 */
  variant?: ButtonVariant;
  /** pill 의 활성(선택) 상태 — aria-pressed 로 표현 */
  active?: boolean;
  /** Button-in-Button: 트레일링 아이콘을 중첩 원에 (자석 hover). 화살표 CTA 등. */
  trailingIcon?: React.ReactNode;
}

/** Prism 버튼. 인터랙션 색은 Peacock teal(#20808d) 하나로 통일. */
export function Button({
  variant = 'primary',
  active,
  trailingIcon,
  className = '',
  type = 'button',
  children,
  ...props
}: ButtonProps) {
  const pressed = variant === 'pill' ? active ?? false : undefined;
  const cls = ['ds-btn', `ds-btn--${variant}`, trailingIcon && 'ds-btn--cta', className]
    .filter(Boolean)
    .join(' ');
  return (
    <button type={type} aria-pressed={pressed} className={cls} {...props}>
      {children}
      {trailingIcon && <span className="ds-btn__trail">{trailingIcon}</span>}
    </button>
  );
}
