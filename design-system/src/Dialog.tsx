import * as React from 'react';
import './theme.css';
import './components.css';

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  children?: React.ReactNode;
  /** 하단 액션 영역 (보통 Button 들) */
  footer?: React.ReactNode;
  /** 백드롭 클릭으로 닫기 (기본 true) */
  closeOnBackdrop?: boolean;
}

/** Prism 중앙 모달. 따뜻한 페이퍼 표면 + 잉크 틴트 그림자, Esc/백드롭으로 닫힘. */
export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  closeOnBackdrop = true,
}: DialogProps) {
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const titleId = title ? 'ds-dialog-title' : undefined;
  return (
    <div
      className="ds-dialog-backdrop"
      onMouseDown={(e) => {
        if (closeOnBackdrop && e.target === e.currentTarget) onClose();
      }}
    >
      <div role="dialog" aria-modal="true" aria-labelledby={titleId} className="ds-dialog">
        {title && (
          <h2 id={titleId} className="ds-dialog__title">
            {title}
          </h2>
        )}
        <div className="ds-dialog__body">{children}</div>
        {footer && <div className="ds-dialog__footer">{footer}</div>}
      </div>
    </div>
  );
}
