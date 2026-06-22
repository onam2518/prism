import * as React from 'react';
import './theme.css';
import './components.css';

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
}

/** Prism 텍스트 입력. 포커스 시 바이올렛 링. */
export function Input({ label, className = '', id, ...props }: InputProps) {
  const inputId = id || (label ? `ds-${label}` : undefined);
  return (
    <div>
      {label && <label className="ds-label" htmlFor={inputId}>{label}</label>}
      <input id={inputId} className={`ds-field ${className}`.trim()} {...props} />
    </div>
  );
}
