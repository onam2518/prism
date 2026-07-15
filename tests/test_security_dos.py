"""자원 고갈(DoS) 회귀 테스트(2026-07-15 감사).

- do_POST 본문 상한: 초과 Content-Length 는 413(본문 read 전), 비수치는 400.
- _read_xlsx zip bomb 캡: 중앙 디렉터리 uncompressed 크기가 상한 초과면 read 거부.

실행: python3 -m pytest tests/test_security_dos.py -q
"""
import io
import os
import socket
import sys
import tempfile
import threading
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestBodyLimit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["PRISM_BACKEND"] = "sqlite"
        os.environ["PRISM_DB"] = os.path.join(tempfile.mkdtemp(), "dos.db")
        from http.server import ThreadingHTTPServer
        from prism import config as _cfg
        from prism import serve
        cls._orig_cfg = _cfg.DEFAULT_CONFIG_PATH
        _cfg.DEFAULT_CONFIG_PATH = os.path.join(tempfile.mkdtemp(), "config.json")
        cls._cfg = _cfg
        serve._STORE = None
        serve.Handler.server_mock = True
        cls.serve = serve
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.serve._STORE = None
        cls._cfg.DEFAULT_CONFIG_PATH = cls._orig_cfg

    def _raw(self, headers_extra, body=b""):
        """헤더를 직접 조립해 전송(잘못된/과대 Content-Length 재현) · 상태코드 반환."""
        s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        try:
            req = (f"POST /feedback HTTP/1.0\r\nHost: x\r\n{headers_extra}\r\n"
                   "Content-Type: application/json\r\n\r\n").encode() + body
            s.sendall(req)
            resp = b""
            while b"\r\n" not in resp:
                chunk = s.recv(4096)
                if not chunk:
                    break
                resp += chunk
            return int(resp.split()[1])
        finally:
            s.close()

    def test_oversized_content_length_413(self):
        # 실제 대용량 본문을 보내지 않아도 선언 크기만으로 413(read 전 차단)
        self.assertEqual(self._raw("Content-Length: 999999999"), 413)

    def test_non_numeric_content_length_400(self):
        self.assertEqual(self._raw("Content-Length: abc"), 400)

    def test_normal_request_ok(self):
        # 정상 소형 요청은 통과(레이트리밋/검증 로직으로 200)
        self.assertEqual(self._raw("Content-Length: 2", body=b"{}"), 200)


class TestXlsxZipBombCap(unittest.TestCase):
    def _xlsx(self, tmp):
        p = os.path.join(tmp, "b.xlsx")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("xl/sharedStrings.xml",
                       '<sst><si><t>' + "A" * 5000 + '</t></si></sst>')
            z.writestr("xl/worksheets/sheet1.xml",
                       '<worksheet><sheetData><row><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>')
        return p

    def test_member_over_cap_rejected(self):
        from prism import ingest as ING
        orig = ING._MAX_XLSX_MEMBER
        ING._MAX_XLSX_MEMBER = 100          # sharedStrings(~5KB) > 100 → 거부
        self.addCleanup(lambda: setattr(ING, "_MAX_XLSX_MEMBER", orig))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                ING._read_xlsx(self._xlsx(tmp))

    def test_normal_xlsx_ok(self):
        from prism import ingest as ING
        with tempfile.TemporaryDirectory() as tmp:
            rows, _ = ING._read_xlsx(self._xlsx(tmp))   # 상한 내(기본 48MB) → 정상 파싱
            self.assertTrue(rows)


if __name__ == "__main__":
    unittest.main()
