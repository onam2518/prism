import * as React from 'react';
import './theme.css';
import './components.css';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** primary = 바이올렛 CTA · secondary = 보더 · ghost = 텍스트 */
  variant?: ButtonVariant;
}

/** Prism 기본 버튼. 인터랙션 색은 바이올렛(#5b52ff) 하나로 통일. */
export function Button({ variant = 'primary', className = '', ...props }: ButtonProps) {
  return <button className={`ds-btn ds-btn--${variant} ${className}`.trim()} {...props} />;
}
