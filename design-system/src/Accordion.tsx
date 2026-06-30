import * as React from 'react';
import './theme.css';
import './components.css';

export interface AccordionItemData {
  id: string;
  title: React.ReactNode;
  content: React.ReactNode;
}

export interface AccordionProps {
  items: AccordionItemData[];
  /** 기본 열림 id들 */
  defaultOpen?: string[];
  /** 한 번에 하나만 열기 */
  single?: boolean;
}

function Chevron() {
  return (
    <svg className="ds-accordion__chevron" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** 접힘/펼침 목록. 설정·상세·FAQ 등. */
export function Accordion({ items, defaultOpen = [], single = false }: AccordionProps) {
  const [open, setOpen] = React.useState<string[]>(defaultOpen);
  const toggle = (id: string) =>
    setOpen((cur) => {
      if (cur.includes(id)) return cur.filter((x) => x !== id);
      return single ? [id] : [...cur, id];
    });
  return (
    <div className="ds-accordion">
      {items.map((it) => {
        const isOpen = open.includes(it.id);
        return (
          <div key={it.id} className={`ds-accordion__item ${isOpen ? 'ds-accordion__item--open' : ''}`.trim()}>
            <button type="button" className="ds-accordion__trigger" aria-expanded={isOpen} onClick={() => toggle(it.id)}>
              <span>{it.title}</span>
              <Chevron />
            </button>
            {isOpen && <div className="ds-accordion__panel">{it.content}</div>}
          </div>
        );
      })}
    </div>
  );
}
