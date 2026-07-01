import * as React from 'react';
import './theme.css';
import './components.css';

export interface ToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
  disabled?: boolean;
  id?: string;
}

/** 설정 스위치. on = Primary 트랙, white thumb. role=switch. */
export function Toggle({ checked, onChange, label, disabled = false, id }: ToggleProps) {
  const btn = (
    <button
      type="button"
      role="switch"
      id={id}
      aria-checked={checked}
      disabled={disabled}
      className="ds-toggle__track"
      onClick={() => onChange(!checked)}
    >
      <span className="ds-toggle__thumb" />
    </button>
  );
  if (!label) return <span className={`ds-toggle ${disabled ? 'ds-toggle--disabled' : ''}`.trim()}>{btn}</span>;
  return (
    <label htmlFor={id} className={`ds-toggle ${disabled ? 'ds-toggle--disabled' : ''}`.trim()}>
      {btn}
      <span className="ds-toggle__label">{label}</span>
    </label>
  );
}
