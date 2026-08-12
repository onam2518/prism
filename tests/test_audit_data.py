"""사전·토픽·메모리 데이터 계층 감사(2026-08-11) 회귀 테스트.

고친 결함(각 클래스가 그 결함 하나를 고정한다):
- T1 dictionaries._merge_override: 중첩 dict 오버라이드가 코드 신규 하위키를 통째로 날렸다.
- T2 dictops.edit_dict: 값 형태를 검증하지 않아 리스트 사전이 dict 로 뒤바뀐 채 영속됐다.
- T3 topic.build_event_topics: cluster_id·대표 엔티티가 set 순회(해시 시드) 순서에 의존했다.
- T4 memfs._observe: 자동 생성 경로가 자기 규칙(valid_path)을 벗어나 수정·삭제 불가가 됐다.
- T5 entconf._norm / entdict.normalize_name: 한글 NFD 입력에서 확신도·개체키가 갈렸다.
- T6 pastcheck.logviewer_data: 교차 검사(노출-클릭 연결)가 표시 상한 때문에 오탐했다.

실행: python3 -m pytest tests/test_audit_data.py -q   (stdlib · 네트워크 0)
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unicodedata
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import dictionaries as D           # noqa: E402
from prism import dictops as DO               # noqa: E402
from prism import entconf as EC               # noqa: E402
from prism import entdict as ED               # noqa: E402
from prism import memfs as MF                 # noqa: E402
from prism import pastcheck as PC             # noqa: E402
from prism import topic as TP                 # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 공통 픽스처 ────────────────────────────────────────────────────────────
class _DictStateCase(unittest.TestCase):
    """전역 사전을 만지는 테스트 공통: 원본 스냅샷 복원(병렬 테스트 오염 방지)."""

    def setUp(self):
        self._snap = {gk: copy.deepcopy(getattr(D, gk, None)) for gk in D._PROFILE_KEYMAP.values()}
        self._base_snapshot = D._BASE_SNAPSHOT
        D._BASE_SNAPSHOT = None                       # 이 테스트 기준의 '코드 기본값' 확정
        self.addCleanup(self._restore)

    def _restore(self):
        g = vars(D)
        for gk, base in self._snap.items():
            cur = g.get(gk)
            if isinstance(cur, dict) and isinstance(base, dict):
                cur.clear()
                cur.update(copy.deepcopy(base))
            else:
                g[gk] = copy.deepcopy(base)
        D._BASE_SNAPSHOT = self._base_snapshot


class _EditCase(_DictStateCase):
    """어드민 편집(serve.edit_dict) 경로: 오버라이드 파일을 임시 경로로 격리."""

    def setUp(self):
        super().setUp()
        import prism.serve as S
        self.S = S
        self._orig_path = S._DICT_OVERRIDES_PATH
        S._DICT_OVERRIDES_PATH = os.path.join(tempfile.mkdtemp(), "ov.json")
        self.addCleanup(lambda: setattr(S, "_DICT_OVERRIDES_PATH", self._orig_path))

    def _saved(self) -> dict:
        with open(self.S._DICT_OVERRIDES_PATH, encoding="utf-8") as f:
            return json.load(f)


class _Store:
    def __init__(self):
        self.reports = {}

    def save_report(self, kind, payload, team=None):
        self.reports[(kind, team)] = payload


class _SV:
    def __init__(self, rows=None):
        self.store = _Store()
        self.rows = rows or []

    def get_store(self):
        return self.store

    def _report_get(self, kind, team=None, default=None):
        return self.store.reports.get((kind, team), default)

    def results_rows(self, limit=5000, team=None):
        return self.rows


# ── T1 ─────────────────────────────────────────────────────────────────────
class TestNestedDictSubkeySurvives(_DictStateCase):
    """T1: dict-of-dict 오버라이드가 코드가 나중에 추가한 하위키를 지우면 안 된다.

    #388 이 리스트 경로에서 막은 '코드 신규 값 영구 삭제'의 dict 판. 편집 저장이 base
    전체를 파일에 박기 때문에, 손실이 편집한 키 하나가 아니라 그 사전의 전 키에 걸렸다."""

    def _stale_override(self, keep=("filter", "method", "status")) -> dict:
        """시딩 시점 스냅샷 흉내: 하위키가 keep 3종뿐이던 시절의 저장본."""
        return {"intake_policy": {k: {kk: vv for kk, vv in v.items() if kk in keep}
                                  for k, v in D.INTAKE_POLICY.items()}}

    def test_code_added_subkey_restored_for_every_key(self):
        ov = self._stale_override()
        ov["intake_policy"]["텍스트형"]["filter"] = "△"      # 사용자가 실제로 바꾼 값
        for v in D.INTAKE_POLICY.values():                   # 이후 코드가 하위키 추가
            v["note"] = "코드가 새로 추가한 값"

        rep = D.apply_profile(copy.deepcopy(ov))

        for k, v in D.INTAKE_POLICY.items():
            self.assertEqual(v.get("note"), "코드가 새로 추가한 값", f"{k} 에서 코드 신규 하위키 유실")
        self.assertEqual(D.INTAKE_POLICY["텍스트형"]["filter"], "△")   # 사용자 편집은 그대로
        self.assertTrue(rep["restored"], "기동 리포트에 복원 경고가 남아야 한다")

    def test_legal_types_subkey_restored(self):
        ov = {"legal_types": {k: {"label": v["label"]} for k, v in D.LEGAL_HARM_TYPES.items()}}
        D.apply_profile(ov)
        for k, v in D.LEGAL_HARM_TYPES.items():
            self.assertTrue(v.get("article"), f"{k} 의 조문이 오버라이드에 지워졌다")

    def test_removed_subkey_is_not_restored(self):
        """진짜 삭제(툼스톤 기록)는 유지 — 코드 신규 하위키 복원과 구분된다."""
        base_text = dict(D.INTAKE_POLICY["텍스트형"])
        ov = {"intake_policy": {k: dict(v) for k, v in D.INTAKE_POLICY.items()}}
        del ov["intake_policy"]["텍스트형"]["status"]         # 사용자가 하위키를 뺀다
        D.stamp_removals(ov, "intake_policy", key="텍스트형")

        tomb = ov[D.REMOVED_KEY]["intake_policy"]["텍스트형"]
        self.assertEqual(tomb[D.REMOVED_SUBKEYS], ["status"])

        rep = D.apply_profile(ov)
        self.assertNotIn("status", D.INTAKE_POLICY["텍스트형"])
        self.assertEqual(D.INTAKE_POLICY["이미지형"], base_text and D.INTAKE_POLICY["이미지형"])
        self.assertIn("intake_policy.텍스트형", rep["dropped"])

    def test_untouched_key_keeps_all_subkeys_when_sibling_edited(self):
        """단건 키 편집은 형제 키의 하위키를 건드리지 않는다(툼스톤 오기록 없음)."""
        ov = {"intake_policy": {k: dict(v) for k, v in D.INTAKE_POLICY.items()}}
        ov["intake_policy"]["영상형"]["status"] = "PoC"
        D.stamp_removals(ov, "intake_policy", key="영상형")
        self.assertNotIn(D.REMOVED_KEY, ov)

        D.apply_profile(ov)
        self.assertEqual(D.INTAKE_POLICY["영상형"]["status"], "PoC")
        self.assertEqual(set(D.INTAKE_POLICY["텍스트형"]), {"filter", "method", "status"})

    def test_remove_all_is_still_full_replacement(self):
        rep = D.apply_profile({"intake_policy": {"전용형": {"filter": "O"}},
                               D.REMOVED_KEY: {"intake_policy": D.REMOVE_ALL}})
        self.assertEqual(D.INTAKE_POLICY["전용형"], {"filter": "O"})
        self.assertFalse(rep["restored"])

    def test_merged_value_is_not_shared_with_base_snapshot(self):
        """복원한 하위키는 깊은 복사 — 이후 in-place 수정이 복원 기준(_BASE_SNAPSHOT)을 오염시키지 않는다."""
        D.INTAKE_POLICY["텍스트형"]["tags"] = ["a"]
        D.apply_profile({"intake_policy": {"텍스트형": {"filter": "O"}}})
        D.INTAKE_POLICY["텍스트형"]["tags"].append("b")
        D.restore_base()
        self.assertEqual(D.INTAKE_POLICY["텍스트형"]["tags"], ["a"])


class TestNestedDictEditRoundTrip(_EditCase):
    """T1 왕복: 키 편집 저장 → 코드에 하위키 추가 → 재기동 병합 후 하위키 생존."""

    def test_keyed_edit_then_restart_keeps_new_subkey(self):
        r = self.S.edit_dict({"target": "intake_policy", "key": "텍스트형",
                              "value": {"filter": "△", "method": "정상 분류", "status": "구현"}})
        self.assertTrue(r.get("saved"))
        self.assertNotIn(D.REMOVED_KEY, self._saved(), "값을 지우지 않은 편집에 툼스톤이 생겼다")

        # 재기동: 코드가 하위키를 추가한 새 버전
        self._restore()
        D._BASE_SNAPSHOT = None
        for v in D.INTAKE_POLICY.values():
            v["owner"] = "인입팀"
        self.S.load_dict_overrides()

        self.assertEqual(D.INTAKE_POLICY["텍스트형"]["filter"], "△")
        for k, v in D.INTAKE_POLICY.items():
            self.assertEqual(v.get("owner"), "인입팀", f"{k} 의 코드 신규 하위키가 편집 저장으로 소실")


# ── T2 ─────────────────────────────────────────────────────────────────────
class TestEditDictShapeGuard(_EditCase):
    """T2: 값 형태가 다른 편집은 진입부에서 거부 — 전역 사전이 다른 타입으로 뒤바뀌지 않는다."""

    def test_list_target_rejects_keyed_edit(self):
        r = self.S.edit_dict({"target": "iab_tier1", "key": "Sports", "value": ["x"]})
        self.assertIn("error", r)
        self.assertFalse(r.get("saved"))
        self.assertIsInstance(D.IAB_TIER1, list)
        self.assertFalse(os.path.exists(self.S._DICT_OVERRIDES_PATH), "거부한 편집이 파일에 남았다")

    def test_list_target_rejects_dict_value(self):
        r = self.S.edit_dict({"target": "intent_universal", "value": {"a": 1}})
        self.assertIn("error", r)
        self.assertIsInstance(D.INTENT_CATEGORIES_UNIVERSAL, list)

    def test_dict_target_rejects_list_value(self):
        r = self.S.edit_dict({"target": "intake_policy", "value": ["텍스트형"]})
        self.assertIn("error", r)
        self.assertIsInstance(D.INTAKE_POLICY, dict)

    def test_keyed_edit_rejects_wrong_value_shape(self):
        r = self.S.edit_dict({"target": "intake_policy", "key": "텍스트형", "value": "그냥 문자열"})
        self.assertIn("error", r)
        self.assertIsInstance(D.INTAKE_POLICY["텍스트형"], dict)

    def test_missing_value_rejected(self):
        r = self.S.edit_dict({"target": "iab_tier1"})
        self.assertIn("error", r)
        self.assertIsInstance(D.IAB_TIER1, list)

    def test_normal_edits_still_work(self):
        r = self.S.edit_dict({"target": "iab_tier1", "value": list(D.IAB_TIER1)[:5]})
        self.assertTrue(r.get("saved"))
        r = self.S.edit_dict({"target": "intake_policy", "key": "영상형",
                              "value": {"filter": "△", "method": "시각 이해", "status": "PoC"}})
        self.assertTrue(r.get("saved"))
        self.assertEqual(D.INTAKE_POLICY["영상형"]["status"], "PoC")
        r = self.S.edit_dict({"target": "intake_policy", "key": "신규형",     # 새 키 추가는 허용
                              "value": {"filter": "O", "method": "신설", "status": "계획"}})
        self.assertTrue(r.get("saved"))

    def test_dict_data_shape_unchanged_after_rejected_edit(self):
        self.S.edit_dict({"target": "iab_tier1", "key": "Sports", "value": ["x"]})
        self.assertEqual(len(self.S.dict_data()["iabTier1"]), len(D.IAB_TIER1))


class TestMergeTypeMismatchDefense(_DictStateCase):
    """T2 방어선: 손으로 만든(또는 구버전이 남긴) 형태 불일치 오버라이드도 적용하지 않는다."""

    def test_list_global_survives_dict_override(self):
        rep = D.apply_profile({"iab_tier1": {"Sports": ["x"]}})
        self.assertIsInstance(D.IAB_TIER1, list)
        self.assertIn("Sports", D.IAB_TIER1)
        self.assertIn("iab_tier1", rep.get("type_kept", {}))

    def test_dict_global_survives_list_override(self):
        rep = D.apply_profile({"intake_policy": ["텍스트형"]})
        self.assertIsInstance(D.INTAKE_POLICY, dict)
        self.assertIn("텍스트형", D.INTAKE_POLICY)
        self.assertIn("intake_policy", rep.get("type_kept", {}))

    def test_nested_type_mismatch_kept(self):
        rep = D.apply_profile({"intake_policy": {"텍스트형": "문자열"}})
        self.assertIsInstance(D.INTAKE_POLICY["텍스트형"], dict)
        self.assertIn("intake_policy.텍스트형", rep.get("type_kept", {}))


# ── T3 ─────────────────────────────────────────────────────────────────────
_SEED_SCRIPT = """
import json, sys
sys.path.insert(0, %r)
from prism import topic as TP

