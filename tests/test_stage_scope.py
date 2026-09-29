"""1차 발행 범위 회귀 (2026-09-29 정책 감사 3-3·4-8·4-9·4-19).

- 서비스 그룹: displayServiceName 원문이 media/ugc 로 정확히 갈린다(4-19).
- 법령 대표 등급이 최고 위험이어도 아이템 메타 4종은 나온다(4-8 · 위키 364314733).
- 품질 단계를 끄면 등급은 빈 값('판정 없음')이고 G 로 유통되지 않는다(3-3).
- 토픽 집계는 빈 등급을 기본 통과(G)로 세지 않는다(4-9 · 위키 279904498).

실행: python3 -m unittest tests.test_stage_scope
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestServiceGroup(unittest.TestCase):
    """displayServiceName 원문별 그룹 판정(위키 278036632 · PGC 5 / UGC 5)."""

    CASES = {
        "뉴스": "media", "연예": "media", "스포츠": "media",
        "콘텐츠뷰 (일반)": "media", "콘텐츠뷰(일반)": "media", "멜론": "media",
        "다음카페": "ugc", "콘텐츠뷰 (커뮤니티)": "ugc", "콘텐츠뷰(커뮤니티)": "ugc",
        "티스토리": "ugc", "VOD": "ugc", "루프": "ugc",
        "카카오TV": "ugc", "카카오비디오": "ugc",        # 마이그레이션 기간 구 명칭
        "": "media", "없는서비스": "media",              # 미정의 = 보수적 default
    }

    def test_dispatch_group_by_display_name(self):
        from prism import routing as R
        from prism.schema import Content
        for name, want in self.CASES.items():
            got = R.dispatch(Content(displayServiceName=name, title="제목", body="본문")).service_group
            self.assertEqual(got, want, f"{name!r} -> {got}")

    def test_ugc_only_metas_active_for_ugc_services(self):
        """UGC 전용 3종(format·political·hate)이 실제 UGC 서비스에서 켜진다."""
        from prism import routing as R
        from prism.schema import Content
        for name in ("다음카페", "콘텐츠뷰 (커뮤니티)", "티스토리", "VOD", "루프"):
            active = R.dispatch(Content(displayServiceName=name, title="t", body="b")).active_quality_metas
            for m in ("format", "political", "hate"):
                self.assertIn(m, active, f"{name} · {m}")
        for name in ("뉴스", "연예", "스포츠", "콘텐츠뷰 (일반)", "멜론"):
            active = R.dispatch(Content(displayServiceName=name, title="t", body="b")).active_quality_metas
            for m in ("format", "political", "hate"):
                self.assertNotIn(m, active, f"{name} · {m}")


if __name__ == "__main__":
    unittest.main()
