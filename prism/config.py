"""중앙 설정 (운영 하드닝). 모델·엔드포인트·단가·동시성·재시도·레이트리밋·임계·경로를 한 곳에."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
import copy
import json
import os
import sys
import threading

from . import modelmeta as _MM     # 고를 수 있는 모델 목록의 유일한 원천(이름·비용 등급도 여기)

HOME = os.path.dirname(os.path.dirname(__file__))

# 아무도 모델을 고르지 않았을 때 쓰는 이름 하나. serve 의 Upstage 시드 기본값
# (_SOLAR_MODEL_DEFAULT)도 이 값을 받아 쓴다. 같은 기본값을 두 곳에 적으면 한쪽만 바뀌어
# 조용히 어긋난다.
MODEL_DEFAULT = "solar-pro2"

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
    max_retries: int = 4            # 콜당 최대 재시도(429·5xx·네트워크 · 과금 없음)
    # 형식 실패(JSON 파싱 불가·빈 응답) 재시도 상한. 네트워크 재시도와 분리한 이유:
    # 이쪽은 HTTP 200 을 받고 버리는 것이라 **매회 과금**되고, temperature=0 이라 같은 요청을
    # 반복하면 같은 답이 온다(형식 불일치 모델에서 콘텐츠 1건당 15콜·46초 대기 실측 2026-08-11).
    # 재시도 시 프롬프트에 형식 지시를 덧붙여 요청 자체를 다르게 만든다(llm.complete_json).
    max_format_retries: int = 1
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
    # 프롬프트 캐시 '읽기' 단가. None = 미설정 → 캐시 토큰도 정가(chat_in)로 계산(종전 동작 유지).
    # 값을 넣으면 LLMResult.cost_usd 가 캐시로 읽은 토큰만 이 단가로 계산한다
    # (Upstage 공시 기준 입력가의 0.1배 → chat_in * 0.1 을 넣으면 된다).
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
    learn_repeat_days: int = 0            # 퀘스트 반복 주기(일) · 0=반복 없음 · 반영 후 같은 시각 +N일로 자동 재생성
    final_rerun_after_batch: bool = True  # 학습 반영 후 미확정분을 새 버전으로 자동 재실행(2층 검수 3-1)
    final_gold_check: bool = True         # 최종검수 큐에 골드 캘리브레이션 문항 블라인드 출제(정확도→신뢰가중)
    fallback_models: list = field(default_factory=list)   # 산출 전량 빈값 시 순서 폴백 모델(최대 3 · 실호출만)
    batch_budget_usd: float = 0.0         # 일괄 실행(재실행) 1회 비용 상한($) · 0 = 무제한
    # 검수 보조 에이전트가 답할 때 쓰는 모델(팀 공유 설정 · 시스템 설정 화면에서 관리자가 고른다).
    # 빈 값 = 미설정 = MODEL_DEFAULT. 해석은 아래 assist_model() 한 곳에서만 한다.
    assist_model: str = ""

    # 실행
    concurrency: int = 12
    timeout: int = 60
    # 프롬프트 캐싱 옵트인: 요청 바디에 prompt_cache_key 를 실을지(기본 off).
    # 근거는 llm.LLMClient._cache_key 주석. 끄는 법 = 이 값 false 또는 env PRISM_PROMPT_CACHE=0.
    prompt_cache: bool = False

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
        data = _read_config_file(p)
        if data:
            cfg = _merge(cfg, data)
        # env 오버라이드 — **캐시 대상이 아니다**. 44개 호출 지점 중에는 env 를 바꿔 가며
        # 재로드를 기대하는 경로(테스트·PRISM_CONFIG 격리 절차)가 있어서, 파일 파싱만 캐시하고
        # env 는 매번 다시 읽는다.
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
        if os.environ.get("PRISM_PROMPT_CACHE") is not None:      # 프롬프트 캐싱 옵트인 스위치
            cfg.prompt_cache = _envbool(os.environ["PRISM_PROMPT_CACHE"])
        if os.environ.get("PRISM_PRICE_CACHE_READ"):              # 캐시 읽기 단가(USD/1M) 주입
            try:
                cfg.prices.cache_read = float(os.environ["PRISM_PRICE_CACHE_READ"])
            except ValueError:
                pass
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


# ── 검수 보조 에이전트 모델 ──────────────────────────────────────────────────
# 설정 필드 `assist_model` 을 읽는 자리는 여기 둘뿐이다(목록·해석). 값을 읽는 쪽은
# assist_model() 하나만 부르면 되고, 미설정·잘못된 값·정상값을 구분하지 않아도 된다.
def assist_model_options() -> list:
    """고를 수 있는 모델 목록 = 설정 화면 드롭다운의 선택지이자 해석 함수의 유효값 집합.

    두 목록을 따로 두면 '화면에서는 고를 수 있는데 저장하면 기본값으로 되돌아가는 모델'이
    생긴다. 원천은 modelmeta 하나뿐이라 여기서 새로 만들지 않고 그대로 받는다.
    기본값은 항상 목록에 있어야 한다(그래야 해석 결과가 언제나 고를 수 있는 이름이다)."""
    out = [m for m in (_MM.KNOWN_ROUTER_MODELS or []) if isinstance(m, str) and m.strip()]
    if MODEL_DEFAULT not in out:
        out.append(MODEL_DEFAULT)
    return out


def assist_model(cfg: "Config | None" = None) -> str:
    """검수 보조 에이전트가 쓸 모델 이름. **언제나 바로 쓸 수 있는 이름 하나**를 돌려준다.

    미설정·잘못된 값·정상값 세 경우가 모두 여기서 끝난다(부르는 쪽이 분기하지 않아도 되게).
    기본값 수렴과 잘못된 값 처리가 두 군데로 갈리면 반드시 어긋나고, 어긋난 쪽이 조용히
    다른 모델을 부른다.

    모르는 이름은 예외가 아니라 기본값으로 조용히 수렴한다. 설정은 사람이 손으로 고치는
    자리라 오타·없어진 모델명·문자열 아닌 값이 실제로 들어오는데, 검수 보조는 검수 화면 옆에
    붙는 기능이라 설정 한 줄 때문에 검수가 멈추면 안 된다.

    미설정일 때 실행 모델(cfg.model)을 따라가지 않는 것도 규칙이다. 따라가면 이 설정이
    막으려던 상황(판정한 모델이 자기 판정을 설명하는 것)이 오히려 기본 동작이 된다.
    """
    c = cfg if isinstance(cfg, Config) else Config.load()
    name = getattr(c, "assist_model", "")
    name = name.strip() if isinstance(name, str) else ""
    return name if name in assist_model_options() else MODEL_DEFAULT


_FILE_CACHE = {}                 # path → (mtime_ns, size, data)
_FILE_LOCK = threading.Lock()


def _read_config_file(p: str):
    """config.json 파싱 결과를 mtime+size 로 메모이즈.

    종전에는 `Config.load()` 가 호출마다 exists+open+json.load 를 돌았다(29.2us/call ·
    호출 지점 44곳 · /config 는 15초 헬스체크 경로). 파일은 1.4KB 이고 런타임에 거의 안 바뀐다.
    stat 은 매번 하므로 **파일이 바뀌면 즉시 반영**되고(save_template 직후 포함),
    env 오버라이드는 캐시하지 않는다(load 본문에서 매번 적용).
    반환 dict 는 캐시 공유본이라 호출부가 통째로 바꿔 쓰지 못하게 _merge 가 컨테이너를 복사한다."""
    try:
        st = os.stat(p)
    except OSError:
        return None                                  # 파일 없음(= 기본값 Config)
    key = (st.st_mtime_ns, st.st_size)
    hit = _FILE_CACHE.get(p)
    if hit and hit[0] == key:
        return hit[1]
    with open(p, encoding="utf-8") as f:             # 손상 파일은 종전대로 예외 전파(조용한 기본값 금지)
        data = json.load(f)
    if not isinstance(data, dict):
        return None
    with _FILE_LOCK:
        _FILE_CACHE[p] = (key, data)
        if len(_FILE_CACHE) > 8:                     # 경로가 여럿(테스트·앱 번들)일 때 무한 성장 방지
            for k in list(_FILE_CACHE)[:-8]:
                _FILE_CACHE.pop(k, None)
    return data


def _envbool(v: str) -> bool:
    """env 스위치 해석. 빈 값·0·false·off·no = 꺼짐(그 밖은 켜짐)."""
    return str(v or "").strip().lower() not in ("", "0", "false", "off", "no")


def _merge(cfg: Config, data: dict) -> Config:
    """평면/중첩 키를 dataclass 에 안전 병합(알 수 없는 키 무시).

    dict·list 값은 **복사해서** 싣는다: data 는 _read_config_file 의 캐시 공유본이라
    호출부가 `cfg.stage_prompts[...] = …` 처럼 제자리 수정하면 캐시가 오염된다."""
    nested = {"retry": RetryPolicy, "rate": RateLimit,
              "prices": Prices, "thresholds": Thresholds}
    for k, v in data.items():
        if k in nested and isinstance(v, dict):
            sub = getattr(cfg, k)
            for sk, sv in v.items():
                if hasattr(sub, sk):
                    setattr(sub, sk, sv)
        elif hasattr(cfg, k) and k != "api_key":
            setattr(cfg, k, copy.deepcopy(v) if isinstance(v, (dict, list)) else v)
    return cfg
