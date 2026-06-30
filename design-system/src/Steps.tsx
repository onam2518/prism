import * as React from 'react';
import './theme.css';
import './components.css';

export type StepStatus = 'done' | 'active' | 'pending';

export interface Step {
  title: string;
  detail?: string;
  status?: StepStatus;
}

export interface StepsProps {
  steps: Step[];
}

function Check() {
  return (
    <svg className="ds-step__check" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <path d="M2.5 6.2 5 8.5 9.5 3.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * 에이전틱 단계 표시 (Perplexity Pro Search 5-step 패턴).
 * Prism 메타 파이프라인: 이미지 읽기 → OCR → 비전 → 메타 추출 → 품질 판정.
 */
export function Steps({ steps }: StepsProps) {
  return (
    <div className="ds-steps">
      {steps.map((s, i) => {
        const status = s.status ?? 'pending';
        return (
          <div key={i} className={`ds-step ds-step--${status}`}>
            <div className="ds-step__rail">
              <span className="ds-step__marker">{status === 'done' && <Check />}</span>
              <span className="ds-step__line" />
            </div>
            <div className="ds-step__body">
              <div className="ds-step__title">{s.title}</div>
              {s.detail && <div className="ds-step__detail">{s.detail}</div>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
