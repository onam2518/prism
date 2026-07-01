import * as React from 'react';
import './theme.css';
import './components.css';
import { Button } from './Button';

export interface CoachmarkProps {
  step: number;
  total: number;
  title: React.ReactNode;
  description?: React.ReactNode;
  onNext?: () => void;
  onBack?: () => void;
  onSkip?: () => void;
  nextLabel?: string;
  /** 화면상 위치(고정). 미지정 시 중앙은 호출측에서 style로 */
  style?: React.CSSProperties;
}

/**
 * 온보딩 코치 카드 · 스텝 표시 + 제목/설명 + 다음/건너뛰기.
 * 스포트라이트(대상 강조)는 대상에 `ds-coach-target` 클래스를 토글해 구현(호출측).
 */
export function Coachmark({
  step,
  total,
  title,
  description,
  onNext,
  onBack,
  onSkip,
  nextLabel,
  style,
}: CoachmarkProps) {
  const last = step >= total - 1;
  return (
    <div className="ds-coach" role="dialog" aria-modal="true" aria-label="온보딩" style={style}>
      <div className="ds-coach__step">
        STEP {step + 1} / {total}
      </div>
      <div className="ds-coach__title">{title}</div>
      {description && <div className="ds-coach__desc">{description}</div>}
      <div className="ds-coach__foot">
        <div className="ds-coach__dots">
          {Array.from({ length: total }).map((_, i) => (
            <span key={i} className={`ds-coach__dot ${i === step ? 'ds-coach__dot--on' : ''}`.trim()} />
          ))}
        </div>
        <div className="ds-coach__btns">
          {onSkip && !last && (
            <Button variant="ghost" onClick={onSkip}>
              건너뛰기
            </Button>
          )}
          {onBack && step > 0 && (
            <Button variant="secondary" onClick={onBack}>
              이전
            </Button>
          )}
          <Button variant="primary" onClick={onNext}>
            {nextLabel ?? (last ? '시작하기' : '다음')}
          </Button>
        </div>
      </div>
    </div>
  );
}
