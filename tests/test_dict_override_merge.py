"""사전 오버라이드 병합 회귀: 저장 시점 스냅샷이 이후 코드에 추가된 사전 값을 지우지 못하게.

결함(2026-08): apply_profile 이 리스트를 통째 재바인딩해 어드민 사전 편집을 한 번이라도 쓴
인스턴스는 그 시점 사전에 고착됐다. 실증 — 2026-07-06 편집본(범용① 8종)이 07-08 추가된
관점 축 '옹호·지지'·'반박·비판' 을 영구히 지워 verify_item 이 LLM 논조 판정을 드롭했다.

계약:
· 리스트 오버라이드는 병합 — 코드 기본값에만 있는 원소는 되살린다(순서: 오버라이드 뒤에 코드 순서로).
· 단, 툼스톤(_removed)에 기록된 '사용자가 의도적으로 뺀 값'은 되살리지 않는다.
· dict 오버라이드의 기존 의미론(있는 키만 갱신 · 키 삭제 없음)은 유지한다.
· 단건 키 편집(stamp_removals key=)은 **그 키만** 툼스톤 계산 — 파일에 남은 형제 키의
  구(stale) 스냅샷이 코드 신규 값을 '사용자 삭제'로 오기록하지 않는다(2026-08 감사).
"""
import copy
import json
import os
import tempfile
import unittest

from prism import dictionaries as D


def _restore_globals(snap):
    """전역 사전을 스냅샷으로 되돌린다(dict 는 참조 유지 · apply_profile 과 동일 규약)."""
    g = vars(D)
    for gk, base in snap.items():
        cur = g.get(gk)
        if isinstance(cur, dict) and isinstance(base, dict):
            cur.clear()
            cur.update(copy.deepcopy(base))
        else:
            g[gk] = copy.deepcopy(base)


def _list_paths(val, path=""):
    """코드 기본값에서 리스트가 놓인 경로 → (경로, 리스트) 목록. dict 안의 리스트도 포함."""
    if isinstance(val, list):
        return [(path, val)]
    if isinstance(val, dict):
        out = []
        for k, v in val.items():
            out += _list_paths(v, f"{path}.{k}" if path else str(k))
        return out
    return []


def _get_path(val, path):
    if not path:
        return val
    for part in path.split("."):
        val = val[part]
    return val


class _DictStateCase(unittest.TestCase):
    """전역 사전을 만지는 테스트 공통: 원본 스냅샷 복원(병렬 테스트 오염 방지)."""

    def setUp(self):
        self._snap = {gk: copy.deepcopy(getattr(D, gk, None)) for gk in D._PROFILE_KEYMAP.values()}
        self._base_snapshot = D._BASE_SNAPSHOT
        D._BASE_SNAPSHOT = None                       # 이 테스트 기준의 '코드 기본값' 확정

        def _cleanup():
            _restore_globals(self._snap)
            D._BASE_SNAPSHOT = self._base_snapshot
        self.addCleanup(_cleanup)


class TestPerspectiveAxisSurvivesOverride(_DictStateCase):
    """실증 케이스: 07-06 편집본(8종)이 07-08 추가된 관점 축 2종을 지우면 안 된다."""

    LEGACY_8 = ["속보·사건 추적", "심층 분석", "팬덤·화제성", "실용 정보",
                "감성·공감", "오락·유머", "의견·논쟁", "학술·전문"]

    def test_perspective_intents_restored(self):
        code_default = list(D.INTENT_CATEGORIES_UNIVERSAL)
        for v in ("옹호·지지", "반박·비판"):
            self.assertIn(v, code_default, "코드 기본값 전제(07-08 추가 관점 축)")

        D.apply_profile({"intent_universal": list(self.LEGACY_8)})   # 구형 오버라이드(삭제 기록 없음)

        for v in ("옹호·지지", "반박·비판"):
            self.assertIn(v, D.INTENT_CATEGORIES_UNIVERSAL, f"{v} 가 오버라이드에 지워졌다")
        # 순서 규칙: 오버라이드 순서(=코드 순서 앞 8종) + 코드에만 있는 신규를 코드 순서로 뒤에
        self.assertEqual(D.INTENT_CATEGORIES_UNIVERSAL, code_default)

    def test_verify_item_keeps_perspective_after_override(self):
        """결함의 실제 피해면: 오버라이드 후에도 verify_item 이 논조 판정을 드롭하지 않는다."""
        from prism import verify as V
        from prism.schema import Content, ItemMeta

        D.apply_profile({"intent_universal": list(self.LEGACY_8)})
        content = Content(displayServiceName="뉴스", title="t", body="b")
        im = ItemMeta(intent=["반박·비판", "심층 분석"])
        V.verify_item(im, content)
        self.assertEqual(im.intent, ["반박·비판", "심층 분석"])

    def test_report_flags_restored_values(self):
        rep = D.apply_profile({"intent_universal": list(self.LEGACY_8)})
        self.assertEqual(rep["restored"].get("intent_universal"), ["옹호·지지", "반박·비판"])
        self.assertFalse(rep["dropped"])


