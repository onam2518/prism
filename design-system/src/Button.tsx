import * as React from 'react';
import './theme.css';
import './components.css';

// Anchor Button 계약 (design-system/anchor/Button.md)
//   Variant(Solid·Outline) × Color × Size(Sm~3Xl) × Shape(Square=R8·Rounded=R100) × State
//   원칙: 한 화면에 Solid/Primary 하나 · 보조는 무게↓ · 파괴적 액션은 Danger.
export type ButtonVariant = 'solid' | 'outline';
export type ButtonColor =
  | 'primary' | 'secondary' | 'subtlest' | 'neutral' | 'inverse' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg' | 'xl' | '2xl' | '3xl';
export type ButtonShape = 'square' | 'rounded';

// 구 API 호환: variant='primary'|'secondary'|'ghost'|'pill' → 신 (variant,color,shape)
type LegacyVariant = 'primary' | 'secondary' | 'ghost' | 'pill';
const LEGACY: Record<LegacyVariant, { variant: ButtonVariant; color: ButtonColor; shape?: ButtonShape }> = {
  primary: { variant: 'solid', color: 'primary' },
  secondary: { variant: 'outline', color: 'neutral' },
  ghost: { variant: 'outline', color: 'ghost' },
  pill: { variant: 'solid', color: 'secondary', shape: 'rounded' },
};

export interface ButtonProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'color'> {
  /** Solid = fill(가장 강함) · Outline = 테두리(한 단계 낮음). 구 값(primary/secondary/ghost/pill)도 허용. */
  variant?: ButtonVariant | LegacyVariant;
  /** 중요도/위계 색. 기본 primary. (Button.md §3-1 매트릭스) */
  color?: ButtonColor;
  /** Sm~3Xl. 기본 lg. 터치 우선 맥락은 2xl·3xl. */
  size?: ButtonSize;
  /** Square=R8(기본) · Rounded=R100 */
  shape?: ButtonShape;
  /** 라벨·아이콘 숨기고 스피너 + 폭 고정(레이아웃 시프트 방지) */
  loading?: boolean;
  /** 토글(필터·세그먼트) 활성 — aria-pressed. 구 pill API 호환. */
  active?: boolean;
  /** 앞 아이콘 슬롯 (16px) */
  leadingIcon?: React.ReactNode;
  /** 뒤 아이콘 슬롯 (16px) — Outline 에서만 허용(Solid 금지) */
  trailingIcon?: React.ReactNode;
}

export function Button({
  variant = 'solid',
  color,
  size = 'lg',
  shape = 'square',
  loading = false,
  active,
  leadingIcon,
  trailingIcon,
  className = '',
  type = 'button',
  disabled,
  children,
  ...props
}: ButtonProps) {
  // 구 API 정규화
  let v: ButtonVariant;
  let c: ButtonColor;
  let sh: ButtonShape = shape;
  if (variant in LEGACY) {
    const m = LEGACY[variant as LegacyVariant];
    v = m.variant;
    c = color ?? m.color;
    if (m.shape) sh = m.shape;
  } else {
    v = variant as ButtonVariant;
    c = color ?? 'primary';
  }

  const pressed = active === undefined ? undefined : active;
  const cls = [
    'ds-btn',
    `ds-btn--${v}`,
    `ds-btn--c-${c}`,
    `ds-btn--s-${size}`,
    sh === 'rounded' && 'ds-btn--rounded',
    loading && 'ds-btn--loading',
    className,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <button
      type={type}
      aria-pressed={pressed}
      aria-busy={loading || undefined}
      disabled={disabled || loading}
      className={cls}
      {...props}
    >
      {/* HoverLayer — 전 variant 공통 오버레이(Hover 시 background.state.hover) */}
      <span className="ds-btn__hover" aria-hidden="true" />
      {loading && <span className="ds-btn__spinner" aria-hidden="true" />}
      {leadingIcon && <span className="ds-btn__icon ds-btn__icon--lead">{leadingIcon}</span>}
      {children != null && <span className="ds-btn__label">{children}</span>}
      {trailingIcon && <span className="ds-btn__icon ds-btn__icon--trail">{trailingIcon}</span>}
    </button>
  );
}
