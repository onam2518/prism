"""건당 지연: 기본 방법론은 품질 호출과 아이템 4호출을 동시에 돌린다(2026-10-13)."""
import time
import unittest

from prism import harness as H
from prism.llm import LLMClient


class SlowMock(LLMClient):
    def complete_json(self, system, user, tag=""):
        time.sleep(0.3)
        return super().complete_json(system, user, tag)


class ExtractLatencyTest(unittest.TestCase):
    def test_quality_and_item_overlap(self):
        c = {"title": "서울 맛집 추천", "body": "을지로 노포 세 곳을 소개합니다.", "service": "news"}
        out = H.run(c, SlowMock(mock=True), H.Methodology())
        wall = out["trace"]["latency_ms"]["wall"]
        # 순차면 품질 0.3s + 아이템 0.3s 이상 = 600ms+ · 동시면 한 번 몫
        self.assertLess(wall, 550, wall)


if __name__ == "__main__":
    unittest.main()
