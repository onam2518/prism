import * as React from 'react';
import './theme.css';
import './components.css';

export interface AttachmentChipProps {
  name: string;
  /** 파일 크기 라벨 (예: "1.2MB") */
  size?: string;
  onRemove?: () => void;
  /** 아이콘 교체 (기본: 파일 아이콘) */
  icon?: React.ReactNode;
}

function FileIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <path
        d="M3.5 1.5h4l3 3v8a.5.5 0 0 1-.5.5H3.5a.5.5 0 0 1-.5-.5v-10a.5.5 0 0 1 .5-.5Z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
      <path d="M7.5 1.5v3h3" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}

/** 업로드된 파일/이미지 칩. 첨부 목록·컴포저에 사용. */
export function AttachmentChip({ name, size, onRemove, icon }: AttachmentChipProps) {
  return (
    <span className="ds-attachment">
      <span className="ds-attachment__icon">{icon ?? <FileIcon />}</span>
      <span className="ds-attachment__name">{name}</span>
      {size && <span className="ds-attachment__size">{size}</span>}
      {onRemove && (
        <button type="button" className="ds-attachment__remove" onClick={onRemove} aria-label={`${name} 제거`}>
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
            <path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
        </button>
      )}
    </span>
  );
}
