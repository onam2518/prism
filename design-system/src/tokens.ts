// Prism Design System — 타입드 토큰. tokens.json / theme.css 와 1:1.
export const tokens = {
  color: {
    violet: '#5b52ff',
    violetHover: '#4a42e0',
    violetDeep: '#281ca5',
    solar: '#d2ff95',
    canvas: '#0b0a0f',
    surface: '#141318',
    surface2: '#1a1922',
    heading: '#ffffff',
    body: '#9aa0aa',
    muted: '#6e7191',
    border: 'rgba(255,255,255,0.08)',
    borderStrong: 'rgba(255,255,255,0.10)',
    success: '#34d399',
    danger: '#fb7185',
    warning: '#fbbf24',
  },
  font: {
    sans: 'Geist, system-ui, sans-serif',
    mono: '"Geist Mono", monospace',
  },
  fontSize: { xs: '12px', sm: '13px', base: '14px', md: '15px', lg: '18px', xl: '24px', '2xl': '30px' },
  fontWeight: { regular: 400, medium: 500, semibold: 600 },
  radius: { none: '0px', control: '8px', lg: '12px', full: '9999px' },
  space: { 1: '4px', 2: '8px', 3: '12px', 4: '16px', 5: '20px', 6: '24px', 8: '32px' },
  shadow: { none: 'none' },
} as const;

export type Tokens = typeof tokens;
