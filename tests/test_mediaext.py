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


class TestMergeAndContent(unittest.TestCase):
    def _subs(self):
        return M.parse_subtitles(SRT)   # cue_count=2

    def test_subtitle_wins_over_transcription(self):
        audio = {"has_speech": True, "transcript": "오디오 전사 원고"}
        m = M.merge_tracks(subtitles=self._subs(), audio=audio)
        self.assertEqual(m["spoken_source"], "subtitle")     # 자막 > 전사
        self.assertIn("안녕하세요", m["transcript"])
        self.assertNotIn("오디오 전사 원고", m["transcript"])
        self.assertEqual(m["sources"], ["subtitle"])

    def test_falls_back_to_transcription_when_no_subs(self):
        audio = {"has_speech": True, "transcript": "오디오 전사 원고"}
        m = M.merge_tracks(subtitles={"cue_count": 0}, audio=audio)
        self.assertEqual(m["spoken_source"], "transcription")
        self.assertIn("오디오 전사 원고", m["transcript"])

    def test_visual_annotated_alongside(self):
        visual = {"description": "야외 현장 영상", "on_screen_text": "속보",
                  "entities": ["A정당"]}
        m = M.merge_tracks(subtitles=self._subs(), visual=visual)
        self.assertIn("[비주얼] 야외 현장 영상", m["transcript"])
        self.assertIn("[화면 텍스트] 속보", m["transcript"])
        self.assertIn("visual", m["sources"])
        self.assertEqual(m["entities"], ["A정당"])

    def test_visual_only_no_speech(self):
        visual = {"description": "인물 사진"}
        m = M.merge_tracks(visual=visual)
        self.assertFalse(m["has_speech"])
        self.assertEqual(m["spoken_source"], "")

    def test_build_content_shape_and_lead(self):
        m = M.merge_tracks(subtitles=self._subs())
        c = M.build_content(m, caption="캡션문", description="기존 설명")
        self.assertEqual(set(c), {"displayServiceName", "title", "subtitle", "body"})
        self.assertEqual(c["displayServiceName"], "영상")
        # 본문에 설명·캡션·원고가 순서대로 결합
        self.assertLess(c["body"].index("기존 설명"), c["body"].index("캡션문"))
        self.assertIn("안녕하세요", c["body"])
        # 리드: 타임스탬프 프리픽스 제거 후 첫 문장
        self.assertNotIn("[00:01]", c["title"])
        self.assertIn("안녕하세요", c["title"])

    def test_build_content_empty_title_fallback(self):
        c = M.build_content({"transcript": "", "visual_desc": ""})
        self.assertEqual(c["title"], "영상 콘텐츠")


class TestNativeVideo(unittest.TestCase):
    def test_mock_shape(self):
        r = M.native_video_track(b"\x00\x01", "video/mp4", model="", mock=True)
        self.assertTrue(r.get("mock"))
        self.assertEqual(r["latency_ms"], 0)
        # merge_tracks 입력형: audio/visual 두 트랙
        self.assertIn("audio", r); self.assertIn("visual", r)
        self.assertTrue(r["audio"]["has_speech"])
        self.assertIn("transcript", r["audio"])
        self.assertIn("description", r["visual"])

    def test_no_model_falls_back_to_mock(self):
        r = M.native_video_track(b"\x00", "video/mp4", model="")
        self.assertTrue(r.get("mock"))

    def test_split_native_maps_fields(self):
        t = M._split_native({"has_speech": True, "transcript": "말", "description": "장면",
                             "on_screen_text": "자막", "entities": ["개체", ""]})
        self.assertEqual(t["audio"]["transcript"], "말")
        self.assertEqual(t["visual"]["description"], "장면")
        self.assertEqual(t["visual"]["entities"], ["개체"])   # falsy 제거

    def test_feeds_merge_tracks(self):
        r = M.native_video_track(b"\x00", model="", mock=True)
        m = M.merge_tracks(audio=r["audio"], visual=r["visual"])
        self.assertEqual(m["spoken_source"], "transcription")   # 자막 없으니 전사 채택
        self.assertIn("[비주얼]", m["transcript"])
        c = M.build_content(m, displayServiceName="영상")
        self.assertEqual(set(c), {"displayServiceName", "title", "subtitle", "body"})
        self.assertTrue(c["body"])


if __name__ == "__main__":
    unittest.main()
