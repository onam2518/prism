"""키 파일 영속 경로(볼륨 우선) 회귀 · '키 저장했는데 재시작 후 없음' 사고 방지.

운영 컨테이너 홈(~)은 임시 루트FS 라 배포·머신 재생성마다 초기화된다. 키 파일은
PRISM_CONFIG(/data/config.json)가 가리키는 볼륨 디렉토리에 저장해야 재배포에도 남는다.
_key_dir 해석 · 과거(홈) 저장분 읽기 폴백 · forget 양쪽 삭제 · 저장→재시작 왕복을 검증.

실행: python3 -m pytest tests/test_key_persist.py -q  (stdlib unittest · 의존성 0)
"""
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism import serve as SV


class _EnvMixin(unittest.TestCase):
    """환경변수 원복 헬퍼(테스트 간 오염 방지)."""

    def _set_env(self, key, val):
        orig = os.environ.get(key)
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val
        self.addCleanup(self._restore_env, key, orig)

    def _restore_env(self, key, orig):
        if orig is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = orig


class TestKeyDir(_EnvMixin):
    """_key_dir: PRISM_CONFIG 볼륨 디렉토리 우선 · 미설정/부재 시 홈."""

    def test_prefers_config_volume_dir(self):
        with tempfile.TemporaryDirectory() as td:
            self._set_env("PRISM_CONFIG", os.path.join(td, "config.json"))
            self.assertEqual(SV._key_dir(), td)

    def test_falls_back_to_home_when_unset(self):
        self._set_env("PRISM_CONFIG", None)
        self.assertEqual(SV._key_dir(), os.path.expanduser("~"))

    def test_falls_back_to_home_when_dir_missing(self):
        self._set_env("PRISM_CONFIG", "/nonexistent-prism-vol/config.json")
        self.assertEqual(SV._key_dir(), os.path.expanduser("~"))


class TestKeyFileHelpers(_EnvMixin):
    """_read_key_file/_key_persisted/_remove_key_file: 현행 경로 우선 · 과거(홈) 폴백."""

    def setUp(self):
        self.vol = tempfile.TemporaryDirectory()
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.vol.cleanup)
        self.addCleanup(self.home.cleanup)
        self._set_env("HOME", self.home.name)          # expanduser("~") 격리
        self.path = os.path.join(self.vol.name, ".prism_key")
        self.legacy = os.path.join(self.home.name, ".prism_key")

    def test_reads_current_path_first(self):
        SV._write_private(self.path, "up_current")
        SV._write_private(self.legacy, "up_old")
        self.assertEqual(SV._read_key_file(self.path), "up_current")

    def test_falls_back_to_legacy_home(self):
        SV._write_private(self.legacy, "up_old")
        self.assertEqual(SV._read_key_file(self.path), "up_old")
        self.assertTrue(SV._key_persisted(self.path))

    def test_missing_both_reads_empty(self):
        self.assertEqual(SV._read_key_file(self.path), "")
        self.assertFalse(SV._key_persisted(self.path))

    def test_remove_deletes_both_locations(self):
        SV._write_private(self.path, "k1")
        SV._write_private(self.legacy, "k2")
        SV._remove_key_file(self.path)
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(os.path.exists(self.legacy))


class TestPersistRoundTrip(_EnvMixin):
    """저장(persist) → 프로세스 재시작(env 소실) → load_persisted_key 복원 왕복."""

    def setUp(self):
        self.vol = tempfile.TemporaryDirectory()
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.vol.cleanup)
        self.addCleanup(self.home.cleanup)
        self._set_env("HOME", self.home.name)          # legacy(~) 접근 격리 · 실 키 파일 보호
        self._set_env("PRISM_BACKEND", "sqlite")       # supabase 키 게이트 우회(로컬 모드)
        self._set_env("UPSTAGE_API_KEY", None)
        self._set_env("PRISM_API_KEY", None)
        self._patch_attr(SV, "_KEY_PATH", os.path.join(self.vol.name, ".prism_key"))
        self._patch_attr(SV, "_ROUTER_KEY_PATHS",      # 실 라우터 키 파일 읽기 차단
                         {s: os.path.join(self.vol.name, ".prism_%s_key" % s)
                          for s in SV._ROUTER_KEY_PATHS})
        self._patch_attr(SV, "_seed_solar_defaults", lambda: None)   # 설정 파일 부작용 차단

    def _patch_attr(self, obj, name, val):
        orig = getattr(obj, name)
        setattr(obj, name, val)
        self.addCleanup(setattr, obj, name, orig)

    def test_save_restart_reload_forget(self):
        SV.apply_config({"api_key": "up_roundtrip", "persist": True})
        self.assertTrue(os.path.exists(SV._KEY_PATH))
        mode = stat.S_IMODE(os.stat(SV._KEY_PATH).st_mode)
        self.assertEqual(mode, 0o600)                  # 비밀 파일 권한

        os.environ.pop("UPSTAGE_API_KEY", None)        # 프로세스 재시작 상황 재현
        SV.load_persisted_key()
        self.assertEqual(os.environ.get("UPSTAGE_API_KEY"), "up_roundtrip")

        SV.apply_config({"forget": True})
        self.assertFalse(os.path.exists(SV._KEY_PATH))
        self.assertNotIn("UPSTAGE_API_KEY", os.environ)


if __name__ == "__main__":
    unittest.main()