class TestNoCodeDefaultLost(_DictStateCase):
    """_PROFILE_KEYMAP 의 리스트 대상 전반(중첩 리스트 포함): 구형 오버라이드로 원소 유실 없음."""

    def test_every_list_target_survives_stale_override(self):
        code = {pk: copy.deepcopy(getattr(D, gk)) for pk, gk in D._PROFILE_KEYMAP.items()}
        paths = {pk: _list_paths(v) for pk, v in code.items()}
        self.assertTrue(any(paths.values()), "리스트 대상이 하나도 없으면 테스트 전제가 깨진 것")

        # 저장 시점 스냅샷 흉내: 모든 리스트에서 마지막 원소를 뺀 구형 오버라이드
        stale = copy.deepcopy(code)
        trimmed = {}
        for pk, ps in paths.items():
            for path, lst in ps:
                if len(lst) < 2:
                    continue
                target = _get_path(stale[pk], path)
                trimmed[(pk, path)] = target.pop()
        self.assertTrue(trimmed, "잘라낸 리스트가 없으면 회귀가 검증되지 않는다")

        D.apply_profile(stale)

        for pk, gk in D._PROFILE_KEYMAP.items():
            now = getattr(D, gk)
            for path, lst in paths[pk]:
                cur = _get_path(now, path)
                for v in lst:
                    self.assertIn(v, cur, f"{gk}.{path} 에서 코드 기본값 '{v}' 유실")

    def test_dict_merge_semantics_kept(self):
        """dict 오버라이드는 기존대로 — 오버라이드에 없는 키는 그대로 남는다."""
        before = dict(D.QUALITY_METAS)
        D.apply_profile({"quality_metas": {"ad": "바뀐 정의"}})
        self.assertEqual(D.QUALITY_METAS["ad"], "바뀐 정의")
        for k, v in before.items():
            if k != "ad":
                self.assertEqual(D.QUALITY_METAS[k], v, f"{k} 가 사라졌다")

    def test_nested_list_in_dict_target_merged(self):
        """dict 안의 리스트(서비스별 인텐트 등)도 같은 병합 규칙."""
        base_news = list(D.INTENT_CATEGORIES_BY_SERVICE["뉴스"])
        D.apply_profile({"intent_by_service": {"뉴스": base_news[:2] + ["회사 전용"]}})
        merged = D.INTENT_CATEGORIES_BY_SERVICE["뉴스"]
        self.assertEqual(merged[:3], base_news[:2] + ["회사 전용"])
        for v in base_news:
            self.assertIn(v, merged)
        self.assertEqual(D.INTENT_CATEGORIES_BY_SERVICE["연예"],
                         self._snap["INTENT_CATEGORIES_BY_SERVICE"]["연예"])


