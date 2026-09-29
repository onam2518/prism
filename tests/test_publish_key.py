"""발행 키 계약(511607345 · 2026-09-22) 회귀.

item_unique_key 원문 보존 · 첫 하이픈 앞 프리픽스 인식 9종 · service_code·cp_type 원문 보존.
세 필드는 발행·라우팅 정보라 모델 입력(4호출 user 메시지·외부 배포 자리표)에 들어가지 않는다.

실행: python3 -m pytest tests/ -q  (stdlib unittest · 의존성 0)
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSourcePrefix(unittest.TestCase):
    def test_nine_recognized(self):
        from prism.schema import SOURCE_PREFIXES, source_prefix
        self.assertEqual(len(SOURCE_PREFIXES), 9)
        for p in ("tv", "hamny", "cafe", "tstory", "vod", "short", "video", "melon", "table"):
            self.assertIn(p, SOURCE_PREFIXES)
            self.assertEqual(source_prefix(p + "-123"), p)

    def test_real_key_examples(self):
        # 기획서 '원문 키 예시와 오류 처리' 표의 실제 키 4개
        from prism.schema import source_prefix
        for key, want in (("hamny-20260619181200992", "hamny"),
                          ("tstory-468099_555", "tstory"),
                          ("cafe-ok1221/9Zdf/2915857", "cafe"),
                          ("video-v_347055004", "video")):
            self.assertEqual(source_prefix(key), want, key)

    def test_error_cases(self):
        from prism.schema import SOURCE_PREFIXES, source_prefix
        # 빈값·null·하이픈 없음·앞부분 없음 = 추출 불가
        for bad in ("", None, "hamny", "-abc", 123):
            self.assertEqual(source_prefix(bad), "", repr(bad))
        # 뒷부분 없음은 관찰값 보존 · 미등록은 원문 그대로(unknown 대체 금지)
        self.assertEqual(source_prefix("hamny-"), "hamny")
        self.assertEqual(source_prefix("other-123"), "other")
        self.assertNotIn(source_prefix("other-123"), SOURCE_PREFIXES)
        # 대소문자 원문 보존 · 소문자화로 hamny 치환 금지
        self.assertEqual(source_prefix("HAMNY-123"), "HAMNY")
        self.assertNotIn(source_prefix("HAMNY-123"), SOURCE_PREFIXES)


class TestContentPreservesKeys(unittest.TestCase):
    RAW = {"displayServiceName": "뉴스", "title": "제목", "body": "본문",
           "item_unique_key": " cafe-ok1221/9Zdf/2915857 ", "service_code": "unknown",
           "cp_type": ""}

    def test_raw_preserved_not_normalized(self):
        from prism.schema import Content
        c = Content.from_dict(self.RAW)
        # trim·소문자화·재조합 금지 → 앞뒤 공백까지 원문 그대로
        self.assertEqual(c.item_unique_key, self.RAW["item_unique_key"])
        self.assertEqual(c.service_code, "unknown")    # unknown 도 원천 값
        self.assertEqual(c.cp_type, "")                # 빈 문자열과 부재를 가른다
        self.assertEqual(c.ref()["item_unique_key"], self.RAW["item_unique_key"])

    def test_absent_stays_none(self):
        from prism.schema import Content
        c = Content.from_dict({"displayServiceName": "뉴스", "title": "t", "body": "b"})
        self.assertIsNone(c.item_unique_key)
        self.assertIsNone(c.service_code)
        self.assertIsNone(c.cp_type)

    def test_hash_identity_unchanged(self):
        # 발행 키는 정체성(4필드 해시)에 안 들어간다 → 키만 붙어도 같은 콘텐츠
        from prism.store import content_hash
        no_key = {k: v for k, v in self.RAW.items()
                  if k not in ("item_unique_key", "service_code", "cp_type")}
        self.assertEqual(content_hash(self.RAW), content_hash(no_key))


class TestNotInModelInput(unittest.TestCase):
    KEY = "hamny-20260619181200992"

    def _content(self):
        from prism.schema import Content
        return Content.from_dict({"displayServiceName": "뉴스", "title": "제목", "body": "본문",
                                  "item_unique_key": self.KEY, "service_code": "contentview",
                                  "cp_type": "media"})

    def test_four_call_user_messages(self):
        from prism import prompts as P
        c = self._content()
        for call in ("summary", "entities", "intent", "content_category"):
            txt = P.call_user(call, c)
            for v in (self.KEY, "hamny", "contentview", "media"):
                self.assertNotIn(v, txt, f"{call} 에 {v} 가 실렸다")

    def test_merged_item_user(self):
        from prism import prompts as P
        txt = P.item_user(self._content())
        for v in (self.KEY, "contentview", "media"):
            self.assertNotIn(v, txt)

    def test_promptdist_template(self):
        from prism import promptdist as PD
        for call in ("summary", "entities", "intent", "content_category"):
            txt = PD.user_template(call)
            for v in ("item_unique_key", "source_prefix", "service_code", "cp_type"):
                self.assertNotIn(v, txt, f"{call} 자리표에 {v} 가 있다")


class TestIngestRoundtrip(unittest.TestCase):
    KEY = "cafe-ok1221/9Zdf/2915857"

    def test_alias_mapping_not_stolen_by_service_name(self):
        from prism import ingest as ING
        m = ING.infer_mapping(["제목", "본문", "item_unique_key", "service_code", "cp_type"])
        self.assertEqual(m.get("item_unique_key"), "item_unique_key")
        self.assertEqual(m.get("service_code"), "service_code")   # 'service' 포함 매칭에 안 뺏긴다
        self.assertEqual(m.get("cp_type"), "cp_type")
        self.assertNotIn("displayServiceName", m)

    def test_template_csv_roundtrips_key(self):
        from prism import ingest as ING
        from prism.runops import build_template_csv
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "t.csv")
            with open(path, "wb") as f:
                f.write(build_template_csv())
            rows = ING.to_contents(path)
        self.assertEqual(rows[0]["item_unique_key"], "hamny-20260619181200992")
        self.assertEqual(rows[0]["service_code"], "contentview")
        self.assertEqual(rows[0]["cp_type"], "media")

    def test_template_xlsx_roundtrips_key(self):
        from prism import ingest as ING
        from prism.runops import build_template_xlsx
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "t.xlsx")
            with open(path, "wb") as f:
                f.write(build_template_xlsx())
            rows = ING.to_contents(path)
        self.assertEqual(rows[0]["item_unique_key"], "hamny-20260619181200992")

    def test_sqlite_payload_and_csv_export(self):
        from prism import dashops as DO
        from prism.schema import Content
        from prism.store import Store
        content = {"displayServiceName": "카페", "title": "제목", "subtitle": "", "body": "본문",
                   "item_unique_key": self.KEY, "service_code": "unknown", "cp_type": "cafe"}
        with tempfile.TemporaryDirectory() as td:
            st = Store(os.path.join(td, "t.db"))
            out = {"content_ref": Content.from_dict(content).ref(),
                   "quality_meta": {"finalGrade": "G", "reasons": []},
                   "item_meta": {"summary": "s"}, "trace": {"model": "m"}}
            st.save_dedup([(content, out)], "run-1")
            rows = st.recent(10)
            self.assertEqual(rows[0]["content_ref"]["item_unique_key"], self.KEY)

            class FakeSV:
                results_rows = staticmethod(lambda limit=5000, team=None: rows)
            orig = DO._SV
            DO._SV = FakeSV
            try:
                csv_txt = DO.build_results_csv().decode("utf-8")
            finally:
                DO._SV = orig
        self.assertIn("item_unique_key,source_prefix,service_code,cp_type", csv_txt)
        self.assertIn(self.KEY, csv_txt)            # 키 원문 그대로
        self.assertIn('"cafe"', csv_txt)            # 계산된 프리픽스 열


if __name__ == "__main__":
    unittest.main()
