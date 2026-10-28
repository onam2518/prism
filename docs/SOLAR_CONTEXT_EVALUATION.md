# Solar Pro 4 원천 메타 평가

작성일: 2026-10-28. 모델 고정값: `solar-pro4-260806`.

원천 메타 4종의 정답 참조 평가를 수행하는 독립 실행 도구다. `scripts/evaluate_solar_context.py`가
실제 생성 결과, 요청 설정, 프롬프트 전문·해시, 표본, 사용량, 문서별 점수와 짝 비교를 저장한다.
운영 DB·정답·자동 승격 설정을 변경하지 않는다. 기존 `metaeval`의 운영 점수·폴백은 그대로이며,
이 도구의 점수는 별도 실험 결과다.

## 평가에서 구분하는 것

| 질문 | 지표 | 해석 |
|---|---|---|
| 정답 리드문과 같은 내용을 담는가 | 동일한 passage 임베딩 모델의 코사인 | -1~1의 의미 근접도, 정확도·확률이 아님 |
| 같은 표현을 쓰는가 | 문자 2-gram F1 | 표현 겹침을 보는 보조 지표 |
| 원문에 근거한 말인가 | 주장별 supported/contradicted/unverifiable | 생성과 분리된 AI 검토, 사람 검증 전 참고값 |
| 핵심 개체가 맞는가 | 정규화 엔티티 P/R/F1, 타입이 주석된 경우 타입 재현율 | 과잉 추출·누락 분리 |
| 목적·형식 라벨이 맞는가 | 인텐트 P/R/F1, 정확 일치, 하나 이상 적중 | 많이 붙여서 적중률만 높이는 결과 식별 |
| 주제 분류가 맞는가 | 카테고리 F1, 조상 확장 계층 F1 | 상위만 맞은 경우 부분 점수 |

의미 점수의 단순 평균과 원문 사실 일치를 하나의 종합 정확도로 합치지 않는다. 특히
부정·주체·날짜·단위가 바뀐 문장은 임베딩이 매우 가까워도 오답이다. 원문에서 사실인 문장이라도
핵심을 누락하면 정답 참조 점수가 낮을 수 있다. 두 점수는 서로 다른 질문에 답한다.

## 연구 근거

