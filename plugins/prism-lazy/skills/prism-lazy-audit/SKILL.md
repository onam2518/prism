---
name: prism-lazy-audit
description: >
  저장소 전체를 과잉 설계 관점으로 감사한다. diff 가 아니라 트리 전체를 훑어
  지울 것 · 단순화할 것 · 표준 라이브러리 · 브라우저 기본으로 바꿀 것을 큰 것부터
  순위로 낸다. 사용자가 "/prism-lazy-audit", "저장소 감사", "군살 찾아줘",
  "ponytail 주석 모아줘" 라고 할 때 쓴다. 보고만 하고 고치지 않는다.
---

prism-lazy-review 의 저장소 전체 판. 큰 파일(`serve.py` · `store.py` · `supastore.py`)은 통독하지 말고 `ARCHITECTURE.md` 의 클러스터 지도를 보고 grep · `sed -n` 으로 구역만 읽는다. 범위가 넓으면 서브에이전트로 나눈다(서버 · 저장·LLM 계층 · UI/JS).

## 태그

prism-lazy-review 와 같다: `delete:` `stdlib:` `native:` `yagni:` `shrink:`.

## 사냥감

- 표준 라이브러리가 이미 해 주는 손 구현(json · itertools · functools · statistics · difflib · html · urllib).
- 구현 하나뿐인 인터페이스 · 제품 하나뿐인 팩토리 · 위임만 하는 래퍼 · 항목 하나만 내보내는 파일.
- 아무도 안 켜는 플래그 · 설정 · 죽은 라우트.
- 두 저장 계층(`store.py` · `supastore.py`)에 같은 논리를 복붙한 곳.
- Alpine 조각에서 CSS · `<input>` 속성으로 될 일을 JS 로 한 곳.
- `# ponytail:` 주석: 미룬 지름길을 모아 원장(한계 · 올릴 방법 · 위치)으로 낸다.

## 출력

한 건에 한 줄, 큰 절감 순: `<tag> <자를 것>. <대신할 것>. [path:line]`.
끝에 `net: -N lines possible.` 자를 게 없으면 `Lean already. Ship.`
마지막에 `# ponytail:` 원장 표(위치 · 한계 · 올릴 때).

## 경계

과잉 설계 · 복잡도만. 정확성 · 보안 · 성능은 별도 리뷰로. 의존성 0 원칙이므로 "패키지 쓰면 짧아진다"는 제안은 내지 않는다. 목록만 내고 고치지 않는다.
