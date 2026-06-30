import * as React from 'react';
import './theme.css';
import './components.css';
import { Character, characters, type CharacterName } from './Character';

export interface PersonaProps {
  character: CharacterName;
  /** 미지정 시 캐릭터 기본 이름 사용 */
  name?: string;
  /** 미지정 시 캐릭터 기본 역할 사용 */
  role?: string;
  description?: string;
  src?: string;
}

/** 사용자 페르소나 카드. 다음프렌즈 캐릭터를 아바타로. */
export function Persona({ character, name, role, description, src }: PersonaProps) {
  const c = characters[character];
  return (
    <div className="ds-persona">
      <span className="ds-persona__avatar">
        <Character name={character} size={48} src={src} />
      </span>
      <div>
        <div className="ds-persona__name">{name ?? c.name}</div>
        <div className="ds-persona__role">{role ?? c.role}</div>
        {description && <div className="ds-persona__desc">{description}</div>}
      </div>
    </div>
  );
}
