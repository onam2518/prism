"""검수 보조 에이전트 모델 설정(시스템 설정 · 팀 공유) 규칙 단언.

검수 보조는 "모델이 왜 그렇게 판정했나"를 설명하는 자리다. 판정한 모델이 설명까지 하면
틀린 판정도 말이 되게 꾸며 내므로 **품질 판정 모델과 따로** 고를 수 있어야 한다.
그래서 여기서 지키는 것은 화면 문구가 아니라 다음 규칙들이다.

  1. 미설정이면 기본값으로 수렴한다. 특히 **실행 모델(cfg.model)을 따라가지 않는다.**
     따라가면 이 설정이 막으려던 상황(자기 판정을 자기가 변호)이 기본 동작이 된다.
  2. 모르는 모델명은 예외가 아니라 기본값이다. 설정은 사람이 손으로 고치는 자리라
     오타·없어진 모델명·문자열 아닌 값이 실제로 들어오는데, 검수 화면 옆에 붙는 기능이
     설정 한 줄 때문에 멈추면 안 된다.
  3. 해석은 함수 하나(config.assist_model)뿐이다. 기본값 수렴과 잘못된 값 처리가 두 군데로
     갈리면 반드시 어긋나고, 어긋난 쪽이 조용히 다른 모델을 부른다. 그래서 이 함수는
     미설정·잘못된 값·정상값 **세 경우 모두**에서 곧바로 쓸 수 있는 이름을 돌려준다
     (부르는 쪽이 분기하지 않는다).
  4. 저장은 기존 /config(관리자 게이트) 한 경로뿐이고 새 저장 계층·새 라우트를 만들지 않는다.
     화면에는 저장된 글자가 아니라 **해석된 값**이 보인다(실제로 쓰이는 모델과 다른 이름을
     보여 주면 그것부터가 거짓말이다).
  5. 저장 시점에도 같은 목록으로 거른다. 읽을 때만 거르면 config.json 에는 없는 모델명이
     남고 화면에는 기본값이 보여서, 나중에 파일을 열어 본 사람이 틀린 결론을 낸다.
     거절은 부분 반영 없이 통째로 하고 사람이 읽을 한 줄을 돌려준다.
  6. 고를 수 있는 목록과 해석의 유효값 집합이 같다. 두 벌이 되면 "화면에서는 고를 수 있는데
     저장하면 기본값으로 되돌아가는 모델"이 생긴다. 화면 목록은 거기서 **지금 키로 부를 수
     있는 것만** 남긴다(못 부르는 모델을 고르면 설정 화면은 멀쩡한데 자유질문만 실패한다).
  7. 고른 모델이 판정 모델과 같아지면 화면이 알린다(막지는 않는다 · 판단은 사람이 한다).
     아무 말도 안 하면 이 설정을 따로 둔 이유가 사라진다.

실행: python3 -m pytest tests/test_assist_model.py -q  (stdlib unittest · 의존성 0)
"""
import inspect
import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import config as CFG
from prism import modelmeta as MM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = os.path.join(ROOT, "prism", "ui", "16-settings.html")
APPJS = os.path.join(ROOT, "prism", "vendor", "app-08-copytext.js")

# 설정 파일에 실제로 들어올 법한 잘못된 값들(손편집 오타 · 구버전 모델명 · 타입 사고).
JUNK = ["", "   ", "no-such-model", "solar-pro2-typo", "SOLAR-PRO2", "gpt-4",
        "solar pro2", "../../etc/passwd", "solar-pro2\n", None, 0, 1, 3.5, True,
        [], {}, ["claude-opus-5"], {"model": "claude-opus-5"}, b"solar-pro2", object()]


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _fn(js, name):
    """조각에서 메서드 본문 한 덩어리만 떼어 낸다(들여쓰기 6칸 관례).

    이웃 함수까지 훑으면 단언이 헐거워진다. 실제로 '저장 경로를 다른 주소로 바꾼다'는
    무력화가 옆 함수의 '/config' 때문에 통과했다(2026-08-13 실측)."""
    m = re.search(r"\n      (?:async |get )?" + name + r"\(.*?\n      \},", js, re.S)
    return m.group(0) if m else ""


