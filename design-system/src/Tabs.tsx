import * as React from 'react';
import './theme.css';
import './components.css';

export interface TabItem {
  id: string;
  label: string;
  icon?: React.ReactNode;
}

export interface TabsProps {
  items: TabItem[];
  value: string;
  onChange: (id: string) => void;
  /** underline = 상단 내비(가로, Primary 밑줄) · sidebar = 세로 내비(tint 하이라이트) */
  variant?: 'underline' | 'sidebar';
}

/** Prism 탭. 상단 내비는 Primary 밑줄, 사이드바는 tint 하이라이트로 활성 표시. */
export function Tabs({ items, value, onChange, variant = 'underline' }: TabsProps) {
  return (
    <div role="tablist" className={`ds-tabs ds-tabs--${variant}`}>
      {items.map((it) => (
        <button
          key={it.id}
          type="button"
          role="tab"
          aria-selected={value === it.id}
          className={`ds-tab ${value === it.id ? 'ds-tab--active' : ''}`.trim()}
          onClick={() => onChange(it.id)}
        >
          {it.icon}
          <span>{it.label}</span>
        </button>
      ))}
    </div>
  );
}
