# Prism 이미지 트랙 + 로컬 UI (방식 A)

Prism에 **이미지→메타** 경로와 **로컬 웹 UI**를 더한 확장. Prism 코어는 한 줄도 고치지 않았다(무수정). 신규 파일 2개뿐:

| 파일 | 역할 |
|---|---|
| `prism/imagext.py` | 이미지 인제스트 어댑터: 이미지 → (OCR + DocVision) → 합성 `Content(title/body)` |
| `prism/serve.py` | 로컬 웹 UI (stdlib `http.server`, 의존성 0) |

## 동작 원리 (방식 A)

```
이미지(들) ─[imagext]─ OCR(텍스트) + DocVision(시각) ─▶ 합성 Content(4필드)
                                                          └─▶ 기존 pipeline.extract ─▶ 리드문·엔티티·인텐트·콘텐츠 카테고리
```

- 이미지에서 뽑은 신호로 **4필드 Content를 합성**해 기존 텍스트 파이프라인에 그대로 태운다.
- 리드문·엔티티·인텐트·콘텐츠 카테고리는 **Prism의 `run_item`이 생성** → 코어 무수정.
- **DNM 체계(13. 프로젝트 기획 / 1312)**: `item_meta` 필드는 `summary`(리드문) · `entities`(엔티티) · `intent`(인텐트) · `content_category`(콘텐츠 카테고리). 코어 스키마·프롬프트·리포트까지 이 명칭으로 정렬됨.
- 여러 이미지는 **하나의 콘텐츠로 통합**(body에 이미지별 신호 누적) → 메타 1세트.
- Upstage 전용: OCR=`document-digitization`, 시각=`solar-docvision`, 생성=설정된 chat 모델.

## API 키 설정 (UI)

서버 실행 후 우상단 **설정(톱니)** 버튼 → Upstage API 키 입력 → **저장** → **연결 테스트**.
키를 넣으면 OCR · DocVision · 생성이 실모델로 동작한다(키 없으면 자동 mock). `이 기기에 저장` 체크 시 `~/.prism_key`에 저장되어 재시작 후에도 유지된다. 키는 `config.json`에 저장되지 않는다.

## 실행

```bash
cd ~/prism
python3 -m prism.serve              # http://127.0.0.1:8765
python3 -m prism.serve --port 9000  # 포트 변경
python3 -m prism.serve --mock       # 키가 있어도 강제 mock
```

- **키 없음 → 자동 mock**: 설치·키 없이 UI 흐름 전체를 바로 체험.
- **실모델**: `UPSTAGE_API_KEY` 환경변수 + Prism `init`으로 chat 엔드포인트 설정
  (`python3 -m prism.cli init --base-url https://api.upstage.ai/v1 --model solar-pro2`).

## 디자인

UI는 **HyperUX**(behavior-first Alpine.js 패턴, <https://github.com/markmead/hyperux>) 디자인을 따른다 — Tailwind CSS 라이트 테마 + Alpine.js(`x-data`/`x-on:`/`x-bind:` 명시 문법), `huxTabs` 스타일 탭(roving tabindex·화살표 내비).

> **트레이드오프**: Tailwind/Alpine을 CDN으로 불러오므로(빌드 단계 없는 stdlib 서버) **로컬 실행 시 네트워크가 필요**하다. 완전 오프라인이 필요하면 두 라이브러리를 `public/`에 벤더링하고 `<script src>`를 로컬 경로로 바꾸면 된다. (백엔드·추출 로직은 여전히 의존성 0.)

## UI 구성 (이미지→메타 데모 중심)

- **이미지 업로드 탭**: 여러 장 + 콘텐츠 그룹/제목/캡션(선택) → 추출 실행
- **텍스트 입력 탭**: 기존 Prism 텍스트 경로 그대로
- 결과: **리드문 / 엔티티 / 인텐트 / 콘텐츠 카테고리** 카드 + 품질 등급(G/R)
- **이미지 추출 신호**(이미지별 OCR/Vision), 합성 Content, 원본 JSON 펼쳐보기
- **전체 리포트 열기**: 기존 Prism 통합 HTML 리포트(`/report`)를 새 탭으로

## 검증 (mock)

```bash
python3 -m prism.serve --mock &
curl -s -F image0=@a.png -F image1=@b.png -F displayServiceName=포토 localhost:8765/run   # 2장 → 메타 1세트
curl -s localhost:8765/report -o report.html                                              # Prism 리포트
```

## 한계 / 후속

- 현재는 **방식 A**(어댑터). 운영 승격 시 **방식 B**(네이티브 `image_only` 트랙: `pipeline.py`의 skip 분기를 이미지 전용 아이템 메타 에이전트로 교체)로 전환 검토.
- `content_category` 사전화, 다중 이미지 통합 정책(장수 상한·가중치)은 DNM `1312`/`134` 문서 기준 후속 과제.
