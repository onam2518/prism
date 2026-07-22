#!/usr/bin/env python3
"""prism-weekly · 빌드된 덱 HTML 의 __SHOT_*__ 플레이스홀더를 화면 캡처(jpeg data URI)로 치환.

사용: python3 embed_shots.py <빌드된.html> <출력.html> <shots 디렉토리>
규약: 토큰 __SHOT_LABRUN__ ↔ 파일 <shots>/labrun.jpg (토큰 소문자, .jpg 우선 · .png 폴백).
"""
import base64
import pathlib
import re
import sys


def main() -> None:
    if len(sys.argv) != 4:
        sys.exit("usage: embed_shots.py <in.html> <out.html> <shots_dir>")
    src, out, shots = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])
    html = pathlib.Path(src).read_text(encoding="utf-8")

    missing = []
    for token in sorted(set(re.findall(r"__SHOT_([A-Za-z0-9]+)__", html))):
        name = token.lower()
        jpg, png = shots / f"{name}.jpg", shots / f"{name}.png"
        if jpg.exists():
            uri = "data:image/jpeg;base64," + base64.b64encode(jpg.read_bytes()).decode()
        elif png.exists():
            uri = "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()
        else:
            missing.append(f"__SHOT_{token}__ -> {name}.jpg")
            continue
        html = html.replace(f"__SHOT_{token}__", uri)

    if missing:
        sys.exit("캡처 파일 없음: " + ", ".join(missing))
    pathlib.Path(out).write_text(html, encoding="utf-8")
    print(f"OK -> {out} ({pathlib.Path(out).stat().st_size // 1024} KB)", file=sys.stderr)


if __name__ == "__main__":
    main()
