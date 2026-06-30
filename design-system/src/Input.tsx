import * as React from 'react';
import './theme.css';
import './components.css';

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  /** field = 표준 텍스트필드 · composer = 큰 Ask 박스 */
  variant?: 'field' | 'composer';
  /** 에러 상태 — 보더/링이 error 색으로 */
  invalid?: boolean;
  /** 도움말·에러 메시지 */
  hint?: string;
}

/** Prism 텍스트 입력. 포커스 시 teal 링. composer 는 시그니처 Ask 웰. */
export function Input({
  label,
  variant = 'field',
  invalid = false,
  hint,
  className = '',
  id,
  ...props
}: InputProps) {
  const inputId = id || (label ? `ds-${label}` : undefined);
  const cls = [
    'ds-field',
    variant === 'composer' && 'ds-field--composer',
    invalid && 'ds-field--invalid',
    className,
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <div>
      {label && (
        <label className="ds-label" htmlFor={inputId}>
          {label}
        </label>
      )}
      <input id={inputId} className={cls} aria-invalid={invalid || undefined} {...props} />
      {hint && <p className={`ds-hint ${invalid ? 'ds-hint--error' : ''}`.trim()}>{hint}</p>}
    </div>
  );
}