class TestIntentionalRemovalHonored(_DictStateCase):
    """(a)사용자가 뺀 값 과 (b)코드에 나중에 추가된 값 의 구분: 툼스톤(_removed)."""

    def test_stamped_removal_is_not_restored(self):
        base = list(D.INTENT_CATEGORIES_UNIVERSAL)
        kept = [v for v in base if v != "오락·유머"]
        ov = D.stamp_removals({"intent_universal": kept}, "intent_universal")
        self.assertEqual(ov[D.REMOVED_KEY]["intent_universal"], ["오락·유머"])

        D.apply_profile(ov)
        self.assertEqual(D.INTENT_CATEGORIES_UNIVERSAL, kept)   # 삭제 유지 · 되살리지 않음

    def test_later_code_addition_restored_while_removal_kept(self):
        """편집 시점(T) 이후 코드에 값이 추가되면(T+1) 복원 · 그때도 사용자 삭제는 유지."""
        base_t = ["A", "B", "C"]
        D.INTENT_CATEGORIES_UNIVERSAL = list(base_t)
        ov = D.stamp_removals({"intent_universal": ["A", "C"]}, "intent_universal")   # B 를 뺀다

        # 코드가 나중에 D 를 추가한 상태에서 재기동
        D._BASE_SNAPSHOT = None
        D.INTENT_CATEGORIES_UNIVERSAL = ["A", "B", "C", "D"]
        rep = D.apply_profile(ov)

        self.assertEqual(D.INTENT_CATEGORIES_UNIVERSAL, ["A", "C", "D"])
        self.assertEqual(rep["restored"]["intent_universal"], ["D"])
        self.assertEqual(rep["dropped"]["intent_universal"], ["B"])

    def test_re_added_value_clears_tombstone(self):
        base = list(D.INTENT_CATEGORIES_UNIVERSAL)
        ov = D.stamp_removals({"intent_universal": [v for v in base if v != "오락·유머"]},
                              "intent_universal")
        self.assertIn(D.REMOVED_KEY, ov)
        ov["intent_universal"] = list(base)
        D.stamp_removals(ov, "intent_universal")
        self.assertNotIn(D.REMOVED_KEY, ov)

    def test_remove_all_is_full_replacement(self):
        """회사 프로파일이 체계를 통째로 갈아끼울 때: _removed 값 '*' = 병합 없이 교체."""
        rep = D.apply_profile({"intent_universal": ["전용 A", "전용 B"],
                               D.REMOVED_KEY: {"intent_universal": D.REMOVE_ALL}})
        self.assertEqual(D.INTENT_CATEGORIES_UNIVERSAL, ["전용 A", "전용 B"])
        self.assertFalse(rep["restored"])

    def test_restore_base_still_works(self):
        base = list(D.INTENT_CATEGORIES_UNIVERSAL)
        D.apply_profile({"intent_universal": ["전용"],
                         D.REMOVED_KEY: {"intent_universal": D.REMOVE_ALL}})
        self.assertEqual(D.INTENT_CATEGORIES_UNIVERSAL, ["전용"])
        D.restore_base()
        self.assertEqual(D.INTENT_CATEGORIES_UNIVERSAL, base)

    def test_apply_is_idempotent(self):
        ov = D.stamp_removals(
            {"intent_universal": [v for v in D.INTENT_CATEGORIES_UNIVERSAL if v != "오락·유머"]},
            "intent_universal")
        D.apply_profile(ov)
        once = list(D.INTENT_CATEGORIES_UNIVERSAL)
        D.apply_profile(ov)
        D.apply_profile(ov)
        self.assertEqual(D.INTENT_CATEGORIES_UNIVERSAL, once)


class TestEditRoundTrip(_DictStateCase):
    """어드민 편집 → 저장 → 재기동(load_dict_overrides) 왕복: 삭제는 유지 · 신규 코드값은 유입."""

    def setUp(self):
        super().setUp()
        import prism.serve as S
        self.S = S
        self._orig_path = S._DICT_OVERRIDES_PATH
        S._DICT_OVERRIDES_PATH = os.path.join(tempfile.mkdtemp(), "ov.json")
        self.addCleanup(lambda: setattr(S, "_DICT_OVERRIDES_PATH", self._orig_path))

    def test_edit_then_restart_picks_up_new_code_values(self):
        D.INTENT_CATEGORIES_UNIVERSAL = ["A", "B", "C"]
        r = self.S.edit_dict({"target": "intent_universal", "value": ["A", "C"]})
        self.assertTrue(r.get("saved"))
        self.assertEqual(D.INTENT_CATEGORIES_UNIVERSAL, ["A", "C"])     # 삭제 즉시 반영

        with open(self.S._DICT_OVERRIDES_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved[D.REMOVED_KEY]["intent_universal"], ["B"])

        # 재기동: 코드가 D 를 추가한 새 버전
        D._BASE_SNAPSHOT = None
        D.INTENT_CATEGORIES_UNIVERSAL = ["A", "B", "C", "D"]
        self.S.load_dict_overrides()
        self.assertEqual(D.INTENT_CATEGORIES_UNIVERSAL, ["A", "C", "D"])

    def test_legacy_override_file_without_tombstone_restores(self):
        """툼스톤 없는 구형 파일(2026-07-06 편집본)로 기동 → 코드 신규 값 복원."""
        with open(self.S._DICT_OVERRIDES_PATH, "w", encoding="utf-8") as f:
            json.dump({"intent_universal": TestPerspectiveAxisSurvivesOverride.LEGACY_8},
                      f, ensure_ascii=False)
        code_default = list(D.INTENT_CATEGORIES_UNIVERSAL)
        self.S.load_dict_overrides()
        self.assertEqual(D.INTENT_CATEGORIES_UNIVERSAL, code_default)

    def test_dict_data_exposes_merged_intents(self):
        """운영 확인 경로: GET /dict 의 intentUniversal 이 코드 기본값 종수를 유지."""
        n = len(D.INTENT_CATEGORIES_UNIVERSAL)
        D.apply_profile({"intent_universal": TestPerspectiveAxisSurvivesOverride.LEGACY_8})
        self.assertEqual(len(self.S.dict_data()["intentUniversal"]), n)

    def test_broken_override_file_is_reported_not_swallowed(self):
        with open(self.S._DICT_OVERRIDES_PATH, "w", encoding="utf-8") as f:
            f.write("{ 깨진 JSON")
        base = list(D.INTENT_CATEGORIES_UNIVERSAL)
        self.S.load_dict_overrides()                     # 예외 전파 없이 기본 사전 유지
        self.assertEqual(D.INTENT_CATEGORIES_UNIVERSAL, base)


