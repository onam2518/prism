import * as React from 'react';
import './theme.css';
import './components.css';

export interface SegmentedItem {
  id: string;
  label: string;
}

export interface SegmentedProps {
  items: SegmentedItem[];
  value: string;
  onChange: (id: string) => void;
}

/** 세그먼티드 컨트롤(탭형 토글). 입력 모드 전환(이미지/텍스트/엑셀) 등. */
export function Segmented({ items, value, onChange }: SegmentedProps) {
  return (
    <div className="ds-segmented" role="tablist">
      {items.map((it) => (
        <button
          key={it.id}
          type="button"
          role="tab"
          aria-pressed={value === it.id}
          aria-selected={value === it.id}
          className="ds-segmented__item"
          onClick={() => onChange(it.id)}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}
