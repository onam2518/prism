"""모델 실행 없이 메타째 추가(외부 파이프라인 결과 검증용 · 엑셀 한정).

다른 파이프라인이 뽑아 둔 메타를 초안으로 올려 검수·정답셋 대조·모델별 비교로 검증한다.
메타는 신뢰 경계 입력이라 행마다 사전과 대조하고, 오류 행은 저장하지 않는다.

계약:
· ingest 별칭: 메타 열(리드문·엔티티·인텐트·콘텐츠 카테고리·등급·사유)이 자동 매핑되고,
  종전 헤더(부제·콘텐츠 그룹)의 매핑은 그대로다(하위 호환 · content_hash 불변)
· 구분자: 쉼표·세미콜론·파이프와 가운뎃점 · 단 사전 값 자체의 가운뎃점은 쪼개지 않는다
· 검증: 폐기 인텐트·미등록 인텐트·미등록 카테고리·등급 값·사유 ID·등급↔사유 정합성
· 저장 결과: run_pipeline 과 같은 모양(item_meta·quality_meta·trace.model=라벨)이라
  검수 화면이 그대로 읽고, trace.model 이 있어 STEP 2 가 재실행하지 않는다

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import json as _j
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HEAD = ("콘텐츠 그룹,제목,부제,본문,리드문,엔티티,인텐트,콘텐츠 카테고리,등급,사유\n")
ROW_OK = ("뉴스,임금 협상 결렬,조정 불성립,본문 하나,리드문 하나,"
          "삼성전자 · 노동위,속보·사건 추적,Business and Finance / Industries,G,\n")


def _csv(*rows) -> bytes:
    return (HEAD + "".join(rows)).encode("utf-8")


def _contents(csv_bytes):
    """CSV → ingest 매핑을 거친 콘텐츠 행(메타 열 포함)."""
    from prism import ingest as ING
    p = os.path.join(tempfile.mkdtemp(), "u.csv")
    with open(p, "wb") as f:
        f.write(csv_bytes)
    a = ING.assess(p)
    return ING.to_contents(p), a["mapping"]


class TestAliasAndSplit(unittest.TestCase):
    def test_meta_columns_map_and_legacy_headers_unchanged(self):
        rows, mapping = _contents(_csv(ROW_OK))
        for f in ("summary", "entities", "intent", "content_category", "finalGrade", "reasons"):
            self.assertIn(f, mapping, mapping)
        self.assertEqual(mapping["subtitle"], "부제")            # 하위 호환
        self.assertEqual(mapping["displayServiceName"], "콘텐츠 그룹")
        self.assertEqual(rows[0]["summary"], "리드문 하나")

    def test_terms_keep_dictionary_dots_but_split_separators(self):
        from prism import runops as RN
        ok = {"속보·사건 추적", "경기 결과·리뷰"}.__contains__
        self.assertEqual(RN._meta_terms("속보·사건 추적; 경기 결과·리뷰", ok),
                         ["속보·사건 추적", "경기 결과·리뷰"])
        self.assertEqual(RN._meta_terms("a|b, c", ok), ["a", "b", "c"])
        self.assertEqual(RN._meta_terms("삼성전자 · 노동위"), ["삼성전자", "노동위"])
        self.assertEqual(RN._meta_terms(""), [])


class MetaImportBase(unittest.TestCase):
    def _serve(self):
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        orig_mock = serve.Handler.server_mock
        serve.Handler.server_mock = True                  # 외부 부수효과(사전 보강) 차단
        self.addCleanup(lambda: setattr(serve.Handler, "server_mock", orig_mock))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        self.addCleanup(serve._agg_bump)
        p = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write(_j.dumps({}))
        from prism import config as C
        orig = C.DEFAULT_CONFIG_PATH
        C.DEFAULT_CONFIG_PATH = p
        self.addCleanup(lambda: setattr(C, "DEFAULT_CONFIG_PATH", orig))
        return serve

    def _import(self, csv_bytes, label="벨루가"):
        from prism import runops as RN
        rows, mapping = _contents(csv_bytes)
        return RN.import_meta_batch(rows, mapping, label=label)


class TestValidation(MetaImportBase):
    def test_retired_intent_is_row_error_with_rejudge_note(self):
        self._serve()
        r = self._import(_csv(ROW_OK.replace("속보·사건 추적", "음악 큐레이션")))
        self.assertEqual((r["saved"], r["error_count"]), (0, 1))
        self.assertEqual(r["errors"][0]["row"], 2)
        self.assertIn("재판정", r["errors"][0]["reason"])

    def test_unknown_intent_and_category_are_errors(self):
        self._serve()
        r = self._import(_csv(ROW_OK.replace("속보·사건 추적", "없는인텐트")))
        self.assertIn("공통 사전", r["errors"][0]["reason"])
        r2 = self._import(_csv(ROW_OK.replace("Business and Finance / Industries", "없는분류")))
        self.assertIn("사전 미등록", r2["errors"][0]["reason"])
        # Tier1 만 맞는 오타는 조용히 Tier1 으로 잘리지 않고 그 행이 오류가 된다
        r3 = self._import(_csv(ROW_OK.replace("Business and Finance / Industries",
                                              "Sports / 없는하위분류")))
        self.assertEqual((r3["saved"], r3["error_count"]), (0, 1))
        self.assertIn("하위 분류", r3["errors"][0]["reason"])

    def test_grade_and_reason_consistency(self):
        self._serve()
        bad_grade = self._import(_csv(ROW_OK.replace(",G,\n", ",Y,\n")))
        self.assertIn("등급", bad_grade["errors"][0]["reason"])
        reason_without_r = self._import(_csv(ROW_OK.replace(",G,\n", ",G,ad\n")))
        self.assertIn("사유가 있으면 등급은 R", reason_without_r["errors"][0]["reason"])
        r_without_reason = self._import(_csv(ROW_OK.replace(",G,\n", ",R,\n")))
        self.assertIn("사유가 1개 이상", r_without_reason["errors"][0]["reason"])
        unknown_reason = self._import(_csv(ROW_OK.replace(",G,\n", ",R,없는사유\n")))
        self.assertIn("품질 메타 ID", unknown_reason["errors"][0]["reason"])
        good_r = self._import(_csv(ROW_OK.replace(",G,\n", ",R,ad·clickbait\n")))
        self.assertEqual((good_r["saved"], good_r["error_count"]), (1, 0))

    def test_missing_title_or_body_is_error(self):
        self._serve()
        r = self._import(_csv(ROW_OK.replace(",본문 하나,", ",,")))
        self.assertIn("제목·본문", r["errors"][0]["reason"])


class TestSavedShape(MetaImportBase):
    def test_saved_row_matches_pipeline_shape_and_is_not_pending(self):
        serve = self._serve()
        r = self._import(_csv(ROW_OK), label="벨루가")
        self.assertEqual((r["saved"], r["error_count"], r["label"]), (1, 0, "벨루가"))
        row = serve.results_rows()[0]
        self.assertEqual(row["item_meta"]["summary"], "리드문 하나")
        self.assertEqual(row["item_meta"]["entities"], ["삼성전자", "노동위"])
        self.assertEqual(row["item_meta"]["intent"], ["속보·사건 추적"])
        self.assertEqual(row["item_meta"]["content_category"],
                         ["Business and Finance / Industries"])
        self.assertEqual(row["quality_meta"]["finalGrade"], "G")
        self.assertEqual(row["quality_meta"]["reasons"], [])
        self.assertEqual(row["trace"]["model"], "벨루가")
        self.assertEqual(row["trace"]["prompt_version"], "external")
        self.assertEqual(row["trace"]["cost_usd"], 0.0)
        self.assertEqual(row["content_ref"]["title"], "임금 협상 결렬")
        # 결과가 있는 콘텐츠는 '대기' 가 아니다 → STEP 2 모델 실행이 덮어쓰지 않는다
        self.assertFalse(serve._is_pending_row(row))


class TestReupload(MetaImportBase):
    def test_existing_result_is_not_overwritten_by_reupload(self):
        """재업로드가 검수자 교정을 되돌리지 않는다(엑셀 추출·add_contents 와 같은 정책)."""
        serve = self._serve()
        from prism.store import content_hash as ch
        self._import(_csv(ROW_OK))
        h = ch({"displayServiceName": "뉴스", "title": "임금 협상 결렬",
                "subtitle": "조정 불성립", "body": "본문 하나"})
        st = serve.get_store()
        st.update_item_meta(h, {"summary": "검수자 교정"})
        st.update_quality(h, "R", ["ad"])
        r = self._import(_csv(ROW_OK))
        self.assertEqual((r["saved"], r.get("skipped_done")), (0, 1))
        row = serve.results_rows()[0]
        self.assertEqual(row["item_meta"]["summary"], "검수자 교정")
        self.assertEqual(row["quality_meta"]["finalGrade"], "R")

    def test_duplicate_rows_in_one_file_save_once(self):
        self._serve()
        dup = ROW_OK.replace("리드문 하나", "리드문 둘")
        r = self._import(_csv(ROW_OK, dup))
        self.assertEqual((r["saved"], r.get("skipped_done")), (1, 1))


class TestMultipartSmoke(MetaImportBase):
    def test_run_batch_route_saves_valid_row_only(self):
        serve = self._serve()
        bad = ROW_OK.replace("임금 협상 결렬", "둘째 기사").replace("속보·사건 추적", "없는인텐트")
        csv = _csv(ROW_OK, bad)
        bnd = "metaboundary"
        body = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"with_meta\"\r\n\r\n1\r\n"
                f"--{bnd}\r\nContent-Disposition: form-data; name=\"model_label\"\r\n\r\n벨루가\r\n"
                f"--{bnd}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"u.csv\"\r\n"
                f"Content-Type: text/csv\r\n\r\n").encode("utf-8") + csv + f"\r\n--{bnd}--\r\n".encode()

        class _H:
            path = "/run-batch"
            server_mock = True
            headers = {"Content-Type": f"multipart/form-data; boundary={bnd}"}

            def _req_team(self):
                return None

            def _bearer_uid(self):
                return ""

            def _bearer_email(self):
                return ""

            def _send(self, *a):
                raise AssertionError("게이트에 막히면 안 된다(로컬 sqlite)")

        r = serve._p_run(_H(), body)
        self.assertEqual((r["saved"], r["error_count"]), (1, 1))
        self.assertEqual(r["errors"][0]["row"], 3)
        self.assertEqual([x["content_ref"]["title"] for x in serve.results_rows()],
                         ["임금 협상 결렬"])


class TestTemplate(unittest.TestCase):
    META = ["리드문", "엔티티", "인텐트", "콘텐츠 카테고리", "등급", "사유"]

    def test_meta_template_adds_columns_and_roundtrips(self):
        from prism import ingest as ING
        from prism import runops as RN
        base = RN.build_template_csv().decode("utf-8-sig").splitlines()[0]
        for col in self.META:
            self.assertNotIn(col, base)                   # 기본 서식은 그대로
        for name, blob in (("t.csv", RN.build_template_csv(True)),
                           ("t.xlsx", RN.build_template_xlsx(True))):
            p = os.path.join(tempfile.mkdtemp(), name)
            with open(p, "wb") as f:
                f.write(blob)
            headers, rows = ING.read_table(p)
            self.assertEqual(headers[-6:], self.META, name)
            self.assertEqual(len(rows), 2, name)
        # 예시 행의 메타 값은 실제 사전 값이라 그대로 올려도 오류가 나지 않는다
        p = os.path.join(tempfile.mkdtemp(), "t.csv")
        with open(p, "wb") as f:
            f.write(RN.build_template_csv(True))
        rows, mapping = ING.to_contents(p), ING.assess(p)["mapping"]
        from prism import serve
        from prism.store import Store
        serve._STORE = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        orig = serve.Handler.server_mock
        serve.Handler.server_mock = True
        self.addCleanup(lambda: setattr(serve.Handler, "server_mock", orig))
        self.addCleanup(lambda: setattr(serve, "_STORE", None))
        r = RN.import_meta_batch(rows, mapping, label="외부")
        self.assertEqual((r["saved"], r["error_count"]), (2, 0), r["errors"])


if __name__ == "__main__":
    unittest.main()
