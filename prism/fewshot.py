"""Few-Shot 예시 선별·렌더. 프롬프트 룰만으로 부족한 부분을 예시로 보완."""
from __future__ import annotations
import json

from . import model_guides as MG
from .schema import normalize_rich_text


class FewShotPool:
    def __init__(self, rows: list):
        # rows: [{content:{...}, expected:{finalGrade,reasons}}]
        self.by_bucket = {}
        for r in rows:
            exp = r.get("expected", {})
            bucket = (exp.get("reasons") or ["normal"])[0]
            self.by_bucket.setdefault(bucket, []).append(r)

    @classmethod
    def from_jsonl(cls, path: str) -> "FewShotPool":
        rows = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return cls(rows)

    def select(self, model: str) -> list:
        k = MG.few_shot_k(model)
        if k <= 0:
            return []
        focus = MG.few_shot_focus(model) or []
        picks, seen = [], set()

        def add(bucket, n):
            for r in self.by_bucket.get(bucket, [])[:n]:
                key = r["content"].get("title", "")
                if key in seen:
                    continue
                seen.add(key)
                picks.append(r)

        # 취약 버킷에서 우선 채움
        per = max(1, k // (len(focus) + 1)) if focus else 0
        for b in focus:
            add(b, per)
        # 대조군 normal 일부
        add("normal", max(1, k // 4))
        # 남으면 다른 버킷에서 보충
        if len(picks) < k:
            for b in self.by_bucket:
                if len(picks) >= k:
                    break
                add(b, 1)
        return picks[:k]

    def render(self, model: str, body_chars: int = 180) -> str:
        """5 Focal Elements 의 '예시' 블록. JSON 출력 형태로 시범 답안 제시."""
        examples = self.select(model)
        if not examples:
            return ""
        lines = ["[예시: 아래 판정 기준을 따른다 (few-shot)]"]
        for i, r in enumerate(examples, 1):
            c = r["content"]
            exp = r["expected"]
            # 골든셋 본문은 원본(마크업 포함) 그대로 저장돼 있다 → 자르기 전에 정제한다.
            # 안 그러면 앞 180자가 통째로 `<div style=…>` 이라 예시가 아무 신호도 못 준다.
            body = normalize_rich_text(c.get("body", ""))[:body_chars]
            ans = {"finalGrade": exp.get("finalGrade", "G"),
                   "reasons": exp.get("reasons", [])}
            lines.append(
                f"\n예시{i})\n"
                f"서비스: {c.get('displayServiceName','')} | 제목: {c.get('title','')}\n"
                f"본문(일부): {body}\n"
                f"판정: {json.dumps(ans, ensure_ascii=False)}"
            )
        return "\n".join(lines)