- [RDASS 논문, COLING 2020](https://aclanthology.org/2020.coling-main.491/)과
  [카카오엔터프라이즈 공식 설명](https://kakaoenterprise.github.io/deepdive/210729):
  한국어의 형태 변화와 바꿔 쓰기를 고려하고, 정답뿐 아니라 원문과의 관계도 평가하는 접근.
  이번 구현은 이 분리 원칙을 참고했다. 학습 모델·산식을 재현한 RDASS 구현은 아니다.
- [BERTScore, ICLR 2020](https://arxiv.org/abs/1904.09675): 문맥 임베딩으로 토큰을 정렬해
  표현이 다른 문장을 비교한다. 문장 벡터 코사인과 다른 지표다. 이번에는 조사만 했으며 실행하지 않았다.
- [SAS, MRQA 2021](https://aclanthology.org/2021.mrqa-1.15/)과
  [deepset 공식 기술 블로그](https://www.deepset.ai/blog/metrics-to-evaluate-a-question-answering-system):
  문자열이 겹치지 않는 동의 답변도 평가하는 cross-encoder 접근. 한국어 운영 데이터에서의
  성능은 별도 검증해야 하므로 이번 실측 지표로 사용하지 않았다.
- [SummaC, TACL 2022](https://aclanthology.org/2022.tacl-1.10/): 문서와 리드문의 사실 관계를
  문장 수준으로 나누어 평가하는 근거. 이번 주장별 AI 검토는 SummaC의 NLI 모델을 구현한 것이 아니다.
- [Sentence Transformers 공식 문서](https://www.sbert.net/docs/sentence_transformer/usage/semantic_textual_similarity.html):
  문장 임베딩을 이용한 의미 유사도 계산의 기본 방식.
- [Dror 외, ACL 2018](https://aclanthology.org/P18-1128/): 평가 지표와 표본 구조에 맞는
  통계 비교가 필요하다는 근거. 이번에는 같은 문서의 전후 차이에 짝 부트스트랩을 적용했다.

## 실험 설계

1. 평가용 `purpose=eval` 콘텐츠와 검수 정답을 ID·팀 기준으로 연결해 읽는다.
2. 중복 원문을 거부하고 ID의 SHA-256 순으로 표본을 고정한다. 모델 입력에는 정답을 보내지 않는다.
3. 개발에 사용한 콘텐츠는 후속 검증에서 제외한다. 콘텐츠의 과거 학습 사용 이력까지 보장하는 분할은 아니다.
4. 모델 스냅샷, temperature=0, reasoning_effort=none, 출력 상한 2,048토큰을 고정한다.
   기존안·후보안 모두 같은 조건으로 원천 메타를 각각 네 번 독립 호출한다. 재시도는 하지 않는다.
5. 문서마다 두 변형의 제출 순서를 번갈아 바꾼다. 요청은 최대 4개 동시 실행하므로
   완료 순서까지 엄격하게 교차시키는 실험은 아니다.
6. JSON과 사전 값을 검증한다. 실패한 생성은 빈 산출로 처리하고 해당 필드 점수는 0점으로 유지한다.
7. 리드문은 `solar-embedding-2-passage`로 양쪽을 같은 방식으로 임베딩한다.
   실패·잘못된 벡터는 의미 미측정(None)이며 문자 F1로 대체하지 않는다.
8. 사실 검토에는 원문 문장 번호와 산출 리드문만 보낸다. 정답·변형 이름·다른 후보는 숨긴다.
   모델이 고른 근거 ID를 코드가 원문으로 복원하고, 없는 ID는 오류로 거부한다.
9. 문서별 지표의 평균과 전후 차이를 기록하고, 같은 문서끼리 2,000회 복원 추출한다.
   고정 시드 20261028, percentile 95% 신뢰구간. 짝 결측 수를 함께 표시한다.
10. 서로 다른 척도의 가중 종합 점수나 미보정 통과선을 만들지 않는다. 구간이 0을 포함하면
    차이를 확정하지 않는다. 다중 비교·반복 개발을 보정한 확증 검정은 아니므로 탐색 결과로 해석한다.

## 사실 검토의 한계

평가자도 생성과 같은 Solar 스냅샷이다. 별도 호출과 변형 이름 숨김으로 직접적인 비교 편향을
줄였지만, 독립 모델·사람 검수에 해당하지 않는다. 주장 분해와 지지 여부의 오류가 남을 수 있다.
원문 문장 번호가 유효하다는 검사는 근거 내용이 실제로 주장을 뒷받침함을 보장하지 않는다.
보고서의 지지율 1.0은 사람 기준 사실 정확도 100%를 뜻하지 않는다.

합성 경계 사례 12개는 평가 도구의 약점을 확인하기 위해 작성한 자료다. 검수자가 확정한
운영 정답셋과 분리한다. 잘못된 주장도 높은 코사인을 받을 수 있는지와 근거 판정의 반응을 확인한다.

## 재현

프롬프트만 내보내기:

```sh
python3 scripts/evaluate_solar_context.py --output /tmp/solar-prompts
```

API 키를 환경변수로 설정한 뒤 실제 비교:

```sh
python3 scripts/evaluate_solar_context.py --golden /path/to/holdout.jsonl \
  --output /tmp/solar-evaluation-new --live --judge --limit 24 --workers 4
```

JSONL 각 행에는 `id`, `content`, `expected`, 선택적인 `provenance`를 넣는다.
`content`는 기존 Content 스키마, `expected`는 summary·entities·intent·content_category를 사용한다.
라벨이 없으면 미주석으로 제외한다. 명시적으로 값이 없어야 하는 정답은 `meta_status`의 `no_value`로 구분한다.
성공한 이전 결과 파일이 있는 경로에는 덮어쓰지 않는다. API 키는 결과물에 저장하지 않는다.

## 프롬프트와 API 기준

[Upstage 모델 문서](https://console.upstage.ai/docs/models/solar-pro-4)에서 날짜 고정 모델과
입력 $0.30·출력 $1.20·캐시 입력 $0.06/100만 토큰을 확인했다.
보고서 비용은 API usage에 이 단가를 적용한 추정값이며 실제 청구서가 아니다.
생성 비용과 평가자 비용은 분리하고 임베딩 비용·서버비·세금·환율은 포함하지 않는다.
[추론 설정 문서](https://console.upstage.ai/docs/capabilities/generate/reasoning)의 의미에 따라 none을 명시했다.
[임베딩 문서](https://console.upstage.ai/docs/capabilities/embed)에 따른 신규 passage 모델을 고정했다.

프롬프트 전문은 저장소 기본 규칙·전체 사전·Solar용 예시를 조립한 결과다. 후보 생성은
`solar_context.call_system`에서 명시적으로 실행하며 운영 기본 빌더는 교체하지 않는다. 운영 서버의
프롬프트 스튜디오 override나 누적 피드백을 재현한 덤프가 아니다. 내용 변경은 시스템 프롬프트
해시로 추적되며 다른 Solar 버전과 다른 모델 계열에는 이번 보강을 적용하지 않는다.
