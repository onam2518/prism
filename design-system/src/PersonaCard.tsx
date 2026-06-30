import * as React from 'react';
import './theme.css';
import './components.css';
import { Character, characters, type CharacterName } from './Character';

export interface CardStat {
  label: string;
  value: number;
}

/** 캐릭터별 카드 기본값 (역할 스탯 + 희귀도). */
export const personaCards: Record<CharacterName, { rarity: string; stats: CardStat[] }> = {
  daesik: { rarity: 'RARE', stats: [{ label: '실행', value: 95 }, { label: '속도', value: 88 }, { label: '정밀', value: 72 }] },
  yonghee: { rarity: 'RARE', stats: [{ label: '전략', value: 92 }, { label: '정밀', value: 90 }, { label: '속도', value: 70 }] },
  boksil: { rarity: 'EPIC', stats: [{ label: '수비', value: 94 }, { label: '신중', value: 91 }, { label: '실행', value: 65 }] },
  ddakji: { rarity: 'LEGEND', stats: [{ label: '결정', value: 96 }, { label: '통솔', value: 93 }, { label: '실행', value: 60 }] },
};

export interface PersonaCardProps {
  character: CharacterName;
  name?: string;
  role?: string;
  rarity?: string;
  stats?: CardStat[];
  src?: string;
}

/** 홀로그래픽 트레이딩 카드(야구/포켓몬 카드)로 표현한 페르소나. 포인터 틸트 + CSS 포일. */
export function PersonaCard({ character, name, role, rarity, stats, src }: PersonaCardProps) {
  const c = characters[character];
  const preset = personaCards[character];
  const ref = React.useRef<HTMLDivElement>(null);

  const onMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width;
    const py = (e.clientY - r.top) / r.height;
    el.style.setProperty('--ry', `${(px - 0.5) * 16}deg`);
    el.style.setProperty('--rx', `${(0.5 - py) * 16}deg`);
    el.style.setProperty('--mx', `${px * 100}%`);
    el.style.setProperty('--my', `${py * 100}%`);
  };
  const reset = () => {
    const el = ref.current;
    if (!el) return;
    el.style.setProperty('--ry', '0deg');
    el.style.setProperty('--rx', '0deg');
    el.style.setProperty('--mx', '50%');
    el.style.setProperty('--my', '30%');
  };

  return (
    <div
      ref={ref}
      className="ds-personacard"
      style={{ ['--card-c' as string]: c.color }}
      onPointerMove={onMove}
      onPointerLeave={reset}
    >
      <div className="ds-personacard__inner">
        <div className="ds-personacard__foil" />
        <div className="ds-personacard__head">
          <div>
            <div className="ds-personacard__name">{name ?? c.name}</div>
            <div className="ds-personacard__role">{role ?? c.role}</div>
          </div>
          <span className="ds-personacard__rarity">{rarity ?? preset.rarity}</span>
        </div>
        <div className="ds-personacard__art">
          <Character name={character} size={120} src={src} />
        </div>
        <div className="ds-personacard__stats">
          {(stats ?? preset.stats).map((s) => (
            <div key={s.label} className="ds-personacard__stat">
              <b>{s.label}</b>
              <span className="ds-personacard__bar">
                <span style={{ width: `${Math.max(0, Math.min(100, s.value))}%` }} />
              </span>
              <span className="ds-personacard__val">{s.value}</span>
            </div>
          ))}
        </div>
        <div className="ds-personacard__sheen" />
      </div>
    </div>
  );
}
