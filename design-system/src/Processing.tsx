import * as React from 'react';
import './theme.css';
import './components.css';
import { Character, type CharacterName } from './Character';
import { ProgressBar } from './ProgressBar';

export interface ProcessingProps {
  /** 대기 화면을 꾸밀 캐릭터 (기본 용희=투수, "던지는 중") */
  character?: CharacterName;
  title?: string;
  /** 보조 메시지 (… 애니메이션 자동) */
  message?: string;
  /** 0–100 진행률. 미지정 시 indeterminate 바 */
  percent?: number;
}

/** 배치/단건 처리 대기 화면. 캐릭터 + 진행 메시지 + 진행바. */
export function Processing({
  character = 'yonghee',
  title = '메타데이터 추출 중',
  message = '이미지를 읽고 있어요',
  percent,
}: ProcessingProps) {
  return (
    <div className="ds-processing" role="status" aria-live="polite">
      <span className="ds-processing__char">
        <Character name={character} size={96} bob />
      </span>
      <div>
        <div className="ds-processing__title">{title}</div>
        <div className="ds-processing__msg">
          <span className="ds-processing__dots">{message}</span>
        </div>
      </div>
      <div style={{ width: '100%', maxWidth: 320 }}>
        <ProgressBar value={percent} indeterminate={percent === undefined} />
      </div>
    </div>
  );
}
