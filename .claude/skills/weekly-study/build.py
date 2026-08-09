#!/usr/bin/env python3
"""weekly-study · 덱 소스에 글꼴과 캐릭터를 심어 단일 HTML 로 만든다.

사용: python3 build.py <소스.html> <출력.html>

소스에 남겨 둔 자리표시자를 실제 데이터 URI 로 바꾼다.
  __FONTS__          → Gmarket Sans Bold + Pretendard Variable (@font-face 두 벌)
  __CHR_DDAKJI__     → 딱지(감독)      · 빨강
  __CHR_DAESIK__     → 대식(타자)      · 파랑
  __CHR_BOKSIL__     → 복실(포수)      · 노랑
  __CHR_YONGHEE__    → 용희(투수)      · 초록

글꼴은 저장소의 전체 파일을 쓴다. 주간회의 덱에서 뽑은 서브셋을 쓰면
덱에 처음 나오는 글자가 시스템 글꼴로 대체되어 자간과 굵기가 튄다.
"""
import base64
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
VENDOR = os.path.join(REPO, 'prism', 'vendor')
ASSETS = os.path.join(HERE, 'assets')

FONTS = [
    ('"Gmarket Sans"', '700', 'GmarketSansBold.woff2'),
    ('Pretendard', '100 900', 'PretendardVariable.woff2'),
]
CHARS = {
    '__CHR_DDAKJI__': 'ddakji.svg',
    '__CHR_DAESIK__': 'daesik.svg',
    '__CHR_BOKSIL__': 'boksil.svg',
    '__CHR_YONGHEE__': 'yonghee.svg',
}


def data_uri(path, mime):
    with open(path, 'rb') as f:
        return 'data:%s;base64,%s' % (mime, base64.b64encode(f.read()).decode())


def font_css():
    faces = []
    for family, weight, fname in FONTS:
        path = os.path.join(VENDOR, fname)
        if not os.path.exists(path):
            sys.exit('글꼴을 찾지 못했습니다: %s' % path)
        faces.append(
            '@font-face { font-family: %s; font-weight: %s; font-display: block;\n'
            '  src: url(%s) format("woff2"); }' % (family, weight, data_uri(path, 'font/woff2'))
        )
    return '\n'.join(faces)


def main():
    if len(sys.argv) != 3:
        sys.exit('사용: python3 build.py <소스.html> <출력.html>')
    src, out = sys.argv[1], sys.argv[2]
    with open(src, encoding='utf-8') as f:
        html = f.read()

    html = html.replace('__FONTS__', font_css())
    for token, fname in CHARS.items():
        html = html.replace(token, data_uri(os.path.join(ASSETS, fname), 'image/svg+xml'))

    left = [t for t in ('__FONTS__',) + tuple(CHARS) if t in html]
    if left:
        sys.exit('치환하지 못한 자리표시자: %s' % ', '.join(left))

    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)

    slides = html.count('<section class="slide')
    print('%s · %d장 · %.2fMB' % (out, slides, len(html.encode()) / 1024 / 1024))


if __name__ == '__main__':
    main()
