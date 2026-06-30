/**
 * Prism Design System — Tailwind preset (Perplexity 정렬).
 * Prism UI(serve.py)나 다른 Tailwind 프로젝트에서 이 토큰을 그대로 쓰려면:
 *   tailwind.config = { presets: [require('@prism/design-system/tailwind.preset.cjs')] }
 */
module.exports = {
  theme: {
    extend: {
      colors: {
        primary: { DEFAULT: '#20808d', hover: '#1a6873', deep: '#13343b', tint: '#e5f2f2' },
        ink: '#091717',
        canvas: '#fbfaf4',
        surface: '#fcfcf9',
        body: '#2e3a3a',
        muted: '#5c6a6a',
        hairline: '#e4e4dc',
        'hairline-soft': '#efefe9',
        success: '#1f9d6b',
        error: '#e0524a',
        warning: '#d9923a',
        // dark
        'dark-canvas': '#0d1117',
        'dark-surface': '#161b22',
        'teal-on-dark': '#34b4c4',
      },
      fontFamily: {
        sans: ['"FK Grotesk"', 'Inter', 'system-ui', 'sans-serif'],
        display: ['"FK Display"', '"FK Grotesk"', 'Inter', 'sans-serif'],
        body: ['"FK Grotesk Neue"', '"FK Grotesk"', 'Inter', 'sans-serif'],
        mono: ['"Berkeley Mono"', '"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      borderRadius: { sm: '6px', DEFAULT: '10px', md: '10px', lg: '12px', xl: '16px' },
      boxShadow: {
        ambient: '0 1px 3px rgba(9,23,23,0.06)',
        subtle: '0 1px 2px rgba(9,23,23,0.05)',
        standard: '0 2px 8px rgba(9,23,23,0.08)',
        elevated: '0 4px 16px rgba(9,23,23,0.12)',
        modal: '0 16px 48px rgba(9,23,23,0.20)',
      },
    },
  },
};
