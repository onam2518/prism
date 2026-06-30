import * as React from 'react';
import './theme.css';
import './components.css';

export interface Source {
  domain: string;
  title: string;
  /** favicon 이미지 URL (없으면 placeholder 블록) */
  favicon?: string;
  /** 클릭 시 이동할 소스 URL */
  url?: string;
}

export interface SourceRailProps {
  sources: Source[];
  /** url 없는 소스 클릭 핸들러 */
  onSelect?: (source: Source, index: number) => void;
}

/** 인용 소스의 가로 스크롤 레일. Card(source) 의 반복 — 본문을 가리지 않음. */
export function SourceRail({ sources, onSelect }: SourceRailProps) {
  return (
    <div className="ds-rail">
      {sources.map((s, i) => {
        const inner = (
          <span className="ds-source">
            {s.favicon ? (
              <img className="ds-source__fav" src={s.favicon} alt="" />
            ) : (
              <span className="ds-source__fav" aria-hidden="true" />
            )}
            <span>
              <span className="ds-source__domain">{s.domain}</span>
              <span className="ds-source__title">{s.title}</span>
            </span>
          </span>
        );
        return s.url ? (
          <a key={i} className="ds-card ds-card--source" href={s.url} target="_blank" rel="noreferrer">
            {inner}
          </a>
        ) : (
          <button
            key={i}
            type="button"
            className="ds-card ds-card--source"
            style={{ textAlign: 'left', cursor: 'pointer' }}
            onClick={() => onSelect?.(s, i)}
          >
            {inner}
          </button>
        );
      })}
    </div>
  );
}
