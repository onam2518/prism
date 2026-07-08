// Tailwind 사전 빌드 설정 · page.py 인라인 tailwind.config 와 1:1 (Play CDN 런타임 제거)
// 재생성: scripts/build_tailwind.sh (레포 루트에서)
module.exports = {
  content: ["prism/page.py", "prism/vendor/app.js"],
  theme: { extend: {
    fontFamily: {
      sans: ['"Pretendard Variable"', 'Pretendard', 'system-ui', '-apple-system', 'sans-serif'],
      mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
    },
    colors: {
      violet: { DEFAULT: '#1e84ff', hover: '#0066db', deep: '#004fad', tint: 'rgba(30,132,255,0.16)' },
      solar: '#18ba45',
      canvas: 'var(--ds-canvas)', surface: 'var(--ds-surface-white)', surface2: 'var(--ds-surface-white)',
      ink: 'var(--ds-ink)', body: 'var(--ds-body)', muted: 'var(--ds-muted)', hair: 'var(--ds-hairline)',
    },
  } },
};
