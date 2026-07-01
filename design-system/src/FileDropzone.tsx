import * as React from 'react';
import './theme.css';
import './components.css';

export interface FileDropzoneProps {
  onFiles: (files: File[]) => void;
  accept?: string;
  multiple?: boolean;
  /** 메인 안내 문구 */
  label?: React.ReactNode;
  /** 보조 문구(지원 포맷 등) */
  hint?: React.ReactNode;
  icon?: React.ReactNode;
}

function UploadIcon() {
  return (
    <svg className="ds-dropzone__icon" width="28" height="28" viewBox="0 0 28 28" fill="none" aria-hidden="true">
      <path d="M14 18V6m0 0-4 4m4-4 4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M5 18v3a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

/** 범용 파일 드롭존 · 드래그·드롭·클릭·붙여넣기. 이미지/엑셀 입력 공통. */
export function FileDropzone({
  onFiles,
  accept,
  multiple = true,
  label = '파일을 끌어다 놓거나 클릭해 선택',
  hint,
  icon,
}: FileDropzoneProps) {
  const [drag, setDrag] = React.useState(false);
  const inputRef = React.useRef<HTMLInputElement>(null);

  const emit = (list: FileList | null) => {
    if (list && list.length) onFiles(Array.from(list));
  };

  return (
    <div
      className={`ds-dropzone ${drag ? 'ds-dropzone--drag' : ''}`.trim()}
      role="button"
      tabIndex={0}
      onClick={() => inputRef.current?.click()}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          inputRef.current?.click();
        }
      }}
      onDragOver={(e) => {
        e.preventDefault();
        setDrag(true);
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDrag(false);
        emit(e.dataTransfer.files);
      }}
      onPaste={(e) => emit(e.clipboardData.files)}
    >
      {icon ?? <UploadIcon />}
      <div>{label}</div>
      {hint && <div className="ds-dropzone__hint">{hint}</div>}
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        hidden
        onChange={(e) => emit(e.target.files)}
      />
    </div>
  );
}
