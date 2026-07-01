import * as React from 'react';
import './theme.css';
import './components.css';

export interface FieldRowProps {
  label: React.ReactNode;
  children: React.ReactNode;
  /** value 가 칩 배열일 때 래핑 스타일 적용 */
  chips?: boolean;
}

/** key/value 한 행. label 120px + value. */
export function FieldRow({ label, children, chips = false }: FieldRowProps) {
  return (
    <div className="ds-fieldrow">
      <div className="ds-fieldrow__label">{label}</div>
      <div className={`ds-fieldrow__value ${chips ? 'ds-fieldrow__chips' : ''}`.trim()}>{children}</div>
    </div>
  );
}

export interface DefinitionListProps {
  children: React.ReactNode;
}

/** 메타 상세 패널 · FieldRow 들의 컨테이너. */
export function DefinitionList({ children }: DefinitionListProps) {
  return <div className="ds-deflist">{children}</div>;
}