class TestSiblingKeyEditNoFalseTombstone(_DictStateCase):
    """단건 키 편집(형제 키 시나리오) 회귀: 2026-08 감사 idx0.

    dict 타깃을 한 번 편집하면 전체 복사본이 파일에 시딩된다. 이후 코드에 값이 추가되고
    아무 키나 재편집하면, 구현이 target 전체를 코드 기본값과 비교할 경우 형제 키의
    stale 스냅샷이 '사용자 삭제'(툼스톤)로 오기록되어 코드 신규 값이 영구 소실된다.
    수정 계약: stamp_removals(key=) 는 편집된 키만 비교하고 형제 키 툼스톤은 유지한다."""

    DICT_TARGETS = ["intent_by_service", "tier2", "quality_metas",
                    "domain_groups", "category_iab_map", "intake_policy"]

    @staticmethod
    def _stale_snapshot(base):
        """시딩 시점 스냅샷 흉내: 리스트 값을 가진 첫 키에서 마지막 원소를 뺀다
        (= 그 원소가 시딩 **이후** 코드에 추가됐다고 가정). 반환 (stale, victim_key, new_val)."""
        stale = copy.deepcopy(base)
        for k, v in stale.items():
            if isinstance(v, list) and len(v) >= 2:
                return stale, k, v.pop()
        return stale, None, None                       # 리스트 값이 없는 타깃(str·dict 값)

    @staticmethod
    def _edited_value(v):
        """값을 지우지 않는 편집(추가·수정만) — 툼스톤이 생길 이유가 없는 편집."""
        if isinstance(v, list):
            return list(v) + ["형제키 시나리오 신설값"]
        if isinstance(v, dict):
            d = dict(v)
            d["memo"] = "수정"
            return d
        return "수정된 값"

    def _fresh(self):
        _restore_globals(self._snap)
        D._BASE_SNAPSHOT = None

    def test_keyed_edit_never_tombstones_stale_siblings_all_dict_targets(self):
        """dict 타깃 전체: 형제 키가 stale 이어도 단건 편집이 툼스톤을 만들지 않고
        병합 후 코드 신규 값이 생존한다."""
        for pk in self.DICT_TARGETS:
            with self.subTest(target=pk):
                self._fresh()
                gk = D._PROFILE_KEYMAP[pk]
                base = copy.deepcopy(getattr(D, gk))
                stale, victim, new_val = self._stale_snapshot(base)
                k_edit = next(k for k in stale if k != victim)
                ov = {pk: stale}
                ov[pk][k_edit] = self._edited_value(base[k_edit])
                D.stamp_removals(ov, pk, key=k_edit)

                tomb = (ov.get(D.REMOVED_KEY) or {}).get(pk) or {}
                if victim is not None:
                    self.assertNotIn(victim, tomb,
                                     f"{pk}: 형제 키 stale 스냅샷이 사용자 삭제로 오기록")
                self.assertFalse(tomb, f"{pk}: 값을 지우지 않은 편집에 툼스톤이 생겼다")

                D.apply_profile(ov)
                if victim is not None:
                    self.assertIn(new_val, getattr(D, gk)[victim],
                                  f"{pk}: 코드 신규 값이 편집 저장으로 소실")

    def test_keyed_deletion_tombstones_only_edited_key(self):
        """진짜 삭제는 여전히 기록된다 — 단, 편집한 키에만."""
        self._fresh()
        base = copy.deepcopy(D.INTENT_CATEGORIES_BY_SERVICE)
        stale, victim, new_val = self._stale_snapshot(base)
        k_edit = next(k for k in stale if k != victim and len(stale[k]) >= 2)
        removed = stale[k_edit][-1]
        ov = {"intent_by_service": stale}
        ov["intent_by_service"][k_edit] = [v for v in base[k_edit] if v != removed]
        D.stamp_removals(ov, "intent_by_service", key=k_edit)

        tomb = ov[D.REMOVED_KEY]["intent_by_service"]
        self.assertEqual(tomb, {k_edit: [removed]})    # victim(형제 키) 미포함

        D.apply_profile(ov)
        self.assertNotIn(removed, D.INTENT_CATEGORIES_BY_SERVICE[k_edit])   # 삭제 유지
        self.assertIn(new_val, D.INTENT_CATEGORIES_BY_SERVICE[victim])      # 신규 값 생존

    def test_sibling_tombstone_preserved_on_keyed_edit(self):
        """과거 다른 키를 편집하며 남긴 툼스톤은 이번 편집이 걷어내지 않는다."""
        self._fresh()
        base = copy.deepcopy(D.INTENT_CATEGORIES_BY_SERVICE)
        keys = [k for k in base if len(base[k]) >= 2]
        k_prev, k_edit = keys[0], keys[1]
        gone = base[k_prev][-1]
        ov = {"intent_by_service": copy.deepcopy(base),
              D.REMOVED_KEY: {"intent_by_service": {k_prev: [gone]}}}
        ov["intent_by_service"][k_prev] = [v for v in base[k_prev] if v != gone]
        ov["intent_by_service"][k_edit] = list(base[k_edit]) + ["신설"]
        D.stamp_removals(ov, "intent_by_service", key=k_edit)

        self.assertEqual(ov[D.REMOVED_KEY]["intent_by_service"].get(k_prev), [gone])
        D.apply_profile(ov)
        self.assertNotIn(gone, D.INTENT_CATEGORIES_BY_SERVICE[k_prev])

    def test_keyed_readd_clears_only_that_key_tombstone(self):
        """편집한 키의 값을 되살리면 그 키의 툼스톤만 걷어낸다."""
        self._fresh()
        base = copy.deepcopy(D.INTENT_CATEGORIES_BY_SERVICE)
        keys = [k for k in base if len(base[k]) >= 2]
        k_prev, k_edit = keys[0], keys[1]
        ov = {"intent_by_service": copy.deepcopy(base),
              D.REMOVED_KEY: {"intent_by_service": {k_prev: [base[k_prev][-1]],
                                                    k_edit: [base[k_edit][-1]]}}}
        ov["intent_by_service"][k_edit] = list(base[k_edit])       # 전부 되살림
        D.stamp_removals(ov, "intent_by_service", key=k_edit)
        tomb = ov[D.REMOVED_KEY]["intent_by_service"]
        self.assertNotIn(k_edit, tomb)
        self.assertIn(k_prev, tomb)

    def test_remove_all_untouched_by_keyed_edit(self):
        """완전 교체(*) 프로파일은 단건 키 편집이 툼스톤을 건드리지 않는다."""
        self._fresh()
        ov = {"intent_by_service": {"뉴스": ["전용 A"]},
              D.REMOVED_KEY: {"intent_by_service": D.REMOVE_ALL}}
        D.stamp_removals(ov, "intent_by_service", key="뉴스")
        self.assertEqual(ov[D.REMOVED_KEY]["intent_by_service"], D.REMOVE_ALL)

    def test_whole_value_edit_still_full_stamps_dict_target(self):
        """key 없는 전체 값 저장은 기존대로 target 전체를 비교한다(사용자가 전체를 제출)."""
        self._fresh()
        base = copy.deepcopy(D.INTENT_CATEGORIES_BY_SERVICE)
        keys = [k for k in base if len(base[k]) >= 2]
        k1 = keys[0]
        whole = copy.deepcopy(base)
        gone = whole[k1].pop()
        ov = D.stamp_removals({"intent_by_service": whole}, "intent_by_service")
        self.assertEqual(ov[D.REMOVED_KEY]["intent_by_service"], {k1: [gone]})


