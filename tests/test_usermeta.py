"""사용자 메타(실데이터 경로): 시간 신호 판별 + 소비 폭(breadth) 산출.

행동 로그 ts 를 실제로 사용해 8종 중 시간 축 페르소나(조사자·이중모드·전환기·
라이트 주말·스낵러 출퇴근)가 판별되는지, breadth 가 실제 카테고리 다양성으로
계산되는지 검증. 실행: python3 -m pytest tests/ -q (stdlib unittest · 의존성 0)
"""
import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import usermeta as UM


def _monday(year=2026, month=6):
    d = datetime.date(year, month, 1)
    while d.weekday() != 0:
        d += datetime.timedelta(days=1)
    return d


MON = _monday()  # 기준 월요일(평일·주말 계산의 앵커)


def _content(cat, entities=None, intent="분석·해설", title=None):
    return {"content_ref": {"title": title or f"{cat} 글", "displayServiceName": "svc"},
            "item_meta": {"intent": [intent], "content_category": [cat],
                          "entities": entities or []},
            "quality_meta": {"finalGrade": "G"}}


def _log(uid, idx, day_offset, hour, dwell=30, scroll=50, event="click", ts=True):
    row = {"user_id": uid, "content_id": str(idx), "event": event,
           "dwell_sec": dwell, "scroll_pct": scroll}
    if ts:
        d = MON + datetime.timedelta(days=day_offset)
        row["ts"] = f"{d.isoformat()}T{hour:02d}:00:00"
    return row


