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
  /** 세로(사이드바) / 가로 */
  orientation?: 'vertical' | 'horizontal';
}

/** Prism 탭/사이드바 내비. 활성 항목은 surface 하이라이트. */
export function Tabs({ items, value, onChange, orientation = 'vertical' }: TabsProps) {
  return (
    <div
      role="tablist"
      className="ds-tabs"
      style={{ flexDirection: orientation === 'vertical' ? 'column' : 'row' }}
    >
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
