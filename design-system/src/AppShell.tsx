import * as React from 'react';
import './theme.css';
import './components.css';

export interface AppShellProps extends React.HTMLAttributes<HTMLDivElement> {
  sidebar: React.ReactNode;
  /** 우측 보조 레일 (소스/관련 등) */
  aside?: React.ReactNode;
}

/** 3-zone 앱 셸: 좌측 레일 · 본문 · (선택) 우측 레일. */
export function AppShell({ sidebar, aside, children, className = '', ...props }: AppShellProps) {
  return (
    <div className={`ds-shell ${aside ? 'ds-shell--right' : ''} ${className}`.trim()} {...props}>
      {sidebar}
      <main>{children}</main>
      {aside}
    </div>
  );
}

export interface PaneProps extends React.HTMLAttributes<HTMLDivElement> {
  title?: React.ReactNode;
  /** 타이틀 옆 보조 텍스트 */
  meta?: React.ReactNode;
}

/** 콘텐츠 패널. 선택적 PaneTitle 포함. */
export function Pane({ title, meta, children, className = '', ...props }: PaneProps) {
  return (
    <section className={`ds-pane ${className}`.trim()} {...props}>
      {title && (
        <h2 className="ds-pane__title">
          {title}
          {meta && <small>{meta}</small>}
        </h2>
      )}
      {children}
    </section>
  );
}

/** 독립 사용용 PaneTitle (Pane 밖에서 같은 스타일이 필요할 때). */
export function PaneTitle({ children, meta }: { children: React.ReactNode; meta?: React.ReactNode }) {
  return (
    <h2 className="ds-pane__title">
      {children}
      {meta && <small>{meta}</small>}
    </h2>
  );
}