def _build(contents, logs, profiles=None):
    with tempfile.TemporaryDirectory() as d:
        rp, lp = os.path.join(d, "r.jsonl"), os.path.join(d, "l.jsonl")
        for path, rows in ((rp, contents), (lp, logs)):
            with open(path, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return UM.build_from_logs(rp, lp, profiles=profiles)


def _user(data, uid):
    return next(u for u in data["users"] if u["user_id"] == uid)


class TestBreadth(unittest.TestCase):
    """breadth = 실소비 카테고리 다양성 (구현 결함이던 len({*()}|set()) 대체)."""

    def test_breadth_is_category_diversity(self):
        one = [{"entity_categories": ["Sports"]} for _ in range(9)]
        many = [{"entity_categories": [c]} for c in
                ("Sports", "News", "Tech", "Food", "Travel", "Music", "Art")]
        self.assertAlmostEqual(UM._breadth(one), 1 / 6.0)
        self.assertEqual(UM._breadth(many), 1.0)
        self.assertEqual(UM._breadth([{"entity_categories": []}]), 0.0)


class TestTimeSignals(unittest.TestCase):
    """ts 기반 시간 신호로 시간 축 페르소나 4종 + 출퇴근 스낵러 판별."""

    def test_burst_maps_to_investigator(self):
        # 14일 관측 · 20건 중 14건이 연속 3일에 몰림 → 조사자
        contents = [_content("News") for _ in range(20)]
        logs = [_log("u", i, day_offset=3 + i % 3, hour=21, dwell=60, scroll=70)
                for i in range(14)]
        logs += [_log("u", 14 + i, day_offset=(0, 1, 8, 10, 12, 13)[i], hour=21,
                      dwell=60, scroll=70) for i in range(6)]
        u = _user(_build(contents, logs), "u")
        self.assertEqual(u["persona"], "조사자")
        self.assertIn("버스트", str(u["persona_derivation"]))

    def test_day_night_dual_mode(self):
        # 평일 낮 = 짧은 체류 훑기 · 밤 = 긴 체류 몰입 → 이중모드
        contents = [_content("News") for _ in range(20)]
        logs = []
        for i in range(10):
            logs.append(_log("u", i, day_offset=i % 5 + (i // 5) * 7, hour=13, dwell=6, scroll=30))
            logs.append(_log("u", 10 + i, day_offset=i % 5 + (i // 5) * 7, hour=22, dwell=70, scroll=80))
        u = _user(_build(contents, logs), "u")
        self.assertEqual(u["persona"], "이중모드")
        self.assertEqual(u["form"]["시간대"], "주·야 이중")

    def test_drift_maps_to_transition(self):
        # 전반 = Sports · 후반 = Finance 로 관심 전환 → 전환기
        contents = [_content("Sports") for _ in range(10)] + [_content("Finance") for _ in range(10)]
        logs = [_log("u", i, day_offset=(i * 2) % 28, hour=13, dwell=25, scroll=45)
                for i in range(20)]
        logs.sort(key=lambda r: r["ts"])  # 시간순 = 전반 Sports · 후반 Finance 가 되도록 재매핑
        for j, r in enumerate(logs):
            r["content_id"] = str(j)
        u = _user(_build(contents, logs), "u")
        self.assertEqual(u["persona"], "전환기")

    def test_weekend_only_maps_to_light(self):
        # 3주간 주말에만 · 얕은 소비 → 라이트
        contents = [_content("Ent") for _ in range(12)]
        logs = [_log("u", i, day_offset=5 + (i // 4) * 7 + (i % 2), hour=10, dwell=8, scroll=25)
                for i in range(12)]
        u = _user(_build(contents, logs), "u")
        self.assertEqual(u["persona"], "라이트")
        self.assertEqual(u["form"]["시간대"], "주말 집중")

    def test_commute_scanner_maps_to_snacker(self):
        # 평일 출퇴근 시간대 · 짧은 체류 훑기 → 스낵러
        contents = [_content(("News", "Ent", "Sports", "Tech")[i % 4]) for i in range(12)]
        logs = [_log("u", i, day_offset=i % 5 + (i // 5) * 7, hour=(8 if i % 2 else 18),
                     dwell=6, scroll=20) for i in range(12)]
        u = _user(_build(contents, logs), "u")
        self.assertEqual(u["persona"], "스낵러")
        self.assertEqual(u["form"]["시간대"], "출퇴근 집중")

    def test_single_entity_maps_to_fandom(self):
        # 시간 신호 없이도(수시) 단일 엔티티 집중 → 팬덤
        contents = [_content("Sports", entities=["손흥민"]) for _ in range(10)]
        logs = [_log("u", i, day_offset=(i * 3) % 14, hour=(9 + i * 2) % 24, dwell=50, scroll=70)
                for i in range(10)]
        u = _user(_build(contents, logs), "u")
        self.assertEqual(u["persona"], "팬덤")
        self.assertIn("손흥민", str(u["persona_derivation"]))


class TestNoTimestamp(unittest.TestCase):
    """ts 없는 로그 = 종전 동작(센트로이드 매칭) 유지 · 관측 부족 표기."""

    def test_without_ts_falls_back(self):
        contents = [_content("News") for _ in range(8)]
        logs = [_log("u", i, 0, 12, dwell=60, scroll=75, ts=False) for i in range(8)]
        u = _user(_build(contents, logs), "u")
        self.assertEqual(u["form"]["시간대"], "관측 부족")
        self.assertIn(u["persona"], [p["name"] for p in UM.PERSONAS])
        self.assertIn("근접 매칭", str(u["persona_derivation"]))
        # 대표 소비 콘텐츠(페르소나 생성의 아이템 근거)가 체류 가중 순으로 산출된다
        self.assertLessEqual(len(u["rep_contents"]), 5)
        self.assertIn("title", u["rep_contents"][0])
        self.assertIn("summary", u["rep_contents"][0])

    def test_under_five_views_is_light(self):
        contents = [_content("News") for _ in range(3)]
        logs = [_log("u", i, 0, 12, ts=False) for i in range(3)]
        u = _user(_build(contents, logs), "u")
        self.assertEqual(u["persona"], "라이트")
        self.assertTrue(u["persona_provisional"])
        self.assertEqual(u["persona_conf"], "저")


class TestConfidenceAndColdStart(unittest.TestCase):
    """U4 신뢰도·2순위 + U6 콜드스타트 프로필 프라이어."""

    def test_explicit_rule_is_high_confidence(self):
        contents = [_content("Sports", entities=["손흥민"]) for _ in range(10)]
        logs = [_log("u", i, day_offset=(i * 3) % 14, hour=(9 + i * 2) % 24, dwell=50, scroll=70)
                for i in range(10)]
        u = _user(_build(contents, logs), "u")
        self.assertEqual(u["persona_conf"], "고")
        self.assertFalse(u["persona_provisional"])

    def test_centroid_match_exposes_confidence_and_second(self):
        contents = [_content("News") for _ in range(8)]
        logs = [_log("u", i, 0, 12, dwell=60, scroll=75, ts=False) for i in range(8)]
        u = _user(_build(contents, logs), "u")
        names = [p["name"] for p in UM.PERSONAS]
        self.assertIn(u["persona_conf"], ("고", "중", "저"))
        self.assertIn(u["persona_second"], names)
        self.assertIn("신뢰도", str(u["persona_derivation"]))

    def test_cold_start_uses_declared_profile(self):
        # 로그 3건뿐이어도 선언 프로필(출퇴근 이용)이 있으면 잠정 스낵러
        contents = [_content("News") for _ in range(3)]
        logs = [_log("u", i, 0, 12, ts=False) for i in range(3)]
        prof = {"u": {"user_id": "u", "age_band": "30대", "interests": ["재테크"],
                      "day_part": "출퇴근", "note": ""}}
        u = _user(_build(contents, logs, profiles=prof), "u")
        self.assertEqual(u["persona"], "스낵러")
        self.assertTrue(u["persona_provisional"])
        self.assertEqual(u["persona_conf"], "저")
        self.assertIn("프로필 기반 잠정", str(u["persona_derivation"]))

    def test_cold_start_without_profile_stays_light(self):
        contents = [_content("News") for _ in range(3)]
        logs = [_log("u", i, 0, 12, ts=False) for i in range(3)]
        u = _user(_build(contents, logs, profiles={}), "u")
        self.assertEqual(u["persona"], "라이트")


class TestSimilarityAndAffinity(unittest.TestCase):
    """U5 사용자 유사도·배정 정합성 + U7 엔티티×페르소나 친화도."""

    def _three_users(self):
        # a·b = 같은 금융 소비(비슷) · c = 스포츠 단일 엔티티(다름 · 팬덤)
        contents = ([_content("Finance", entities=["연준"], intent="기획·심층") for _ in range(8)]
                    + [_content("Sports", entities=["손흥민"], intent="속보") for _ in range(10)])
        logs = [_log(uid, i, day_offset=(i * 2) % 10, hour=13, dwell=60, scroll=70)
                for uid in ("a", "b") for i in range(8)]
        logs += [_log("c", 8 + i, day_offset=(i * 3) % 14, hour=(9 + i * 2) % 24,
                      dwell=50, scroll=70) for i in range(10)]
        return _build(contents, logs)

    def test_similar_users_and_coherence(self):
        data = self._three_users()
        a = _user(data, "a")
        self.assertEqual(a["similar_users"][0][0], "b")
        self.assertGreater(a["similar_users"][0][1], 0.9)
        coh = data["aggregate"]["persona_coherence"]
        self.assertIsNotNone(coh["agree_rate"])
        self.assertGreaterEqual(coh["n"], 2)

    def test_entity_persona_matrix(self):
        data = self._three_users()
        ep = data["aggregate"]["entity_persona"]
        self.assertEqual(ep["personas"], [p["name"] for p in UM.PERSONAS])
        rows = {r[0]: r for r in ep["rows"]}
        self.assertIn("손흥민", rows)
        self.assertEqual(rows["손흥민"][1], "팬덤")   # c 는 단일 엔티티 집중 → 팬덤 귀속
        self.assertEqual(len(rows["손흥민"][2]), len(UM.PERSONAS))

    def test_empty_state_has_keys(self):
        with tempfile.TemporaryDirectory() as d:
            rp = os.path.join(d, "r.jsonl")
            open(rp, "w").close()
            data = UM.build_user_meta(rp)
        self.assertIn("persona_coherence", data["aggregate"])
        self.assertIn("entity_persona", data["aggregate"])


if __name__ == "__main__":
    unittest.main()
