import * as React from 'react';
import './theme.css';
import './components.css';
import { SourceRail, type Source } from './SourceRail';
import { Button } from './Button';

export interface AnswerBlockProps {
  /** 상단 소스 레일 (없으면 생략) */
  sources?: Source[];
  onSelectSource?: (source: Source, index: number) => void;
  /** 답변 본문 — <Citation/> 을 흐름 속에 포함 */
  children: React.ReactNode;
  /** 카드 상단 라벨 (기본 "Answer") */
  eyebrow?: string;
  /** 관련 질문 — Pill 로 표시 */
  related?: string[];
  onRelated?: (question: string) => void;
}

/**
 * 읽기 순서 = 배치 순서. Source Rail → Answer Card(ds-answer, 68ch) → Related.
 * Card(answer) 와 SourceRail 을 조합한 Composite.
 */
export function AnswerBlock({
  sources,
  onSelectSource,
  children,
  eyebrow = 'Answer',
  related = [],
  onRelated,
}: AnswerBlockProps) {
  return (
    <div className="ds-answer-block">
      {sources && sources.length > 0 && <SourceRail sources={sources} onSelect={onSelectSource} />}

      <div className="ds-card ds-card--answer">
        {eyebrow && <p className="ds-answer-block__eyebrow">{eyebrow}</p>}
        <div className="ds-answer">{children}</div>
      </div>

      {related.length > 0 && (
        <div className="ds-answer-block__related">
          <span className="ds-answer-block__related-label">Related</span>
          {related.map((q) => (
            <Button key={q} variant="pill" onClick={() => onRelated?.(q)}>
              {q}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}
