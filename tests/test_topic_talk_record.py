"""말로 만들기 응답 계약: 대화 기록 · 해석 모델 · 걸린 이유 · 가까운 기존 토픽 (스펙 132112 · 4-38 · 4-44 · 4-45).

실행: python3 -m pytest tests/test_topic_talk_record.py -q  (stdlib unittest · 의존성 0)
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _row(title, entities=(), cats=("Business and Finance",)):
    return {"content_ref": {"title": title, "subtitle": "", "body": "본문-" + title,
                            "displayServiceName": "뉴스"},
            "item_meta": {"summary": "리드-" + title, "entities": list(entities),
                          "intent": ["분석·해설"], "content_category": list(cats)},
            "quality_meta": {"finalGrade": "G", "review": "", "reasons": []}}


class TestTalkRecordContract(unittest.TestCase):
    def setUp(self):
        import prism.serve as S
        from prism.store import Store
        self.S = S
        # 임베딩 실호출 차단: 키를 걷어 토큰 폴백 경로(결정적)로 유사도를 재운다
        self._saved = {k: os.environ.pop(k, None) for k in ("UPSTAGE_API_KEY", "PRISM_API_KEY")}
        self.addCleanup(lambda: [os.environ.__setitem__(k, v)
                                 for k, v in self._saved.items() if v is not None])
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_store, self._orig_mock = S._STORE, S.Handler.server_mock
        S.Handler.server_mock = True
        S._STORE = Store(os.path.join(self._tmp.name, "t.db"))
        rows = [_row("삼성 분석1", ["삼성전자"]), _row("삼성 분석2", ["삼성전자"])]
        S.store_save([({k: r["content_ref"][k] for k in
                        ("displayServiceName", "title", "subtitle", "body")}, r) for r in rows],
                     source="test")

    def tearDown(self):
        self.S._STORE, self.S.Handler.server_mock = self._orig_store, self._orig_mock
        self._tmp.cleanup()

    def test_turns_model_reason_and_similar(self):
        from prism.config import Config
        S = self.S
        td = S.topic_studio_action({"action": "save", "talk": True, "def": {
            "name": "경제 심층분석", "prompt": "경제 심층분석만 / 광고는 빼줘",
            "cats": ["Business and Finance"], "keywords": ["삼성전자"],
            "turns": [{"text": "경제 심층분석만", "model": "", "via": "llm",
                       "before": ["cats:Business and Finance", "keywords:삼성전자"],
                       "after": ["cats:Business and Finance"]},
                      {"text": "광고는 빼줘", "model": "solar-pro2", "via": "heuristic",
                       "before": ["cats:Business and Finance"], "after": ["cats:Business and Finance"]}]}})
        d = next(x for x in td["customDefs"] if x["name"] == "경제 심층분석")
        # 4-38: 턴별 문장 원문 · 해석 모델 · 해석 결과(before) · 칩 조정 뒤(after)
        self.assertEqual([t["text"] for t in d["turns"]], ["경제 심층분석만", "광고는 빼줘"])
        self.assertEqual(d["turns"][0]["after"], ["cats:Business and Finance"])
        self.assertEqual(d["turns"][0]["via"], "llm")
        # 4-45: 해석 모델 기록이 빈 문자열이 되지 않는다(마지막 턴 모델 · 없으면 시스템 기본)
        self.assertEqual(d["talk_model"], "solar-pro2")
        d2 = S.topic_studio_action({"action": "save", "talk": True, "def": {
            "name": "무모델", "cats": ["Business and Finance"]}})["customDefs"]
        self.assertEqual(next(x for x in d2 if x["name"] == "무모델")["talk_model"], Config.load().model)

        # 4-44: 저장 전(미리보기)에도 걸린 이유와 가까운 기존 토픽이 응답에 실린다
        out = S.topic_studio_action({"action": "preview", "similar": True, "def": {
            "name": "경제 심층분석 모음", "prompt": "경제 심층분석만 / 광고는 빼줘",
            "cats": ["Business and Finance"], "keywords": ["삼성전자"]}})
        pv = out["preview"]
        core = next(b for b in pv["bundles"] if b["kind"] == "core")
        self.assertTrue(core["samples"][0]["why"])
        self.assertEqual([s["name"] for s in pv["similar"]], ["경제 심층분석"])
        # 요청하지 않으면 유사도 계산을 돌리지 않는다(임베딩 호출 절약)
        self.assertNotIn("similar", S.topic_studio_action(
            {"action": "preview", "def": {"name": "x", "cats": ["Business and Finance"]}})["preview"])

    def test_turns_history_is_preserved_on_resave(self):
        """재저장(말로 다듬기·직접 손보기)해도 저장된 대화 기록은 사라지지 않고 새 턴만 뒤에 붙는다(4-38)."""
        S = self.S
        first = [{"text": "경제 심층분석만", "model": "solar-pro2", "via": "llm", "before": ["cats:Business and Finance"], "after": ["cats:Business and Finance"]}]
        td = S.topic_studio_action({"action": "save", "talk": True, "def": {
            "name": "기록 보존", "cats": ["Business and Finance"], "turns": first}})
        d = next(x for x in td["customDefs"] if x["name"] == "기록 보존")
        # 말로 다듬기: 클라이언트가 기존 턴을 이어 보내도 중복 없이 · 새 턴만 추가
        td2 = S.topic_studio_action({"action": "save", "talk": True, "def": {
            "id": d["id"], "name": "기록 보존", "cats": ["Business and Finance"],
            "turns": first + [{"text": "광고는 빼줘", "model": "solar-pro2", "via": "heuristic", "before": [], "after": []}]}})
        d2 = next(x for x in td2["customDefs"] if x["id"] == d["id"])
        self.assertEqual([t["text"] for t in d2["turns"]], ["경제 심층분석만", "광고는 빼줘"])
        # 직접 손보기(turns 없이 저장): 기록이 지워지지 않는다
        td3 = S.topic_studio_action({"action": "save", "def": {"id": d["id"], "name": "기록 보존", "cats": ["Business and Finance", "Sports"]}})
        d3 = next(x for x in td3["customDefs"] if x["id"] == d["id"])
        self.assertEqual([t["text"] for t in d3["turns"]], ["경제 심층분석만", "광고는 빼줘"])

    def test_turns_limits(self):
        """신뢰 경계: 기록은 20턴 · 문장 600자 · 칩 40개에서 잘리고, 목록이 아닌 turns 는 무시한다."""
        from prism.topicops import _sanitize_def
        d = _sanitize_def({"name": "상한", "cats": ["Business and Finance"],
                           "turns": [{"text": "t%d" % i, "model": "m", "via": "llm",
                                      "before": ["b%d" % j for j in range(60)], "after": []}
                                     for i in range(30)]
                           + [{"text": "가" * 900, "model": "M" * 200, "via": "V" * 50}]})
        self.assertEqual(len(d["turns"]), 20)
        self.assertEqual(d["turns"][0]["text"], "t11")              # 오래된 턴부터 버린다
        self.assertEqual(len(d["turns"][0]["before"]), 40)
        self.assertEqual([len(d["turns"][-1][k]) for k in ("text", "model", "via")], [600, 80, 20])
        self.assertEqual(_sanitize_def({"turns": [{"text": "  "}]})["turns"], [])
        for bad in ("abc", {"a": 1}, 5, None):                      # 목록이 아니면 500 이 아니라 빈 기록
            self.assertEqual(_sanitize_def({"turns": bad})["turns"], [])


if __name__ == "__main__":
    unittest.main()
