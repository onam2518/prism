// Prism Design System · 타입드 토큰. theme.css / anchor/tokens.json 와 1:1.
// Source of truth = Anchor Design System (axz): 무채색 캔버스 + Blue(Primary)·Red(Accent)
// + 도메인별 카테고리색 · Pretendard 단일 패밀리 · Light/Dark 자동 swap.
export const tokens = {
  color: {
    primary: '#1e84ff',        // atomic.Blue.500 · interaction.primary
    primaryHover: '#0066db',   // atomic.Blue.600
    primaryDeep: '#004fad',    // atomic.Blue.700
    primaryTint: 'rgba(30,132,255,0.16)', // atomic.Blue.100
    blueOnDark: '#66a8ff',     // atomic.Blue.400 · 다크 Primary

    ink: '#000000',            // text.primary
    canvas: '#f4f5f7',         // background.base (atomic.Gray.50)
    surface: '#ffffff',        // surface.base
    surfaceWhite: '#ffffff',
    surfaceOn: '#f4f5f7',      // surface.on
    body: 'rgba(0,0,0,0.88)',  // text.secondary
    muted: 'rgba(0,0,0,0.48)', // text.subtle
    placeholder: 'rgba(0,0,0,0.32)',
    hairline: 'rgba(0,0,0,0.08)',
    hairlineSoft: 'rgba(0,0,0,0.04)',

    darkCanvas: '#161718',     // background.base (dark)
    darkSurface: '#202122',    // surface.base (dark)
    darkSurfaceRaised: '#303233', // surface.on (dark)
    darkLine: 'rgba(255,255,255,0.08)',
    inkInverse: '#ffffff',
    inkInverseMuted: 'rgba(255,255,255,0.48)',

    success: '#18ba45',        // atomic.Green
    error: '#ff4e33',          // atomic.Red.500 · state.accent
    warning: '#ff9429',        // atomic.Orange.500
    info: '#1e84ff',           // state.info
    onPrimary: '#ffffff',      // text.static.white.primary

    // 도메인 카테고리 식별색 (background 500 기준)
    catNews: '#1e84ff',
    catShopping: '#ff4e33',
    catSports: '#5c77ff',          // atomic.Indigo.500
    catEntertainment: '#a05cff',   // atomic.Violet.500
    catCafe: '#ff5c66',            // atomic.Coral.500
    catInterest: '#ff9429',        // atomic.Orange.500
    catCommunity: '#5e47eb',       // atomic.Lavender.500
  },
  font: {
    sans: "'Pretendard', 'Pretendard Variable', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Apple SD Gothic Neo', sans-serif",
    display: "'Pretendard', 'Pretendard Variable', -apple-system, sans-serif",
    body: "'Pretendard', 'Pretendard Variable', -apple-system, sans-serif",
    mono: "'Berkeley Mono', 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
  },
  // 사이즈 스케일(11단계): 12·14·15·16·17·18·20·22·24·26·40
  fontSize: {
    label: '12px',
    caption: '14px',
    body: '15px',
    answer: '17px',
    subtitle: '18px',
    heading: '22px',
    headingLg: '26px',
    display: '40px',
    displayHero: '48px',
  },
  // 줄간격: 1.2(헤딩)·1.32(본문)·1.4(긴 본문)·1.52(장문)
  lineHeight: { tight: 1.2, snug: 1.32, normal: 1.4, relaxed: 1.52 },
  // 굵기 enum: 400(normal)·600(emphasis)·700(strong)
  fontWeight: { regular: 400, medium: 500, emphasis: 600, semibold: 600, strong: 700 },
  // Radius 4·8·12·16·24·100(pill)
  radius: { xs: '4px', sm: '8px', md: '12px', lg: '16px', xl: '24px', full: '9999px' },
  // Spacing 2·4·6·8·10·12·16·18·20·24·32·40
  space: { 0.5: '2px', 1: '4px', 1.5: '6px', 2: '8px', 2.5: '10px', 3: '12px', 4: '16px', 4.5: '18px', 5: '20px', 6: '24px', 8: '32px', 10: '40px' },
  // Shadow 3단 (low·medium·high). Anchor: 깊이는 surface 대비가 1차, shadow는 보조.
  shadow: {
    low: '0 0 4px 0 rgba(0,0,0,0.04)',
    medium: '0 1px 10px 0 rgba(0,0,0,0.08)',
    high: '0 2px 16px 0 rgba(0,0,0,0.16)',
  },
  focus: {
    ring: '0 0 0 3px rgba(30,132,255,0.4)',     // border.focus = Blue.500
    ringError: '0 0 0 3px rgba(255,78,51,0.4)',
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
