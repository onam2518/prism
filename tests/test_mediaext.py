"""mediaext(미디어 텍스트화 트랙 T1·T2·T3) 계약 테스트.

T1(자막 파싱)은 룰 기반이라 네트워크 없이 검증. T2·T3 은 mock 경로로 검증
(키 유무와 무관하게 결정론적 mock 이 나와 UI 가 키 없이 동작함을 보장)."""
import unittest

from prism import mediaext as M


SRT = """1
00:00:01,000 --> 00:00:04,000
안녕하세요 여러분

2
00:00:04,500 --> 00:00:07,200
오늘은 날씨가 좋네요
"""

VTT = """WEBVTT

00:00:01.000 --> 00:00:03.000
<c>첫 자막</c>

00:00:03.500 --> 00:00:06.000
<00:00:03.500>두 번째 자막
"""


class TestSubtitleParsing(unittest.TestCase):
    def test_srt_basic(self):
        r = M.parse_subtitles(SRT)
        self.assertEqual(r["format"], "srt")
        self.assertEqual(r["cue_count"], 2)
        self.assertEqual(r["segments"][0]["start"], 1.0)
        self.assertEqual(r["segments"][0]["end"], 4.0)
        self.assertEqual(r["segments"][0]["text"], "안녕하세요 여러분")
        self.assertEqual(r["segments"][1]["start"], 4.5)
        self.assertIn("[00:01]", r["transcript"])

    def test_vtt_detected_and_tags_stripped(self):
        r = M.parse_subtitles(VTT)
        self.assertEqual(r["format"], "vtt")
        self.assertEqual(r["cue_count"], 2)
        # 인라인 태그(<c>, <00:00:03.500>)가 제거되어야 한다
        self.assertEqual(r["segments"][0]["text"], "첫 자막")
        self.assertEqual(r["segments"][1]["text"], "두 번째 자막")
        self.assertNotIn("<", r["transcript"])

    def test_explicit_format_override(self):
        r = M.parse_subtitles(SRT, fmt="srt")
        self.assertEqual(r["format"], "srt")

    def test_empty_and_garbage(self):
        self.assertEqual(M.parse_subtitles("")["cue_count"], 0)
        self.assertEqual(M.parse_subtitles("타임코드 없는\n그냥 텍스트")["cue_count"], 0)

    def test_blank_cue_skipped(self):
        # 텍스트 없는 큐는 세그먼트로 잡히지 않는다
        srt = "1\n00:00:01,000 --> 00:00:02,000\n\n"
        self.assertEqual(M.parse_subtitles(srt)["cue_count"], 0)


class TestAudioTrackMock(unittest.TestCase):
    def test_mock_deterministic(self):
        r = M.transcribe_track(b"\x00\x01", "audio/mp3", model="", mock=True)
        self.assertTrue(r.get("mock"))
        self.assertTrue(r["has_speech"])
        self.assertEqual(len(r["segments"]), 2)
        self.assertEqual(r["latency_ms"], 0)
        # 결정론: 두 번 호출해도 동일
        self.assertEqual(r, M.transcribe_track(b"\x00\x01", "audio/mp3", model="", mock=True))

    def test_no_model_falls_back_to_mock(self):
        # 모델 미지정이면 네트워크 없이 mock
        r = M.transcribe_track(b"\x00", "audio/wav", model="")
        self.assertTrue(r.get("mock"))

    def test_audio_format_mapping(self):
        self.assertEqual(M._audio_format("audio/mpeg"), "mp3")
        self.assertEqual(M._audio_format("audio/x-wav"), "wav")
        self.assertEqual(M._audio_format("audio/unknown"), "unknown")


class TestVisualTrackMock(unittest.TestCase):
    def test_mock_deterministic(self):
        frames = [{"bytes": b"\x00", "mime": "image/jpeg"}]
        r = M.visual_track(frames, model="", mock=True)
        self.assertTrue(r.get("mock"))
        self.assertEqual(r["frame_count"], 1)
        self.assertIn("description", r)
        self.assertEqual(r["latency_ms"], 0)

    def test_no_frames_falls_back_to_mock(self):
        r = M.visual_track([], model="anything", service="bizrouter")
        self.assertTrue(r.get("mock"))

    def test_cap_frames(self):
        frames = [{"bytes": b"x", "mime": "image/jpeg"} for _ in range(M.MAX_FRAMES + 3)]
        kept, dropped = M.cap_frames(frames)
        self.assertEqual(len(kept), M.MAX_FRAMES)
        self.assertEqual(dropped, 3)


if __name__ == "__main__":
    unittest.main()