class TestResolveRules(unittest.TestCase):
    """해석 함수 하나(config.assist_model)가 지키는 규칙."""

    def test_unset_falls_back_to_default(self):
        self.assertEqual(CFG.assist_model(CFG.Config()), CFG.MODEL_DEFAULT)

    def test_unset_does_not_follow_the_judging_model(self):
        """미설정일 때 실행 모델을 따라가면 안 된다(판정한 모델이 자기 판정을 설명하게 된다)."""
        cfg = CFG.Config()
        cfg.model = "claude-opus-5"                     # 품질 판정에 쓰는 모델
        cfg.stage_models = {"judge": "claude-opus-5"}
        self.assertEqual(CFG.assist_model(cfg), CFG.MODEL_DEFAULT)
        self.assertNotEqual(CFG.assist_model(cfg), cfg.model)

    def test_valid_value_is_returned_as_is(self):
        for m in ("claude-opus-5", "gpt-5.4-mini", "gemini-3.5-flash", "openai/gpt-5.4"):
            cfg = CFG.Config()
            cfg.assist_model = m
            self.assertEqual(CFG.assist_model(cfg), m, m)

    def test_surrounding_spaces_are_forgiven(self):
        cfg = CFG.Config()
        cfg.assist_model = "  claude-opus-5  "
        self.assertEqual(CFG.assist_model(cfg), "claude-opus-5")

    def test_unknown_value_converges_to_default_without_raising(self):
        for bad in JUNK:
            cfg = CFG.Config()
            cfg.assist_model = bad
            try:
                out = CFG.assist_model(cfg)
            except Exception as e:                      # noqa: BLE001 - 예외 자체가 실패 조건
                self.fail(f"잘못된 값 {bad!r} 에서 예외: {e!r}")
            self.assertEqual(out, CFG.MODEL_DEFAULT, repr(bad))

    def test_always_returns_a_usable_model_name(self):
        """부르는 쪽이 분기하지 않아도 되게: 어떤 입력이든 결과는 고를 수 있는 이름이다."""
        opts = set(CFG.assist_model_options())
        for v in JUNK + ["claude-opus-5", "solar-pro2", "kimi-k3"]:
            cfg = CFG.Config()
            cfg.assist_model = v
            out = CFG.assist_model(cfg)
            self.assertIsInstance(out, str)
            self.assertTrue(out.strip(), repr(v))
            self.assertIn(out, opts, repr(v))

    def test_default_is_selectable(self):
        """기본값이 목록에 없으면 '해석 결과는 언제나 고를 수 있는 이름' 이 거짓이 된다."""
        self.assertIn(CFG.MODEL_DEFAULT, CFG.assist_model_options())

    def test_options_come_from_modelmeta_only(self):
        """목록 원천은 modelmeta 하나. 여기서 모델 목록을 새로 만들거나 베껴 두지 않는다.

        원천을 바꿔치기해 보면 사본인지 아닌지가 드러난다(사본이면 바뀌지 않는다)."""
        self.assertEqual(CFG.assist_model_options(), list(MM.KNOWN_ROUTER_MODELS))
        orig = MM.KNOWN_ROUTER_MODELS
        MM.KNOWN_ROUTER_MODELS = ["solar-pro2", "brand-new-model"]
        self.addCleanup(lambda: setattr(MM, "KNOWN_ROUTER_MODELS", orig))
        self.assertIn("brand-new-model", CFG.assist_model_options())
        cfg = CFG.Config()
        cfg.assist_model = "brand-new-model"            # 원천에 새로 실린 모델은 곧바로 유효값
        self.assertEqual(CFG.assist_model(cfg), "brand-new-model")
        cfg.assist_model = "claude-opus-5"              # 원천에서 빠진 모델은 기본값으로
        self.assertEqual(CFG.assist_model(cfg), CFG.MODEL_DEFAULT)

    def test_cost_tier_source_is_modelmeta(self):
        """이름 옆 비용 등급도 원천이 하나다(설정 화면은 공용 드롭다운을 그대로 쓴다)."""
        meta = MM.model_meta({}, CFG.assist_model_options())
        for m in CFG.assist_model_options():
            self.assertIn(m, meta, m)
            self.assertIn("tierLabel", meta[m])

    def test_default_shared_with_serve_seed(self):
        """기존 기본값과 같은 값을 두 곳에 적지 않는다(한쪽만 바뀌면 조용히 어긋난다)."""
        from prism import serve as SV
        self.assertEqual(SV._SOLAR_MODEL_DEFAULT, CFG.MODEL_DEFAULT)

    def test_callable_without_arguments(self):
        """부르는 쪽(자유질문 담당)이 설정 객체를 들고 있지 않아도 된다."""
        sig = inspect.signature(CFG.assist_model)
        self.assertTrue(all(p.default is not inspect.Parameter.empty
                            for p in sig.parameters.values()))
        self.assertIn(CFG.assist_model(), set(CFG.assist_model_options()))


