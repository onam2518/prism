import * as React from 'react';
import './theme.css';
import './components.css';
import { StatusDot, type StatusTone } from './StatusDot';

export interface KeyFieldProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label?: string;
  /** 연결 상태 (라벨 우측에 dot) */
  status?: StatusTone;
  statusLabel?: string;
}

/** API 키 입력 — 비밀 토글(eye) + 연결 상태 dot. */
export function KeyField({ label, status, statusLabel, className = '', id, ...props }: KeyFieldProps) {
  const [shown, setShown] = React.useState(false);
  const fieldId = id || (label ? `ds-key-${label}` : undefined);
  return (
    <div className="ds-keyfield">
      {(label || status) && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          {label && (
            <label className="ds-label" htmlFor={fieldId} style={{ margin: 0 }}>
              {label}
            </label>
          )}
          {status && <StatusDot tone={status}>{statusLabel}</StatusDot>}
        </div>
      )}
      <div className="ds-keyfield__row">
        <input
          id={fieldId}
          type={shown ? 'text' : 'password'}
          className={`ds-field ${className}`.trim()}
          {...props}
        />
        <button
          type="button"
          className="ds-keyfield__eye"
          aria-label={shown ? '키 숨기기' : '키 표시'}
          aria-pressed={shown}
          onClick={() => setShown((s) => !s)}
        >
          {shown ? <EyeOff /> : <Eye />}
        </button>
      </div>
    </div>
  );
}

function Eye() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M1.5 8s2.5-4.5 6.5-4.5S14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}
function EyeOff() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M6.2 6.2A2 2 0 0 0 8 10a2 2 0 0 0 1.8-1.1M3 3l10 10M5.2 5.3C2.9 6.4 1.5 8 1.5 8s2.5 4.5 6.5 4.5c1 0 1.9-.3 2.7-.7M9.5 3.7C9 3.6 8.5 3.5 8 3.5 4 3.5 1.5 8 1.5 8m13 0s-.9-1.6-2.5-2.9" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}
