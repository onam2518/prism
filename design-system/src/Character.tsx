import * as React from 'react';
import './theme.css';
import './components.css';

/** 다음프렌즈 캐릭터 메타 · 야구 팀 역할로 페르소나에 매핑. */
export const characters = {
  daesik: { name: '대식', role: '타자', color: '#2f6bff', file: 'daesik-batter' },
  yonghee: { name: '용희', role: '투수', color: '#16c098', file: 'yonghee-pitcher' },
  boksil: { name: '복실', role: '포수', color: '#f5a623', file: 'boksil-catcher' },
  ddakji: { name: '딱지', role: '감독', color: '#e0524a', file: 'ddakji-manager' },
} as const;

export type CharacterName = keyof typeof characters;

/** 번들러(import.meta.url)로 패키지 내 SVG 경로 해석. src 로 직접 지정도 가능. */
function assetUrl(name: CharacterName): string {
  return new URL(`../assets/characters/${characters[name].file}.svg`, import.meta.url).href;
}

export interface CharacterProps {
  name: CharacterName;
  /** px (정사각) */
  size?: number;
  /** 위아래 바운스 애니메이션 */
  bob?: boolean;
  alt?: string;
  /** 에셋 경로를 직접 지정(번들러 미사용 환경) */
  src?: string;
}

/** 단일 캐릭터 일러스트. */
export function Character({ name, size = 64, bob = false, alt, src }: CharacterProps) {
  const url = src ?? assetUrl(name);
  const c = characters[name];
  return (
    <span className={`ds-character ${bob ? 'ds-character--bob' : ''}`.trim()} style={{ width: size, height: size }}>
      <img src={url} alt={alt ?? `${c.name} (${c.role})`} />
    </span>
  );
}
