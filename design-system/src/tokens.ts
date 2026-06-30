// Prism Design System — 타입드 토큰. tokens.json / theme.css 와 1:1.
// Perplexity 정렬: 따뜻한 페이퍼 + Peacock teal · 라이트 우선 · 페이퍼-플랫 깊이.
export const tokens = {
  color: {
    primary: '#20808d',
    primaryHover: '#1a6873',
    primaryDeep: '#13343b',
    primaryTint: '#e5f2f2',
    tealOnDark: '#34b4c4',

    ink: '#091717',
    canvas: '#fbfaf4',
    surface: '#fcfcf9',
    surfaceWhite: '#ffffff',
    body: '#2e3a3a',
    muted: '#5c6a6a',
    placeholder: '#8a9494',
    hairline: '#e4e4dc',
    hairlineSoft: '#efefe9',

    darkCanvas: '#0d1117',
    darkSurface: '#161b22',
    darkSurfaceRaised: '#1c2128',
    darkLine: '#2a2f37',
    inkInverse: '#f2f2ed',
    inkInverseMuted: '#9ba1a6',

    success: '#1f9d6b',
    error: '#e0524a',
    warning: '#d9923a',
    onPrimary: '#ffffff',
  },
  font: {
    sans: "'FK Grotesk', 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    display: "'FK Display', 'FK Grotesk', 'Inter', sans-serif",
    body: "'FK Grotesk Neue', 'FK Grotesk', 'Inter', sans-serif",
    mono: "'Berkeley Mono', 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
  },
  fontSize: {
    caption: '13px',
    label: '14px',
    body: '15px',
    answer: '16px',
    subtitle: '18px',
    heading: '22px',
    headingLg: '28px',
    display: '36px',
    displayHero: '48px',
  },
  lineHeight: { tight: 1.17, snug: 1.3, normal: 1.5, relaxed: 1.63 },
  fontWeight: { regular: 400, medium: 500, semibold: 600 },
  radius: { sm: '6px', md: '10px', lg: '12px', xl: '16px', full: '9999px' },
  space: { 1: '4px', 2: '8px', 3: '12px', 4: '16px', 5: '20px', 6: '24px', 8: '32px', 12: '48px', 16: '64px' },
  shadow: {
    ambient: '0 1px 3px rgba(9,23,23,0.06)',
    subtle: '0 1px 2px rgba(9,23,23,0.05)',
    standard: '0 2px 8px rgba(9,23,23,0.08)',
    elevated: '0 4px 16px rgba(9,23,23,0.12)',
    modal: '0 16px 48px rgba(9,23,23,0.20)',
  },
  focus: {
    ring: '0 0 0 3px rgba(32,128,141,0.12)',
    ringError: '0 0 0 3px rgba(224,82,74,0.12)',
  },
  motion: {
    fast: '120ms',
    standard: '220ms',
    slow: '360ms',
    easeStandard: 'cubic-bezier(0.4, 0, 0.2, 1)',
    easeEnter: 'cubic-bezier(0, 0, 0.2, 1)',
    easeExit: 'cubic-bezier(0.4, 0, 1, 1)',
  },
} as const;

export type Tokens = typeof tokens;
