---
name: prism-lazy-review
description: >
  현재 diff 를 과잉 설계 관점으로만 리뷰한다. 지울 것 · 표준 라이브러리로 바꿀 것 ·
  브라우저 기본으로 바꿀 것을 한 줄씩 낸다. 사용자가 "/prism-lazy-review",
  "과잉 설계 리뷰", "뭘 지울 수 있어", "더 짧게 안 되나" 라고 할 때 쓴다.
  정확성 · 보안 · 성능 리뷰는 /code-review 로 보낸다.
---

`git diff`(스테이징 포함) 를 읽고 불필요한 복잡도만 찾는다. 결과가 짧아지는 게 가장 좋은 결말이다.

## 형식

`<file>:L<line>: <tag> <자를 것>. <대신할 것>.` 한 건에 한 줄.

- `delete:` 죽은 코드 · 안 쓰는 유연성 · 추측성 기능. 대신할 것 없음.
- `stdlib:` 손으로 짠 표준 라이브러리 기능. 함수 이름을 댄다.
- `native:` 브라우저 · SQLite 가 이미 해 주는 것. 기능 이름을 댄다.
- `yagni:` 구현 하나뿐인 추상화 · 아무도 안 바꾸는 설정 · 호출자 하나뿐인 계층.
- `shrink:` 같은 논리를 더 짧게. 짧은 형태를 보여 준다.

## 예

- `prism/learnops.py:L412: shrink: 딕셔너리 만드는 루프 8줄. dict(zip(keys, vals)) 한 줄.`
- `prism/ui/13-eval.html:L40: native: JS 로 날짜 검증. <input type="date" min=...> 이 한다.`
- `prism/serve.py:L900: yagni: 구현 하나뿐인 Provider 클래스. 함수 하나로 인라인.`

## 마무리

`net: -N lines possible.` 한 줄. 자를 게 없으면 `Lean already. Ship.` 하고 끝.

## 경계

과잉 설계만 본다. 버그 · 보안 · 성능은 범위 밖. 스모크 테스트 한 건이나 assert 자가 검증은 최소치이지 군살이 아니므로 삭제 대상으로 올리지 않는다. 고치지 않고 목록만 낸다.
