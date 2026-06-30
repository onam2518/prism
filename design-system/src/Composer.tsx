import * as React from 'react';
import './theme.css';
import './components.css';
import { Button } from './Button';

export interface ComposerMode {
  id: string;
  label: string;
}

export interface ComposerProps {
  value?: string;
  onChange?: (value: string) => void;
  onSubmit?: (value: string) => void;
  placeholder?: string;
  /** 포커스모드/필터 pill (Web·Academic·Writing 등) */
  modes?: ComposerMode[];
  activeMode?: string;
  onModeChange?: (id: string) => void;
  /** 우측 추가 액션(첨부·음성 등 아이콘 버튼) */
  actions?: React.ReactNode;
  submitLabel?: string;
  disabled?: boolean;
}

/**
 * 시그니처 Ask 웰. Input(composer) + Pill row + Action bar + Submit 의 조합.
 * Enter 제출, Shift+Enter 줄바꿈. value/onChange 미지정 시 내부 상태로 동작.
 */
export function Composer({
  value,
  onChange,
  onSubmit,
  placeholder = 'Ask anything…',
  modes = [],
  activeMode,
  onModeChange,
  actions,
  submitLabel = 'Ask',
  disabled = false,
}: ComposerProps) {
  const [internal, setInternal] = React.useState('');
  const isControlled = value !== undefined;
  const text = isControlled ? value : internal;

  const setText = (v: string) => {
    if (!isControlled) setInternal(v);
    onChange?.(v);
  };
  const submit = () => {
    const t = text.trim();
    if (!t || disabled) return;
    onSubmit?.(t);
  };

  return (
    <div className="ds-composer">
      <textarea
        className="ds-composer__input"
        rows={1}
        placeholder={placeholder}
        value={text}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <div className="ds-composer__toolbar">
        <div className="ds-composer__pills">
          {modes.map((m) => (
            <Button
              key={m.id}
              variant="pill"
              active={activeMode === m.id}
              onClick={() => onModeChange?.(m.id)}
            >
              {m.label}
            </Button>
          ))}
        </div>
        <div className="ds-composer__actions">
          {actions}
          <Button variant="primary" onClick={submit} disabled={disabled || !text.trim()}>
            {submitLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