class TestSiblingKeyEditRoundTrip(_DictStateCase):
    """감사 idx0 원 시나리오 재현(파일 왕복): stale 뉴스 리스트 파일 → 기동 병합 복원 →
    스포츠 키 단건 편집 저장 → 뉴스 신규 값이 삭제로 기록되지 않고 재기동 후에도 생존."""

    BASE = {"뉴스": ["속보", "심층", "트렌드·시장 분석"],       # 마지막 값 = 코드 신규 추가분
            "스포츠": ["경기 결과", "구단 소식"]}

    def setUp(self):
        super().setUp()
        import prism.serve as S
        self.S = S
        self._orig_path = S._DICT_OVERRIDES_PATH
        S._DICT_OVERRIDES_PATH = os.path.join(tempfile.mkdtemp(), "ov.json")
        self.addCleanup(lambda: setattr(S, "_DICT_OVERRIDES_PATH", self._orig_path))
        self._set_code_base()

    def _set_code_base(self):
        """합성 코드 기본값 설치(참조 유지 · in-place). 재기동 흉내에도 재사용."""
        D.INTENT_CATEGORIES_BY_SERVICE.clear()
        D.INTENT_CATEGORIES_BY_SERVICE.update(copy.deepcopy(self.BASE))
        D._BASE_SNAPSHOT = None

    def test_audit_scenario_sports_edit_keeps_news_new_value(self):
        # 시딩 시점(신규 값 추가 전) 스냅샷이 파일에 남아 있다
        stale = {"intent_by_service": {"뉴스": ["속보", "심층"],
                                       "스포츠": ["경기 결과", "구단 소식"]}}
        with open(self.S._DICT_OVERRIDES_PATH, "w", encoding="utf-8") as f:
            json.dump(stale, f, ensure_ascii=False)

        self.S.load_dict_overrides()                   # 기동 병합: 신규 값 복원(정상)
        self.assertIn("트렌드·시장 분석", D.INTENT_CATEGORIES_BY_SERVICE["뉴스"])

        # 관리자가 '스포츠' 키 하나만 편집·저장(UI 는 target+key+value 단건 전송)
        r = self.S.edit_dict({"target": "intent_by_service", "key": "스포츠",
                              "value": ["경기 결과", "구단 소식", "이적 시장"]})
        self.assertTrue(r.get("saved"))

        with open(self.S._DICT_OVERRIDES_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        tomb = (saved.get(D.REMOVED_KEY) or {}).get("intent_by_service") or {}
        self.assertNotIn("뉴스", tomb, "형제 키(뉴스)의 코드 신규 값이 사용자 삭제로 오기록")
        self.assertEqual(saved["intent_by_service"]["스포츠"],
                         ["경기 결과", "구단 소식", "이적 시장"])

        # 저장 직후에도, 재기동 후에도 뉴스 신규 값 생존
        self.assertIn("트렌드·시장 분석", D.INTENT_CATEGORIES_BY_SERVICE["뉴스"])
        self._set_code_base()                          # 재기동 흉내
        self.S.load_dict_overrides()
        self.assertIn("트렌드·시장 분석", D.INTENT_CATEGORIES_BY_SERVICE["뉴스"])
        self.assertEqual(D.INTENT_CATEGORIES_BY_SERVICE["스포츠"],
                         ["경기 결과", "구단 소식", "이적 시장"])

    def test_keyed_edit_deletion_survives_restart(self):
        """단건 편집으로 뺀 값은 툼스톤으로 기록되어 재기동에도 되살아나지 않는다."""
        r = self.S.edit_dict({"target": "intent_by_service", "key": "스포츠",
                              "value": ["경기 결과"]})       # '구단 소식' 삭제
        self.assertTrue(r.get("saved"))
        with open(self.S._DICT_OVERRIDES_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved[D.REMOVED_KEY]["intent_by_service"], {"스포츠": ["구단 소식"]})

        self._set_code_base()                          # 재기동 흉내
        self.S.load_dict_overrides()
        self.assertNotIn("구단 소식", D.INTENT_CATEGORIES_BY_SERVICE["스포츠"])
        self.assertIn("트렌드·시장 분석", D.INTENT_CATEGORIES_BY_SERVICE["뉴스"])


if __name__ == "__main__":
    unittest.main()
