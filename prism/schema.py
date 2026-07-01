"""입출력 스키마. pydantic 없이 stdlib dataclass 로: 즉시 실행 가능(의존성 0)."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
import hashlib
import re
import unicodedata

# 제어문자(탭/개행 제외) 제거 패턴
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_text(s) -> str:
    """입력 견고성: None 안전, 유니코드 NFC, 제어문자 제거, 공백 정리."""
    if s is None:
        return ""
    s = str(s)
    s = unicodedata.normalize("NFC", s)
    s = _CTRL.sub(" ", s)
    s = re.sub(r"[ \t ]+", " ", s)        # 연속 공백 축약
    s = re.sub(r"\n{3,}", "\n\n", s)            # 과도 개행 축약
    return s.strip()


@dataclass
class Content:
    """입력 4필드 고정. 4필드 외는 받지도 추론하지도 않는다."""
    displayServiceName: str
    title: str
    subtitle: str = ""
    body: str = ""
    source_url: str = ""      # 참조용 원문 링크(추출 입력 아님 · 있으면 상세에서 '원문' 링크)

    @classmethod
    def from_dict(cls, d: dict) -> "Content":
        return cls(
            displayServiceName=normalize_text(d.get("displayServiceName", "")),
            title=normalize_text(d.get("title", "")),
            subtitle=normalize_text(d.get("subtitle", "")),
            body=normalize_text(d.get("body", "")),
            source_url=normalize_text(d.get("source_url", "") or d.get("url", "")),
        )

    def body_hash(self) -> str:
        return hashlib.sha1(self.body.encode("utf-8")).hexdigest()[:12]

    def ref(self) -> dict:
        return {
            "displayServiceName": self.displayServiceName,
            "title": self.title,
            "subtitle": self.subtitle,
            "source_url": self.source_url,
            "body_hash": self.body_hash(),
        }


@dataclass
class Routing:
    service_group: str            # media | ugc
    content_track: str            # text | image_only
    active_quality_metas: list = field(default_factory=list)


@dataclass
class HarmType:
    code: str
    routed_article: str
    scores: dict                  # {a,b,c,total}
    grade: str                    # GREEN | YELLOW | RED


@dataclass
class LegalMeta:
    enabled: bool = False
    harm_types: list = field(default_factory=list)
    representative_grade: str = "GREEN"
    representative_score: int = 0


@dataclass
class QualityMeta:
    finalGrade: str = "G"         # G | R (YELLOW 시 provisional)
    reasons: list = field(default_factory=list)   # 11종 ID 배열
    review: str = "auto"          # auto | yellow(사람 검수 필요)
    confidence: float | None = None   # 0~1 (YELLOW 판단 근거)
    review_reason: str = ""       # YELLOW 사유(불일치/중간대역)


@dataclass
class ItemMeta:
    # DNM 메타 체계(13. 프로젝트 기획 / 1312. 아이템 메타) 기준 필드명
    summary: str = ""                                     # 리드문 (생성 문장)
    entities: list = field(default_factory=list)          # 엔티티
    intent: list = field(default_factory=list)            # 인텐트 (속성 분류값)
    content_category: list = field(default_factory=list)  # 콘텐츠 카테고리(콘텐츠 단위 N개·복수 매핑)
    # 3차 메타(생성 시점 부여) · 사건형 토픽 식별·연결 신호. 1차 추출에서는 빈 값
    topic: str = ""                                       # 토픽 (사안 명사구)
    topic_categories: list = field(default_factory=list)  # 토픽 카테고리


@dataclass
class Trace:
    prompt_version: str = ""
    agent_verdicts: list = field(default_factory=list)
    fallbacks: list = field(default_factory=list)
    latency_ms: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    tokens: dict = field(default_factory=dict)


@dataclass
class Output:
    content_ref: dict
    routing: Routing
    legal_meta: LegalMeta
    quality_meta: QualityMeta
    item_meta: ItemMeta | None
    trace: Trace

    def to_dict(self, slim: bool = False) -> dict:
        """slim=True → 운영 출력(품질 2필드 + 아이템 4키). False → 전체(디버그)."""
        if slim:
            out = {
                "quality_meta": {
                    "finalGrade": self.quality_meta.finalGrade,
                    "reasons": self.quality_meta.reasons,
                },
            }
            if self.item_meta is not None:
                out["item_meta"] = asdict(self.item_meta)
            return out
        d = {
            "content_ref": self.content_ref,
            "routing": asdict(self.routing),
            "legal_meta": asdict(self.legal_meta),
            "quality_meta": asdict(self.quality_meta),
            "item_meta": asdict(self.item_meta) if self.item_meta is not None else None,
            "trace": asdict(self.trace),
        }
        return d