def row(title, ents):
    return {"content_ref": {"displayServiceName": "뉴스", "title": title, "subtitle": "", "body": title},
            "quality_meta": {"finalGrade": "G"},
            "item_meta": {"entities": ents, "intent": ["속보·단신"], "content_category": []}}

ents = ["김철수", "이영희", "박민수", "최지우"]
rows = [row("A", ents), row("B", ents), row("C", ents),
        row("D", ["가나다", "라마바"]), row("E", ["가나다", "라마바"])]
pools = TP.build_event_topics(rows, TP._service_names(rows), co_min=2)
print(json.dumps([[p["cluster_id"], p["name"], p["representative_entities"]] for p in pools],
                 ensure_ascii=False))
""" % (_ROOT,)


class TestEventTopicDeterminism(unittest.TestCase):
    """T3: cluster_id 는 큐레이션 제외의 영속 키다 — 재기동(=새 해시 시드)에도 같아야 한다."""

    def _run(self, seed: str) -> list:
        env = dict(os.environ, PYTHONHASHSEED=seed)
        out = subprocess.run([sys.executable, "-c", _SEED_SCRIPT], env=env, cwd=_ROOT,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_cluster_id_stable_across_hash_seeds(self):
        a, b = self._run("1"), self._run("2")
        self.assertTrue(a, "사건형 토픽이 하나도 안 생기면 회귀가 검증되지 않는다")
        self.assertEqual(a, b, "해시 시드만 다른데 cluster_id·이름이 달라졌다")

    def test_rep_entities_tie_broken_by_name(self):
        """동률 엔티티 순서 = 이름 오름차순(입력 순서·해시 순서에 좌우되지 않는다)."""
        def row(title, ents):
            return {"content_ref": {"displayServiceName": "뉴스", "title": title,
                                    "subtitle": "", "body": title},
                    "quality_meta": {"finalGrade": "G"},
                    "item_meta": {"entities": ents, "intent": ["속보·단신"], "content_category": []}}

        ents = ["최지우", "박민수", "이영희", "김철수"]                 # 역순 입력
        rows = [row("A", ents), row("B", ents), row("C", ents)]
        pools = TP.build_event_topics(rows, TP._service_names(rows), co_min=2)
        self.assertEqual(pools[0]["representative_entities"], sorted(ents))
        self.assertEqual(pools[0]["cluster_id"], "C-" + TP._slug("·".join(sorted(ents)[:2])))

    def test_input_order_does_not_change_cluster_id(self):
        def row(title, ents):
            return {"content_ref": {"displayServiceName": "뉴스", "title": title,
                                    "subtitle": "", "body": title},
                    "quality_meta": {"finalGrade": "G"},
                    "item_meta": {"entities": ents, "intent": ["속보·단신"], "content_category": []}}

        base = ["김철수", "이영희", "박민수"]
        rows_a = [row("A", base), row("B", base)]
        rows_b = [row("A", list(reversed(base))), row("B", list(reversed(base)))]
        ids_a = [p["cluster_id"] for p in TP.build_event_topics(rows_a, TP._service_names(rows_a), co_min=2)]
        ids_b = [p["cluster_id"] for p in TP.build_event_topics(rows_b, TP._service_names(rows_b), co_min=2)]
        self.assertEqual(ids_a, ids_b)

    def test_duplicate_entities_counted_once_per_content(self):
        """중복 제거는 유지(set → 순서 보존 목록으로 바꾼 뒤에도)."""
        cent = TP._content_entities(
            [{"content_ref": {"displayServiceName": "뉴스", "title": "t"},
              "quality_meta": {"finalGrade": "G"},
              "item_meta": {"entities": ["김철수", "김철수", "이영희"], "intent": [],
                            "content_category": []}}], {"뉴스"})
        self.assertEqual(cent, [["김철수", "이영희"]])


# ── T4 ─────────────────────────────────────────────────────────────────────
class TestMemfsObservePath(unittest.TestCase):
    """T4: 자동 생성 경로도 자기 규칙(valid_path)을 지켜야 수정·삭제가 가능하다."""

    LONG_Q = "오늘 하루 동안 있었던 국내외 스포츠 경기 결과와 선수 이적 소식을 모아 보고 싶어요"

    def setUp(self):
        self._old = MF._SV
        MF._SV = _SV(rows=[{"content_ref": {"title": "반도체 실적"},
                            "item_meta": {"content_category": ["Business and Finance"],
                                          "intent": ["기획·심층"], "summary": "요약", "entities": []},
                            "quality_meta": {"finalGrade": "G"}}])
        self.addCleanup(lambda: setattr(MF, "_SV", self._old))

    def test_long_topic_name_stays_within_rule(self):
        for cat in (self.LONG_Q, "a" * 80, "가" * 60, "!!! ???", ""):
            files = {}
            wrote, err = MF._observe(files, "제목", cat, "검색", "라벨", tag="stated")
            self.assertIsNone(err)
            self.assertIsNotNone(MF.valid_path(wrote["path"]),
                                 f"규칙 밖 경로 생성: {wrote['path']}")

    def test_unusable_name_falls_back_to_misc(self):
        files = {}
        wrote, _ = MF._observe(files, "제목", "!!! ???", "검색", "라벨")
        self.assertEqual(wrote["path"], "topics/misc.md")

    def test_search_written_file_is_editable_and_deletable(self):
        """무결과 검색(검색어가 곧 주제 파일) → 사용자가 그 파일을 고치고 지울 수 있다."""
        MF.demo_ops({"op": "event", "event": "search", "query": self.LONG_Q}, team="t1")
        path = MF.memory_data(team="t1")["files"][0]["path"]
        self.assertIsNotNone(MF.valid_path(path))

        ver = MF.memory_data(team="t1")["files"][0]["ver"]
        r = MF.memory_ops({"op": "append", "path": path, "line": "- 손으로 덧붙임"}, team="t1")
        self.assertNotIn("error", r)
        r = MF.memory_ops({"op": "write", "path": path, "content": "새 내용", "ver": ver + 1}, team="t1")
        self.assertNotIn("error", r)
        r = MF.memory_ops({"op": "delete", "path": path}, team="t1")
        self.assertNotIn("error", r)
        self.assertEqual(MF.memory_data(team="t1")["files"], [])

    def test_delete_clears_legacy_out_of_rule_file(self):
        """이미 만들어진 규칙 밖 파일(구 _observe 산물)도 지울 수 있어야 정원이 풀린다."""
        legacy = "topics/" + "가" * 45 + ".md"
        MF._SV.store.save_report(MF.KIND, {"files": {legacy: {"content": "x", "ver": 1, "updated": ""}}},
                                 team="t1")
        self.assertIsNone(MF.valid_path(legacy))
        r = MF.memory_ops({"op": "delete", "path": legacy}, team="t1")
        self.assertNotIn("error", r)
        self.assertEqual(MF.memory_data(team="t1")["files"], [])

    def test_delete_still_rejects_unknown_paths(self):
        for p in ("topics/nope.md", "../../etc/passwd", "", None):
            r = MF.memory_ops({"op": "delete", "path": p}, team="t1")
            self.assertIn("error", r)

    def test_write_append_still_reject_out_of_rule_paths(self):
        r = MF.memory_ops({"op": "write", "path": "topics/" + "가" * 45 + ".md", "content": "x"},
                          team="t1")
        self.assertIn("error", r)


# ── T5 ─────────────────────────────────────────────────────────────────────
def _nfd(s: str) -> str:
    return unicodedata.normalize("NFD", s)


class TestUnicodeNormalization(unittest.TestCase):
    """T5: NFD 로 실린 한글 콘텐츠에서도 확신도·개체키가 NFC 와 같아야 한다."""

    REF_NFC = {"title": "삼성전자 반도체 신규 투자 발표", "subtitle": "",
               "body": "삼성전자가 평택에 반도체 공장을 짓는다. 삼성전자 관계자는 …"}
    META = {"summary": "삼성전자 신규 투자", "entities": ["삼성전자", "평택"]}

    def test_confidence_same_for_nfd_and_nfc_content(self):
        ref_nfd = {k: _nfd(v) for k, v in self.REF_NFC.items()}
        for e in self.META["entities"]:
            self.assertEqual(EC.entity_confidence(e, ref_nfd, self.META),
                             EC.entity_confidence(e, self.REF_NFC, self.META), e)

    def test_title_entity_not_collapsed_in_nfd(self):
        """실측 회귀: NFD 본문에서 conf 0.25 로 접히던 제목 엔티티가 1.0 으로 돌아온다."""
        ref_nfd = {k: _nfd(v) for k, v in self.REF_NFC.items()}
        self.assertGreaterEqual(EC.entity_confidence("삼성전자", ref_nfd, self.META), 0.5)

    def test_nfd_entity_name_matches_nfc_body(self):
        self.assertEqual(EC.entity_confidence(_nfd("삼성전자"), self.REF_NFC, self.META),
                         EC.entity_confidence("삼성전자", self.REF_NFC, self.META))

    def test_normalize_name_and_entity_id_unified(self):
        self.assertEqual(ED.normalize_name(_nfd("삼성전자")), "삼성전자")
        self.assertEqual(ED.new_entity_id(_nfd("삼성전자")), ED.new_entity_id("삼성전자"))

    def test_eligible_length_counted_in_nfc(self):
        """코드포인트 기준 길이 컷: NFD 는 2~3배로 세어져 긴 이름이 조용히 탈락했다."""
        name = "한국거래소상장기업협의회사무국서울지부"        # 19자 · NFD 는 40자 초과
        self.assertGreater(len(_nfd(name)), 40)
        self.assertTrue(ED.eligible(name))
        self.assertEqual(ED.eligible(_nfd(name)), ED.eligible(name))

    def test_ingest_does_not_split_nfc_and_nfd_surfaces(self):
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        r1 = ED.ingest_meta(st, [("h1", ["삼성전자"])])
        r2 = ED.ingest_meta(st, [("h2", [_nfd("삼성전자")])])
        self.assertEqual((r1["created"], r2["created"]), (1, 0), "같은 이름이 두 개체로 갈렸다")
        self.assertEqual(st.ent_stats().get("total", 1), 1)

    def test_legacy_nfd_alias_absorbed_by_nfc_lookup(self):
        """이행: NFC 적용 전에 등재된 NFD 별칭은 재계산 없이 별칭 추가로 흡수한다."""
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        legacy = {"entity_id": "e_legacy", "name": _nfd("삼성전자"), "type": "OG",
                  "status": "active", "attrs": {}, "attr_meta": {}, "external_ids": {},
                  "merged_into": "", "created_at": 0, "updated_at": 0}
        st.ent_upsert(legacy)
        st.ent_alias_add(_nfd("삼성전자"), "e_legacy")

        r = ED.ingest_meta(st, [("h1", ["삼성전자"])])
        self.assertEqual(r["created"], 0, "구 NFD 개체를 못 찾아 중복 등재했다")
        self.assertEqual(st.ent_id_by_alias("삼성전자"), "e_legacy")   # NFC 별칭이 덧붙었다


# ── T6 ─────────────────────────────────────────────────────────────────────
class TestPastLinkCheckWindow(unittest.TestCase):
    """T6: 노출-클릭 교차 검사는 표시 상한과 분리 — 정상 계측을 오탐하지 않는다."""

    N = 20

    def setUp(self):
        events = [{"idx": i, "event": "impression", "dwell": 0, "scroll": 0,
                   "t": "10:00:%02d" % i, "title": "c%d" % i} for i in range(self.N)]
        for j in range(self.N):                       # 노출된 콘텐츠만 클릭·읽기(정상 계측)
            events.append({"idx": j, "event": "click", "dwell": 0, "scroll": 0,
                           "t": "10:%02d:00" % (j + 1), "title": "c%d" % j})
            events.append({"idx": j, "event": "read", "dwell": 30, "scroll": 80,
                           "t": "10:%02d:30" % (j + 1), "title": "c%d" % j})
        self.events = events
        self._old = MF._SV
        MF._SV = _SV()
        MF._SV.store.save_report(MF.DEMO_KIND, {"events": events}, team="t1")
        self.addCleanup(lambda: setattr(MF, "_SV", self._old))

    def test_link_check_sees_impressions_outside_display_window(self):
        d = PC.logviewer_data(team="t1")
        self.assertGreater(len(self.events), PC.LOGS_MAX, "표시 상한을 넘겨야 회귀가 검증된다")
        self.assertEqual(d["bypass"]["link_ok"], self.N)
        self.assertEqual(d["bypass"]["link_miss"], [])
        self.assertEqual(d["bypass"]["imp_ids"], self.N)

    def test_display_cap_still_applies(self):
        d = PC.logviewer_data(team="t1")
        self.assertEqual(d["n"], PC.LOGS_MAX)
        self.assertEqual(d["behavior"]["ops"], len(self.events))

    def test_real_link_miss_still_reported(self):
        """미탐 방지 확인: 노출되지 않은 콘텐츠 클릭은 여전히 연결 끊김으로 잡힌다."""
        self.events.append({"idx": 999, "event": "click", "dwell": 0, "scroll": 0,
                            "t": "11:00:00", "title": "c999"})
        MF._SV.store.save_report(MF.DEMO_KIND, {"events": self.events}, team="t1")
        d = PC.logviewer_data(team="t1")
        self.assertEqual(d["bypass"]["link_miss"], ["999"])

    def test_injected_examples_still_shown_and_counted(self):
        d = PC.logviewer_ops({"op": "inject"}, team="t1")
        self.assertEqual(len([l for l in d["logs"] if l["injected"]]), 7)
        self.assertEqual(d["n"], PC.LOGS_MAX + 7)
        self.assertEqual(d["behavior"]["dup"], 1)     # 중복 전송 예시 1건은 그대로 잡힌다
        self.assertEqual(d["checklist"]["dups"], ["앱 실행"])   # 1회성 기대 2건 = 중복(주입 의도)


if __name__ == "__main__":
    unittest.main()
