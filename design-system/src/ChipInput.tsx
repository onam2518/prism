import * as React from 'react';
import './theme.css';
import './components.css';

export interface ChipInputProps {
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
}

/** 칩 입력 — Enter 로 추가, ×로 제거. 사전(엔티티·인텐트) 편집용. */
export function ChipInput({ value, onChange, placeholder = '입력 후 Enter' }: ChipInputProps) {
  const [draft, setDraft] = React.useState('');

  const add = () => {
    const v = draft.trim();
    if (v && !value.includes(v)) onChange([...value, v]);
    setDraft('');
  };
  const remove = (v: string) => onChange(value.filter((x) => x !== v));

  return (
    <div className="ds-chipinput">
      {value.map((v) => (
        <span key={v} className="ds-chipinput__chip">
          {v}
          <button type="button" className="ds-chipinput__remove" aria-label={`${v} 제거`} onClick={() => remove(v)}>
            <svg width="11" height="11" viewBox="0 0 11 11" fill="none" aria-hidden="true">
              <path d="M3 3l5 5M8 3l-5 5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
            </svg>
          </button>
        </span>
      ))}
      <input
        className="ds-chipinput__input"
        value={draft}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            add();
          }
          if (e.key === 'Backspace' && !draft && value.length) {
            remove(value[value.length - 1]);
          }
        }}
        onBlur={add}
      />
    </div>
  );
}
