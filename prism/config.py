"""중앙 설정 (운영 하드닝). 모델·엔드포인트·단가·동시성·재시도·레이트리밋·임계·경로를 한 곳에."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
import json
import os
import sys

HOME = os.path.dirname(os.path.dirname(__file__))

# 앱 번들(.app)은 읽기전용 → config 는 사용자 디렉터리에 둔다. 일반 실행은 레포 루트.
# 컨테이너(상시 서버)는 PRISM_CONFIG 로 볼륨 경로 지정(재시작 시 퀘스트 일시·모델 설정 보존).
if os.environ.get("PRISM_CONFIG"):
    DEFAULT_CONFIG_PATH = os.environ["PRISM_CONFIG"]
elif getattr(sys, "frozen", False):
    DEFAULT_CONFIG_PATH = os.path.expanduser("~/Library/Application Support/Prism/config.json")
else:
    DEFAULT_CONFIG_PATH = os.path.join(HOME, "config.json")


@dataclass
class RetryPolicy:
    max_retries: int = 4            # 콜당 최대 재시도
    base_delay: float = 1.0         # 지수백오프 기준(초)
    max_delay: float = 30.0
    jitter: float = 0.4             # 지터 비율(0~1)
    retry_status: tuple = (429, 500, 502, 503, 504)


@dataclass
class RateLimit:
    # Upstage 티어별 상한(미확정: 콘솔 실측 후 교체). 0=무제한.
    rpm: int = 0                    # 분당 요청 상한
    tpm: int = 0                    # 분당 토큰 상한(입력+출력 근사)


@dataclass
class Prices:
    # USD per 1M tokens (공시 2026-06-04, 미확정값은 콘솔 실측)
    chat_in: float = 0.15
    chat_out: float = 0.60
    cache_read: float | None = None
    embedding: float = 0.10


@dataclass
class Thresholds:
    prefilter_conf: float = 0.72    # 임베딩 품질 사전필터 신뢰 임계(=YELLOW 상단)
    yellow_low: float = 0.45        # YELLOW 중간대역 하단(이하·합의면 auto)
    legal_confidence: float = 0.30  # 법령 라우팅 임계
    legal_red: int = 70             # RED 차단 임계
    eval_gate: float = 0.85         # 정확도 게이트(§6.4)


@dataclass
class Config:
    # 엔드포인트·모델 (기본 없음 · init/--base-url/--model 로 지정. 미설정 시 --mock 만 가능)
    chat_url: str = ""
    embed_url: str = ""
    models_url: str = ""
    model: str = ""
    embed_query_model: str = ""
    embed_passage_model: str = ""
    reasoning_effort: str = "default"   # default|low|high|off
    system_prompt: str = ""             # 아이템 추출에 덧붙이는 추가 지시(선택, =analyze 하위호환)
    # 단계별 추가 지시(프롬프트 스튜디오): extract·analyze·review·judge
    stage_prompts: dict = field(default_factory=dict)
    stage_prompts_meta: dict = field(default_factory=dict)   # {stage: "최종 수정 시각"}
    # 단계별 모델 지정 + 모델별 프롬프트(각 과정이 다른 모델을 쓸 수 있음)
    stage_models: dict = field(default_factory=dict)         # {stage: model_id}
    model_prompts: dict = field(default_factory=dict)        # {model_id: {stage: prompt}}
    # 자동 인입 파이프라인 소스(API/Kafka 등). 각: {id,type,name,enabled,...연결정보}
    ingest_sources: list = field(default_factory=list)

    # ── 모델 슬롯(제공자 선택) ──
    # 텍스트 슬롯: 메타·품질·법령 추출. solar(직접) | bizrouter | timely(통합 라우터).
    # 비전 슬롯: 이미지 맥락 생성. upstage_ie(Information Extraction) | bizrouter | timely.
    # 라우터는 OpenAI 호환. 키는 라우터별 비밀값(env PRISM_BIZROUTER_KEY / PRISM_TIMELY_KEY).
    text_provider: str = "solar"        # solar | bizrouter | timely
    text_model: str = ""                # 라우터일 때 public id (예: gpt-5.4 / openai/gpt-5.4)
    vision_provider: str = "upstage_ie"  # upstage_ie | bizrouter | timely
    vision_model: str = ""              # 라우터일 때 public id
    legal_enabled: bool = False         # 품질 1차 법령 필터 포함 여부(단건/일괄)
    golden_min_good: int = 1            # 골든 확정 최소 '정확' 인원(팀 규모에 맞게 상향 가능)
    # 아이템 메타 분리형 4호출(계약 기본). False 면 통합 1콜 폴백.
    meta_four_calls: bool = True
    # 호출별 모델 티어: {summary|entities|intent|category: model_id}. 빈 값 = 실행 모델.
    meta_call_models: dict = field(default_factory=dict)
    # 모델 계열 쿡북 래퍼 오버라이드: {gpt|gemini|claude|solar|default: template}. 수정 단위는 계열 래퍼만.
    family_wrappers: dict = field(default_factory=dict)
    learn_next_at: str = ""               # 검수 목표(퀘스트) 일시 'YYYY-MM-DDTHH:MM' · 도달 시 학습 반영 1회 후 소진
    learn_team: str = ""                  # 그 퀘스트를 만든 팀 · 스케줄러가 이 팀으로 학습 배치를 돌려 골든·버전을 팀에 태깅

    # 실행
    concurrency: int = 12
    timeout: int = 60

    # 정책(중첩)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    rate: RateLimit = field(default_factory=RateLimit)
    prices: Prices = field(default_factory=Prices)
    thresholds: Thresholds = field(default_factory=Thresholds)

    # 경로
    db_path: str = os.path.join(HOME, "prism.db")
    emb_cache_path: str = os.path.join(HOME, ".emb_cache.json")
    runs_dir: str = os.path.join(HOME, "runs")

    # 비밀값(파일에 저장 금지: env 에서만)
    api_key: str = ""

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        cfg = cls()
        p = path or DEFAULT_CONFIG_PATH
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            cfg = _merge(cfg, data)
        # env 오버라이드
        cfg.api_key = os.environ.get("PRISM_API_KEY", os.environ.get("UPSTAGE_API_KEY", cfg.api_key))
        if os.environ.get("PRISM_DB"):                 # 컨테이너 볼륨 등으로 DB 경로 지정
            cfg.db_path = os.environ["PRISM_DB"]
        if os.environ.get("PRISM_MODEL"):
            cfg.model = os.environ["PRISM_MODEL"]
        if os.environ.get("PRISM_CONCURRENCY"):
            cfg.concurrency = int(os.environ["PRISM_CONCURRENCY"])
        if os.environ.get("PRISM_RPM"):
            cfg.rate.rpm = int(os.environ["PRISM_RPM"])
        if os.environ.get("PRISM_TPM"):
            cfg.rate.tpm = int(os.environ["PRISM_TPM"])
        if os.environ.get("PRISM_BASE_URL"):
            cfg.set_base_url(os.environ["PRISM_BASE_URL"])
        if os.environ.get("PRISM_EMBED_MODEL"):
            cfg.embed_query_model = cfg.embed_passage_model = os.environ["PRISM_EMBED_MODEL"]
        return cfg

    def is_configured(self) -> bool:
        """실제 호출에 필요한 엔드포인트·모델이 갖춰졌는지."""
        return bool(self.chat_url and self.model)

    def set_base_url(self, base: str):
        """OpenAI 호환 base URL(예: https://api.openai.com/v1)로 엔드포인트 일괄 설정."""
        b = base.rstrip("/")
        self.chat_url = b + "/chat/completions"
        self.embed_url = b + "/embeddings"
        self.models_url = b + "/models"

    def redacted(self) -> dict:
        """매니페스트 적재용: api_key 마스킹."""
        d = asdict(self)
        d["api_key"] = ("set(len=%d)" % len(self.api_key)) if self.api_key else "unset"
        return d

    def save_template(self, path: str | None = None):
        """config.json 템플릿 작성(비밀값 제외)."""
        d = asdict(self)
        d.pop("api_key", None)
        target = path or DEFAULT_CONFIG_PATH
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)


def _merge(cfg: Config, data: dict) -> Config:
    """평면/중첩 키를 dataclass 에 안전 병합(알 수 없는 키 무시)."""
    nested = {"retry": RetryPolicy, "rate": RateLimit,
              "prices": Prices, "thresholds": Thresholds}
    for k, v in data.items():
        if k in nested and isinstance(v, dict):
            sub = getattr(cfg, k)
            for sk, sv in v.items():
                if hasattr(sub, sk):
                    setattr(sub, sk, sv)
        elif hasattr(cfg, k) and k != "api_key":
            setattr(cfg, k, v)
    return cfg
