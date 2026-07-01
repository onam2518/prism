import * as React from 'react';
import './theme.css';
import './components.css';

export interface CitationProps {
  /** 인용 번호 [1] */
  index: number;
  /** 소스 링크 (있으면 a, 없으면 button) */
  href?: string;
  onClick?: () => void;
}

/** 답변 본문 흐름 속 인라인 인용 토큰. Primary superscript. */
export function Citation({ index, href, onClick }: CitationProps) {
  if (href) {
    return (
      <a className="ds-citation" href={href} aria-label={`소스 ${index}`}>
        {index}
      </a>
    );
  }
  return (
    <button type="button" className="ds-citation" onClick={onClick} aria-label={`소스 ${index}`}>
      {index}
    </button>
  );
}
