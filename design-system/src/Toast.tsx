import * as React from 'react';
import './theme.css';
import './components.css';

export type ToastVariant = 'default' | 'success' | 'error';

export interface ToastProps {
  open: boolean;
  children: React.ReactNode;
  variant?: ToastVariant;
  /** 좌측 아이콘 */
  icon?: React.ReactNode;
  /** 자동 닫힘(ms). 0이면 수동. 기본 3000 */
  duration?: number;
  onClose?: () => void;
}

/** 일시적 확인(Copied·Shared). 의도적으로 다크 — 모드 무관 하단 중앙. */
export function Toast({ open, children, variant = 'default', icon, duration = 3000, onClose }: ToastProps) {
  React.useEffect(() => {
    if (!open || !duration || !onClose) return;
    const t = setTimeout(onClose, duration);
    return () => clearTimeout(t);
  }, [open, duration, onClose]);

  if (!open) return null;
  return (
    <div className="ds-toast-viewport">
      <div className={`ds-toast ds-toast--${variant}`} role="status" aria-live="polite">
        {icon && <span className="ds-toast__icon">{icon}</span>}
        <span>{children}</span>
      </div>
    </div>
  );
}
