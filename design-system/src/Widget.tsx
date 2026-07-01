import * as React from 'react';
import './theme.css';
import './components.css';

/** 4-col 셀 기준 사이즈. sm 1×1 · md 2×1 · lg 2×2 · tall 1×2 · wide 4×1 · xl 4×2 */
export type WidgetSize = 'sm' | 'md' | 'lg' | 'tall' | 'wide' | 'xl';

/** 사이즈 → W×H 라벨 (편집 모드 배지). */
export const widgetSizeLabel: Record<WidgetSize, string> = {
  sm: '1×1',
  md: '2×1',
  lg: '2×2',
  tall: '1×2',
  wide: '4×1',
  xl: '4×2',
};

const SEL_HANDLES = (
  <div className="ds-widget__sel" aria-hidden="true">
    <i /><i /><i /><i /><i /><i /><i /><i />
  </div>
);

/** function = 기능 위젯(실행·메뉴) · info = 정보 위젯(정보 패널) */
export type WidgetKind = 'function' | 'info';

export interface WidgetProps {
  title?: React.ReactNode;
  /** 타이틀 좌측 아이콘 */
  icon?: React.ReactNode;
  size?: WidgetSize;
  /** 위젯 종류 · function(기능) / info(정보). 기본 info */
  kind?: WidgetKind;
  /** 헤더에 종류 칩 표시 */
  showKind?: boolean;
  /** 헤더 우측 액션(IconButton 등) */
  actions?: React.ReactNode;
  /** 지정 시 편집 모드에서 삭제 버튼 노출 */
  onRemove?: () => void;
  /** 선택 상태 · Primary 링 + 8핸들 표시(배치 편집) */
  selected?: boolean;
  children?: React.ReactNode;
  className?: string;
}

const REMOVE = (
  <svg width="11" height="11" viewBox="0 0 11 11" fill="none" aria-hidden="true">
    <path d="M3 3l5 5M8 3l-5 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
  </svg>
);

/**
 * 위젯 · 자족 모듈(폰 홈 위젯처럼). 헤더(제목·아이콘·종류·액션) + 바디.
 * kind 로 기능 위젯/정보 위젯 구분, size 로 그리드 span 결정.
 */
export function Widget({
  title,
  icon,
  size = 'sm',
  kind = 'info',
  showKind = false,
  actions,
  onRemove,
  selected = false,
  children,
  className = '',
}: WidgetProps) {
  const cls = [
    'ds-widget',
    size !== 'sm' && `ds-widget--${size}`,
    `ds-widget--${kind}`,
    selected && 'ds-widget--selected',
    className,
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <div className={cls} data-size={widgetSizeLabel[size]}>
      {selected && SEL_HANDLES}
      {onRemove && (
        <button type="button" className="ds-widget__remove" aria-label="위젯 제거" onClick={onRemove}>
          {REMOVE}
        </button>
      )}
      {(title || actions || showKind) && (
        <div className="ds-widget__head">
          <div className="ds-widget__title">
            {icon && <span className="ds-widget__title-icon">{icon}</span>}
            <span>{title}</span>
          </div>
          <div className="ds-widget__actions">
            {actions}
            {showKind && (
              <span className={`ds-widget__kind ds-widget__kind--${kind === 'function' ? 'fn' : 'info'}`}>
                {kind === 'function' ? '기능' : '정보'}
              </span>
            )}
          </div>
        </div>
      )}
      <div className="ds-widget__body">{children}</div>
    </div>
  );
}

export interface LauncherWidgetProps {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  icon?: React.ReactNode;
  onClick?: () => void;
  onRemove?: () => void;
  size?: WidgetSize;
  className?: string;
}

/** 메뉴형 기능 위젯 · 클릭하면 기능 실행/이동하는 런처 타일. */
export function LauncherWidget({ title, subtitle, icon, onClick, onRemove, size = 'sm', className = '' }: LauncherWidgetProps) {
  const cls = ['ds-widget', 'ds-widget--function', 'ds-widget--launcher', size !== 'sm' && `ds-widget--${size}`, className]
    .filter(Boolean)
    .join(' ');
  return (
    <div
      className={cls}
      data-size={widgetSizeLabel[size]}
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onClick?.();
        }
      }}
    >
      {onRemove && (
        <button
          type="button"
          className="ds-widget__remove"
          aria-label="위젯 제거"
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
        >
          {REMOVE}
        </button>
      )}
      {icon && <span className="ds-launcher__icon">{icon}</span>}
      <span className="ds-launcher__body">
        <span className="ds-launcher__title">{title}</span>
        {subtitle && <span className="ds-launcher__sub">{subtitle}</span>}
      </span>
      <span className="ds-launcher__chev" aria-hidden="true">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <path d="M6 4l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
    </div>
  );
}

export interface WidgetGridProps extends React.HTMLAttributes<HTMLDivElement> {
  /** 편집 모드 · 위젯 지글 + 삭제 버튼 노출 */
  editing?: boolean;
  /** 그리드 컬럼 수 (기본 4) */
  columns?: number;
}

/** 위젯 캔버스 · bento 그리드. 편집 모드로 재배치/삭제. */
export function WidgetGrid({ editing = false, columns, className = '', style, children, ...props }: WidgetGridProps) {
  return (
    <div
      className={`ds-widgetgrid ${editing ? 'ds-widgetgrid--edit' : ''} ${className}`.trim()}
      style={columns ? { ['--ds-wg-cols' as string]: String(columns), ...style } : style}
      {...props}
    >
      {children}
    </div>
  );
}
