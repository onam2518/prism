# Prism 데스크탑 패키징 (macOS .app + .dmg)

목표: nunchi 수준의 배포 — 네이티브 창(pywebview) 앱 + 꾸민 DMG + (선택) 자동 업데이트.

> **현실**: 공증(notarization)은 유료 Apple Developer ID 필요. 없으면 Gatekeeper "확인되지 않은 개발자"
> 경고(첫 실행 우클릭→열기 1회). nunchi 가 `co.axz` 로 서명돼 있으니 **axz 조직 Developer ID** 를
> 확보하면 T2(서명·공증)·T3(안전한 자동업데이트)까지 nunchi 와 동일해진다.

## 구성
```
desktop/
  app.py            # pywebview 진입점 (Prism 서버 + 네이티브 창)
  Prism.spec        # PyInstaller → Prism.app
  make_icns.sh      # 1024 PNG → prism.icns
  make_dmg.sh       # Prism.app → 꾸민 DMG (create-dmg)
  icon/             # prism-1024.png(원본) → prism.icns
  dmg/background.png# DMG 배경(선택)
  sparkle/appcast.xml  # 자동업데이트 피드(T3)
```

## 사전 도구 (Python 3.11 사용 — 3.14 는 패키저 호환 이슈)
```bash
brew install create-dmg
python3.11 -m venv ~/.venvs/prism-pkg
source ~/.venvs/prism-pkg/bin/activate
pip install pywebview pyinstaller
```

## T1 — 동작하는 .app + 꾸민 DMG (미서명)
```bash
cd ~/Desktop/project/prism
# 1) 아이콘 (1024x1024 PNG 를 desktop/icon/prism-1024.png 로 두고)
( cd desktop && ./make_icns.sh )
# 2) .app 빌드
python3.11 -m PyInstaller desktop/Prism.spec --noconfirm --distpath dist --workpath build
# 3) 로컬 실행 확인
open dist/Prism.app
# 4) 꾸민 DMG
chmod +x desktop/make_dmg.sh && ./desktop/make_dmg.sh
# 결과: dist/Prism-0.1.0.dmg
```
미서명이라 첫 실행: **우클릭 → 열기** (또는 `xattr -dr com.apple.quarantine dist/Prism.app`).

## T2 — 서명 + 공증 (Apple Developer ID 필요)
```bash
# Developer ID Application 인증서(키체인) + Apple ID app-specific password 필요
codesign --deep --force --options runtime \
  --sign "Developer ID Application: AXZ ..." dist/Prism.app
xcrun notarytool submit dist/Prism-0.1.0.dmg \
  --apple-id <id> --team-id <TEAMID> --password <app-pw> --wait
xcrun stapler staple dist/Prism-0.1.0.dmg
```
완료되면 Gatekeeper 경고 없이 더블클릭 설치.

## T3 — Sparkle 자동 업데이트
1. Sparkle 키 생성: `./bin/generate_keys` → `SUPublicEDKey` 를 `Prism.spec` info_plist 에 기입.
2. `Prism.spec` info_plist 에 `SUFeedURL`(appcast 호스팅 URL) 추가 후 재빌드.
3. Sparkle.framework 를 `Prism.app/Contents/Frameworks/` 에 임베드하고, pyobjc 로
   `SPUStandardUpdaterController` 를 앱 시작 시 인스턴스화 (app.py 에 훅 추가).
4. 릴리스마다 `generate_appcast <dmg폴더>` 로 `appcast.xml` 갱신 + GitHub Releases 업로드.

> Sparkle 자체는 자체 EdDSA 키로 동작해 Apple Developer ID 없이도 무결성 검증은 됨.
> 단 업데이트본도 미공증이면 Gatekeeper 마찰은 남음 → T2 와 함께 가는 것을 권장.

## 알려진 productization TODO
- **config 쓰기**: 앱 번들은 읽기전용 → 설정 UI 의 모델 변경 저장이 번들 내 config.json 에 안 됨.
  → config/키 경로를 `~/Library/Application Support/Prism/` 로 옮기는 패치 필요(서버 코드).
- **CDN 의존**: UI 가 Tailwind·Alpine·Geist 를 CDN 로드 → 첫 실행 시 인터넷 필요.
  → `prism/vendor/` 에 벤더링 후 serve.py URL 교체하면 오프라인 가능.
