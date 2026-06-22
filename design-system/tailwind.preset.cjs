/**
 * Prism Design System — Tailwind preset.
 * Prism UI(serve.py)나 다른 Tailwind 프로젝트에서 이 토큰을 그대로 쓰려면:
 *   tailwind.config = { presets: [require('@prism/design-system/tailwind.preset.cjs')] }
 */
module.exports = {
  theme: {
    extend: {
      colors: {
        violet: { DEFAULT: '#5b52ff', hover: '#4a42e0', deep: '#281ca5' },
        solar: '#d2ff95',
        canvas: '#0b0a0f',
        surface: '#141318',
        surface2: '#1a1922',
        body: '#9aa0aa',
        muted: '#6e7191',
      },
      fontFamily: {
        sans: ['Geist', 'system-ui', 'sans-serif'],
        mono: ['"Geist Mono"', 'monospace'],
      },
      borderRadius: { control: '8px' },
      boxShadow: { none: 'none' },
    },
  },
};
