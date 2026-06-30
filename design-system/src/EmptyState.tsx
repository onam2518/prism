import * as React from 'react';
import './theme.css';
import './components.css';

export interface EmptyStateProps {
  /** 아이콘 또는 캐릭터(Character) */
  icon?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  /** 제안 액션들(Button/pill) */
  actions?: React.ReactNode;
}

/** 범용 빈 상태. 사과하지 않고 다음 행동을 안내. */
export function EmptyState({ icon, title, description, actions }: EmptyStateProps) {
  return (
    <div className="ds-empty">
      {icon && <div className="ds-empty__icon">{icon}</div>}
      <div className="ds-empty__title">{title}</div>
      {description && <div className="ds-empty__desc">{description}</div>}
      {actions && <div className="ds-empty__actions">{actions}</div>}
    </div>
  );
}