class _CfgIsolate(unittest.TestCase):
    """설정 파일·DB 를 임시 디렉토리로 격리(운영 config.json·prism.db 오염 금지)."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.cfg_path = os.path.join(td.name, "config.json")
        orig_path = CFG.DEFAULT_CONFIG_PATH
        CFG.DEFAULT_CONFIG_PATH = self.cfg_path
        self.addCleanup(lambda: setattr(CFG, "DEFAULT_CONFIG_PATH", orig_path))

        from prism import serve as SV
        self.SV = SV
        orig_db = os.environ.get("PRISM_DB")
        os.environ["PRISM_DB"] = os.path.join(td.name, "t.db")
        self.addCleanup(lambda: (os.environ.pop("PRISM_DB", None) if orig_db is None
                                 else os.environ.__setitem__("PRISM_DB", orig_db)))
        SV._STORE = None
        self.addCleanup(lambda: setattr(SV, "_STORE", None))
        self.addCleanup(SV._MMETA_CACHE.clear)
        SV._MMETA_CACHE.clear()
        CFG._FILE_CACHE.clear()
        self.addCleanup(CFG._FILE_CACHE.clear)

    def _save(self, value):
        """관리자가 화면에서 고른 것과 같은 경로(POST /config 본문 → apply_config)."""
        return self._save_many({"assist_model": value})

    def _save_many(self, body):
        out = self.SV.apply_config(dict(body), allow_key=True, team="teamA")
        CFG._FILE_CACHE.clear()                        # 같은 경로 재기록(mtime 동일 창) 방지
        return out


class TestSaveAndRead(_CfgIsolate):
    """저장은 기존 /config 경로 · 읽으면 같은 값 · 잘못된 값은 조용히 기본값."""

    def test_round_trip(self):
        out = self._save("claude-opus-5")
        self.assertEqual(out["assistModel"], "claude-opus-5")
        self.assertEqual(CFG.assist_model(), "claude-opus-5")
        self.assertEqual(self.SV.config_status(team="teamA")["assistModel"], "claude-opus-5")

    def test_persisted_to_config_file(self):
        """새 저장 계층을 만들지 않는다. 기존 설정 객체(config.json)에 그대로 들어간다."""
        self._save("gpt-5.4-mini")
        with open(self.cfg_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["assist_model"], "gpt-5.4-mini")

    def test_unknown_value_is_refused_at_save_time(self):
        """읽을 때만 거르면 파일에는 없는 모델명이 남는다. 저장 때 거절하고 한 줄로 말한다."""
        self._save("claude-opus-5")
        out = self._save("no-such-model")
        self.assertIn("error", out)
        self.assertIn("no-such-model", out["error"])   # 무엇이 거절됐는지 그대로 말한다
        self.assertTrue(len(out["error"]) > 10)
        with open(self.cfg_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["assist_model"], "claude-opus-5")   # 파일 불변
        self.assertEqual(CFG.assist_model(), "claude-opus-5")                 # 앞의 값 그대로

    def test_refusal_does_not_half_apply_the_request(self):
        """거절은 통째로 한다. 절반만 반영된 설정이 제일 찾기 어렵다."""
        before = self.SV.config_status(team="teamA")["legalEnabled"]
        out = self._save_many({"assist_model": "no-such-model", "legal_enabled": not before})
        self.assertIn("error", out)
        self.assertEqual(self.SV.config_status(team="teamA")["legalEnabled"], before)

    def test_hand_edited_unknown_value_still_reads_back_as_default(self):
        """안전망은 그대로 남는다 = 저장 경로를 지나지 않은 값(손편집·예전 값)."""
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump({"assist_model": "no-such-model"}, f)
        CFG._FILE_CACHE.clear()
        self.assertEqual(CFG.assist_model(), CFG.MODEL_DEFAULT)
        self.assertEqual(self.SV.config_status(team="teamA")["assistModel"], CFG.MODEL_DEFAULT)

    def test_hand_edited_config_file_does_not_break(self):
        """사람이 config.json 을 직접 고친 경우(이 경로는 apply_config 를 지나지 않는다)."""
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump({"assist_model": {"oops": 1}}, f)
        CFG._FILE_CACHE.clear()
        self.assertEqual(CFG.assist_model(), CFG.MODEL_DEFAULT)
        self.assertEqual(self.SV.config_status(team="teamA")["assistModel"], CFG.MODEL_DEFAULT)

    def test_clearing_returns_to_default(self):
        self._save("claude-opus-5")
        self._save("")
        self.assertEqual(CFG.assist_model(), CFG.MODEL_DEFAULT)

    def test_other_settings_do_not_wipe_it(self):
        """다른 설정 저장(단계 프롬프트 등)이 검수 보조 모델을 지우면 안 된다."""
        self._save("claude-fable-5")
        self.SV.apply_config({"legal_enabled": True, "golden_min_good": 2},
                             allow_key=True, team="teamA")
        CFG._FILE_CACHE.clear()
        self.assertEqual(CFG.assist_model(), "claude-fable-5")

    def test_status_carries_the_selectable_list(self):
        st = self.SV.config_status(team="teamA")
        self.assertTrue(set(st["assistModels"]) <= set(CFG.assist_model_options() + [st["assistModel"]]))
        self.assertIn(st["assistModel"], st["assistModels"])   # 현재 값이 목록에 있다


class _KeyEnv(unittest.TestCase):
    """키 환경변수 조작(테스트 간 오염 방지 · llm_for_model 캐시도 함께 비운다)."""

    KEYS = ("UPSTAGE_API_KEY", "PRISM_API_KEY", "PRISM_BIZROUTER_KEY",
            "PRISM_ROUTER_KEY", "PRISM_TIMELY_KEY")

    def _keys(self, **on):
        from prism import serve as SV
        for k in self.KEYS:
            orig = os.environ.get(k)
            self.addCleanup(lambda k=k, v=orig: (os.environ.pop(k, None) if v is None
                                                 else os.environ.__setitem__(k, v)))
            os.environ.pop(k, None)
        for k, v in on.items():
            os.environ[k] = v
        SV._LLM_CACHE.clear()
        self.addCleanup(SV._LLM_CACHE.clear)


class TestCandidateList(_KeyEnv):
    """화면 목록은 지금 키로 부를 수 있는 모델만. 값 형식은 평평한 모델 id 그대로."""

    def _cands(self):
        from prism import serve as SV
        return SV._assist_candidates(CFG.Config())

    def test_flat_ids_not_provider_pairs(self):
        """`provider|model` 로 바꾸지 않는다(이 값을 읽는 쪽 계약까지 흔들린다)."""
        self._keys(PRISM_TIMELY_KEY="t-key")
        for m in self._cands():
            self.assertIsInstance(m, str)
            self.assertNotIn("|", m)

    def test_router_key_only_drops_direct_solar_models(self):
        """solar* 는 Upstage 직접 호출이라 라우터 키만으로는 못 부른다(llm_for_model 규칙)."""
        self._keys(PRISM_TIMELY_KEY="t-key")
        cands = self._cands()
        self.assertIn("claude-opus-5", cands)
        self.assertNotIn("solar-pro3", cands)

    def test_upstage_key_only_keeps_solar_models(self):
        self._keys(UPSTAGE_API_KEY="up-key")
        cands = self._cands()
        self.assertIn("solar-pro3", cands)
        self.assertNotIn("claude-opus-5", cands)

    def test_no_keys_falls_back_to_full_list(self):
        """빈 드롭다운은 안내가 아니라 고장으로 읽힌다(새 설치·mock)."""
        self._keys()
        self.assertEqual(set(self._cands()), set(CFG.assist_model_options()))

    def test_current_value_always_listed(self):
        """부를 수 없게 됐어도 지금 골라진 값은 보여야 한다(드롭다운이 현재 선택을 표시)."""
        self._keys(PRISM_TIMELY_KEY="t-key")             # 기본값(solar)은 못 부르는 상태
        from prism import serve as SV
        cands = SV._assist_candidates(CFG.Config())
        self.assertIn(CFG.assist_model(CFG.Config()), cands)

    def test_status_serves_the_filtered_list(self):
        """화면에 실제로 나가는 것이 걸러진 목록이어야 한다.

        후보 함수만 검사하면 '함수는 거르는데 응답은 전체 목록'인 회귀가 그대로 지나간다
        (2026-08-13 무력화 실측에서 아무 단언도 안 걸렸다)."""
        from prism import serve as SV
        self._keys(PRISM_TIMELY_KEY="t-key")
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        orig_path = CFG.DEFAULT_CONFIG_PATH
        CFG.DEFAULT_CONFIG_PATH = os.path.join(td.name, "config.json")
        self.addCleanup(lambda: setattr(CFG, "DEFAULT_CONFIG_PATH", orig_path))
        orig_db = os.environ.get("PRISM_DB")
        os.environ["PRISM_DB"] = os.path.join(td.name, "t.db")
        self.addCleanup(lambda: (os.environ.pop("PRISM_DB", None) if orig_db is None
                                 else os.environ.__setitem__("PRISM_DB", orig_db)))
        SV._STORE = None
        self.addCleanup(lambda: setattr(SV, "_STORE", None))
        SV._MMETA_CACHE.clear()
        self.addCleanup(SV._MMETA_CACHE.clear)
        CFG._FILE_CACHE.clear()
        self.addCleanup(CFG._FILE_CACHE.clear)

        served = self.SV_status()
        self.assertNotIn("solar-pro3", served)          # 라우터 키로는 못 부르는 모델
        self.assertIn("claude-opus-5", served)
        self.assertEqual(served, SV._assist_candidates(CFG.Config.load()))

    def SV_status(self):
        from prism import serve as SV
        return SV.config_status(team="teamA")["assistModels"]

    def test_reachability_agrees_with_the_router(self):
        """판정 규칙 원천은 llm_for_model 하나. 두 벌이 어긋나면 여기서 잡힌다."""
        from prism import serve as SV
        self._keys()                                     # 원복 예약 먼저(아래에서 env 를 헤집는다)
        for env in ({}, {"PRISM_TIMELY_KEY": "t"}, {"UPSTAGE_API_KEY": "u"},
                    {"PRISM_BIZROUTER_KEY": "b"}, {"UPSTAGE_API_KEY": "u", "PRISM_TIMELY_KEY": "t"}):
            with self.subTest(env=sorted(env)):
                for k in self.KEYS:
                    os.environ.pop(k, None)
                os.environ.update(env)
                SV._LLM_CACHE.clear()
                for m in CFG.assist_model_options():
                    llm, why = SV.llm_for_model(m, False)
                    self.assertEqual(SV._assist_reachable(m), llm is not None, f"{m} · {why}")


class TestTeamScope(_CfgIsolate):
    """팀 공유 설정 · 쓰기는 기존 관리자 게이트 · 팀 스코프가 새는 자리를 새로 만들지 않는다."""

    def test_write_goes_through_the_existing_admin_gate(self):
        self.assertEqual(self.SV._POST_ROUTES["/config"][1], "admin")

    def test_no_new_route(self):
        for path in list(self.SV._POST_ROUTES) + list(self.SV._GET_ROUTES):
            self.assertNotIn("assist-model", path)
            self.assertNotIn("assist_model", path)

    def test_shared_value_not_stored_per_team(self):
        """팀별 값이 따로 저장되지 않는다 = 다른 팀으로 샐 팀 값 자체가 없다(실행 모델과 같은 관례)."""
        self._save("kimi-k3")
        a = self.SV.config_status(team="teamA")
        b = self.SV.config_status(team="teamB")
        self.assertEqual(a["assistModel"], b["assistModel"])
        self.assertEqual(b["assistModel"], "kimi-k3")
        self.assertNotIn("team", inspect.signature(CFG.assist_model).parameters)

    def test_unauthenticated_config_does_not_carry_it(self):
        """운영(supabase) 무인증 /config 슬림 응답에 팀 공유 설정이 실리면 안 된다."""
        SV = self.SV
        orig = SV._supa
        SV._supa = lambda: ("http://supabase.local", "test-key")
        self.addCleanup(lambda: setattr(SV, "_supa", orig))

        class _H:                                      # 로그인하지 않은 요청
            def _bearer_uid(self):
                return None

            def _req_team(self):
                return None

        out = SV._GET_ROUTES["/config"][0](_H(), {})
        self.assertNotIn("assistModel", out)
        self.assertNotIn("assistModels", out)


class TestSettingsScreen(unittest.TestCase):
    """화면: 공용 드롭다운 한 줄 · 비용 등급 노출 · 왜 따로 고르는지 한 줄 · 기존 /config 저장."""

    def setUp(self):
        from prism.page import PAGE
        self.page = PAGE
        self.markup = _read(SETTINGS)
        self.js = _read(APPJS)

    def test_uses_the_shared_model_dropdown(self):
        """네이티브 select 로 모델 목록을 새로 만들지 않는다(이름·등급 표시가 갈린다)."""
        self.assertIn("<x-modelpick", self.markup)
        self.assertIn("assistModels", self.markup)
        block = self.markup[self.markup.index("검수 보조 에이전트 모델"):]
        self.assertNotIn("<select", block)
        self.assertNotIn("<x-modelpick", self.page)    # 합성에서 전부 펼쳐진다

    def test_dropdown_is_wired_to_value_and_save(self):
        self.assertIn("mpRows(assistModels,{})", self.page)
        self.assertIn("assistModel=row.value", self.page)
        self.assertIn("saveAssistModel()", self.page)

    def test_cost_tier_is_visible_next_to_the_name(self):
        i = self.page.index("mpRows(assistModels")
        row = self.page[i:i + 1200]
        self.assertIn("mpick__nm", row)                # 모델 이름
        self.assertIn("mpick__tier", row)              # 그 옆 비용 등급 배지
        self.assertIn("row.tierLabel", row)

    def test_screen_says_why_it_is_separate(self):
        """왜 판정 모델과 따로 고르는지가 화면에 있어야 한다(설정 옆에 이유가 없으면 되돌려진다)."""
        i = self.markup.index("검수 보조 에이전트 모델")
        block = self.markup[i:i + 1200]
        self.assertIn("판정", block)
        self.assertIn("설명", block)
        self.assertRegex(block, r"다른 모델|따로")
        self.assertNotIn("—", block)                   # em dash 금지(산출물 공통 규칙)

    def test_saves_through_existing_config_route(self):
        """새 라우트를 만들지 않는다. 저장은 기존 /config(관리자 게이트) 한 경로뿐이다."""
        fn = _fn(self.js, "saveAssistModel")
        self.assertTrue(fn)
        self.assertIn("'/config'", fn)
        self.assertIn("assist_model", fn)
        self.assertIn("_authHeaders()", fn)
        self.assertEqual(re.findall(r"_afetch\('([^']+)'", fn), ["/config"])

    def test_screen_shows_the_value_the_server_resolved(self):
        """서버가 해석한 값을 되받아 그린다. 화면이 실제로 쓰이는 모델과 다른 이름을 들면 안 된다."""
        self.assertIn("j.assistModel", _fn(self.js, "saveAssistModel"))
        self.assertRegex(self.js, r"this\.assistModel = this\.cfg\.assistModel")

    def test_warns_when_it_equals_the_judging_model(self):
        """같아지면 알린다. 아무 말도 안 하면 이 설정을 따로 둔 이유가 사라진다."""
        i = self.markup.index("검수 보조 에이전트 모델")
        block = self.markup[i:i + 1600]
        self.assertIn("assistSameAsJudge", block)
        self.assertIn("판정 모델과 같습니다", block)
        self.assertIn("x-show", block)                 # 같을 때만 뜬다

    def test_warning_does_not_block_the_choice(self):
        """막지 않는다 · 판단은 사람이 한다(disabled·확인창으로 손을 묶지 않는다)."""
        i = self.markup.index("검수 보조 에이전트 모델")
        block = self.markup[i:i + 1600]
        self.assertNotIn("disabled", block)
        self.assertNotIn("dsConfirm", block)
        fn = _fn(self.js, "saveAssistModel")
        self.assertNotIn("assistSameAsJudge", fn)      # 저장 경로가 이 판단에 걸리지 않는다

    def test_judging_model_is_read_from_the_server_config(self):
        """판정 모델은 단계 모델(judge) 우선 · 없으면 실행 모델(라우터면 그쪽 id)."""
        fn = _fn(self.js, "judgeModel")
        self.assertTrue(fn)
        self.assertIn("stageModels", fn)
        self.assertIn("judge", fn)
        self.assertIn("textModel", fn)

    def test_list_comes_from_the_server(self):
        """선택지를 화면에서 따로 만들면 서버가 인정하지 않는 모델을 고를 수 있게 된다."""
        self.assertRegex(self.js, r"this\.assistModels = this\.cfg\.assistModels")
        for name in os.listdir(os.path.join(ROOT, "prism", "vendor")):
            if not name.endswith(".js"):
                continue
            src = _read(os.path.join(ROOT, "prism", "vendor", name))
            for m in re.findall(r"assistModels\s*[:=]\s*\[([^\]]*)\]", src):
                self.assertFalse(m.strip(), f"{name}: 화면에 모델 목록 사본이 있다")


if __name__ == "__main__":
    unittest.main()
