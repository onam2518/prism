import * as React from 'react';
import './theme.css';
import './components.css';

export interface SidebarProps extends React.HTMLAttributes<HTMLElement> {}

/** 좌측 내비게이션 레일. NavGroup/NavItem 을 담는다. */
export function Sidebar({ className = '', children, ...props }: SidebarProps) {
  return (
    <aside className={`ds-sidebar ${className}`.trim()} {...props}>
      {children}
    </aside>
  );
}

export interface NavGroupProps {
  label?: string;
  children: React.ReactNode;
}

/** 내비 그룹(라벨 + 항목들). */
export function NavGroup({ label, children }: NavGroupProps) {
  return (
    <nav className="ds-navgroup">
      {label && <div className="ds-navgroup__label">{label}</div>}
      {children}
    </nav>
  );
}

export interface NavItemProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  icon?: React.ReactNode;
  active?: boolean;
  /** 우측 배지/상태 (StatusDot, Badge 등) */
  trailing?: React.ReactNode;
}

/** 내비 항목. active = tint 하이라이트. */
export function NavItem({ icon, active = false, trailing, children, className = '', ...props }: NavItemProps) {
  return (
    <button
      type="button"
      aria-current={active ? 'page' : undefined}
      className={`ds-navitem ${active ? 'ds-navitem--active' : ''} ${className}`.trim()}
      {...props}
    >
      {icon && <span className="ds-navitem__icon">{icon}</span>}
      <span>{children}</span>
      {trailing && <span className="ds-navitem__badge">{trailing}</span>}
    </button>
  );
}
