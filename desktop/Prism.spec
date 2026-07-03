# PyInstaller spec — Prism.app (macOS 번들)
# 빌드: python3.11 -m PyInstaller desktop/Prism.spec  (레포 루트에서 실행)
import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hidden = collect_submodules("prism") + collect_submodules("webview")

a = Analysis(
    ["app.py"],                          # desktop/ 기준
    pathex=[".."],                        # 레포 루트(= prism 패키지 import 경로)
    binaries=[],
    datas=[
        ("../prism/vendor", "prism/vendor"),   # force-graph.min.js 등 런타임 로드 데이터
        # config.json 은 gitignore — 있으면 번들(없어도 빌드 OK, 키는 ~/.prism_key 에서 로드)
        *( [("../config.json", ".")] if os.path.exists("../config.json") else [] ),
    ],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Prism",
    debug=False,
    strip=False,
    upx=False,
    console=False,                        # GUI 앱(터미널 창 없음)
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="Prism")

app = BUNDLE(
    coll,
    name="Prism.app",
    icon="icon/prism.icns",
    bundle_identifier="co.axz.prism.desktop",
    version="0.5.4",
    info_plist={
        "CFBundleName": "Prism",
        "CFBundleDisplayName": "Prism",
        "CFBundleShortVersionString": "0.5.4",
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.productivity",
        # Sparkle(T3) 연동 시 추가:
        # "SUFeedURL": "https://<appcast-호스팅-URL>/appcast.xml",
        # "SUPublicEDKey": "<sparkle generate_keys 의 공개키>",
    },
)
