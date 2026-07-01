/**
 * Prism Design System — Tailwind preset.
 * Source of truth = Anchor Design System (axz): Blue(Primary)·Red(Accent)·무채색 캔버스
 * + 도메인 카테고리색 · Pretendard. 값 원본 design-system/anchor/tokens.json.
 *   tailwind.config = { presets: [require('@prism/design-system/tailwind.preset.cjs')] }
 */
module.exports = {
  theme: {
    extend: {
      colors: {
        primary: { DEFAULT: '#1e84ff', hover: '#0066db', deep: '#004fad', tint: 'rgba(30,132,255,0.16)' },
        ink: '#000000',
        canvas: '#f4f5f7',
        surface: '#ffffff',
        'surface-on': '#f4f5f7',
        body: 'rgba(0,0,0,0.88)',
        muted: 'rgba(0,0,0,0.48)',
        hairline: 'rgba(0,0,0,0.08)',
        'hairline-soft': 'rgba(0,0,0,0.04)',
        success: '#18ba45',
        error: '#ff4e33',
        warning: '#ff9429',
        info: '#1e84ff',
        // 도메인 카테고리 식별색
        'cat-news': '#1e84ff',
        'cat-shopping': '#ff4e33',
        'cat-sports': '#5c77ff',
        'cat-entertainment': '#a05cff',
        'cat-cafe': '#ff5c66',
        'cat-interest': '#ff9429',
        'cat-community': '#5e47eb',
        // dark
        'dark-canvas': '#161718',
        'dark-surface': '#202122',
        'blue-on-dark': '#66a8ff',
      },
      fontFamily: {
        sans: ['"Pretendard"', '"Pretendard Variable"', 'system-ui', '"Apple SD Gothic Neo"', 'sans-serif'],
        display: ['"Pretendard"', '"Pretendard Variable"', 'system-ui', 'sans-serif'],
        body: ['"Pretendard"', '"Pretendard Variable"', 'system-ui', 'sans-serif'],
        mono: ['"Berkeley Mono"', '"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        label: '12px',
        caption: '14px',
        body: '15px',
        answer: '17px',
        subtitle: '18px',
        heading: '22px',
        'heading-lg': '26px',
        display: '40px',
      },
      // Radius 4·8·12·16·24·100(pill)
      borderRadius: { xs: '4px', sm: '8px', DEFAULT: '8px', md: '12px', lg: '16px', xl: '24px', full: '9999px' },
      // Anchor Shadow 3단
      boxShadow: {
        low: '0 0 4px 0 rgba(0,0,0,0.04)',
        medium: '0 1px 10px 0 rgba(0,0,0,0.08)',
        high: '0 2px 16px 0 rgba(0,0,0,0.16)',
      },
    },
  },
};
