# 모델 선정용 평가 방법 리서치 · 아이템 메타 유사도 2026-09-03

배경: 여러 모델을 같은 정답셋에 돌려 최종 모델을 고른다(`/compare-models` · 실험실 › 평가 › 모델별 비교).
지금 재는 것은 등급 일치율 · 사유 자카드 · 인텐트 정확/자카드 · 유해 미탐률 · 빈 결과 · 비용 · 속도다.
정답셋(`golden.expected`)에는 이미 `content_category` · `entities` · `summary` 가 들어 있는데 채점에 안 쓴다.
이 문서는 그 빈칸을 무엇으로 채울지 조사한 결과다. 표준 라이브러리만 쓰는 제약 안에서 고른다.

## 필드별 추천

| 필드 | 1순위 | 2순위 | 이유 | 난이도 | 외부 호출 |
|---|---|---|---|---|---|
| intent(복수 라벨) | 샘플 기준 F1 | macro F1 + 정확 일치율 | 문서별 체감 점수 · 희귀 라벨 감시 | 낮음 | 없음 |
| content_category(계층) | 계층 F1(조상 노드 확장 후 집합 P/R/F1) | 샘플 자카드 | 부모까지만 맞춘 경우 부분 점수 | 중간(부모 조회) | 없음 |
| topic_categories | 샘플 F1 | macro F1 | intent 와 동일 | 낮음 | 없음 |
| entities | 정규화 후 집합 P/R/F1 | MUC 식 부분 일치 F1(부분 겹침 0.5점 · difflib 0.8 이상) | 표기 변형 흡수 · 경계 오류와 누락 구분 | 중간(`entdict` 별칭 사전 재사용) | 없음 |
| summary(한 문장) | 임베딩 코사인(`embed.cosine` 기존) | LLM 루브릭(정답 참조형 2~3항목 · `evalops` 루브릭 저지 재사용) | 짧은 문장 의미 비교 · 사람 판단과 상관 | 낮음 / 중간 | 임베딩 API / LLM |
| topic(명사구) | 정규화 정확 일치 | 임베딩 코사인 | 짧아서 정확 일치 먼저 | 낮음 | 임베딩 API |

메모
- ROUGE/BLEU 는 한국어 조사·어미 때문에 어절 단위로는 나쁘다. 형태소 분석기가 없으므로 쓴다면 문자 2·3-gram F1 정도가 대안이다.
- 임베딩 코사인은 분포가 좁다(0.7~0.95). 절대값 컷오프보다 모델 간 상대 비교로 쓰고, 컷오프는 "정답 자기 자신 vs 무관 문장" 분포를 먼저 재서 정한다.
- LLM 심판은 후보 모델과 다른 계열로 둔다.

## 여러 필드를 한 점수로

실무 순서: 게이트(필드별 최소 통과선) → 파레토로 후보 축소 → 가중 합성으로 최종 순위 → 비용 열을 붙여 보고.
- 게이트: 예를 들어 등급 일치율 0.85(현재 `eval_gate`) · 엔티티 F1 0.7 · 인텐트 샘플 F1 0.6. 한 필드가 무너진 모델이 다른 필드로 만회하는 것을 막는다.
- 가중 합성: 필드별 0~1 점수 × 업무 가중치. 가중치를 ±10% 흔들어도 순위가 유지되는지 같이 본다.
- 파레토: 후보 5개 안팎이면 표 하나로 충분하다. 어느 필드에서도 뒤지지 않는 모델만 남기고 비용·속도로 결정.

## 순위가 우연이 아닌지

- 정답셋 크기: 정확도 85% 근처에서 500건이면 95% 신뢰구간 약 ±3점. ±1.5점을 원하면 약 2,000건. 모델 차이가 2~3점이면 500건으로 구분이 안 된다.
- 부트스트랩: 문서 단위 복원 추출 1,000회 이상으로 지표 재계산 → 2.5·97.5 백분위. `random` 모듈로 된다. 어떤 지표에도 적용된다.
- 짝 비교: 같은 문서를 여러 모델에 돌리므로 짝 검정을 쓴다. 이진(맞음/틀림)이면 McNemar, 연속 점수면 paired bootstrap(A−B 차이 분포에 0 이 들어가는지). 모델 5개면 10쌍이니 Bonferroni 보정.
- 사람 간 일치도가 상한선: 2인 검수 데이터로 Cohen kappa(라벨 집합이면 Krippendorff alpha)를 재고, 모델 일치율이 사람 간 일치율에 근접하면 정답셋 품질이 병목이다.

## 오토파일럿과의 연결(설계안)

현재 오토파일럿(`evalops.autopilot_start`)은 기본 모델 하나로 학습 배치를 반복하며 목표 일치율까지 프롬프트를 보정한다.
모델 비교와 붙이는 방법:
1. 후보 모델 목록을 오토파일럿 인자로 받는다(`models`). 라운드마다 `learning_batch(team, models)` 가 비교를 함께 붙인다(이미 `models` 가 2개 이상이면 붙는 코드가 있다).
2. 게이트를 오토파일럿 목표(`target`)와 같은 값으로 쓴다. 게이트를 넘는 후보 중 최저 비용 모델을 `cheapest_passing` 으로 추천하고, 라운드 종료 시 그 모델을 기본 모델로 확정할지 사용자가 고른다(자동 승격은 하지 않는다).
3. 위 필드별 지표를 넣으면 게이트가 여러 개가 된다. 오토파일럿의 "목표 달성" 판정도 등급 일치율 하나가 아니라 게이트 전부 통과로 바꾼다.

## 적용 현황(2026-09-03)

1. 완료: intent 샘플 F1 · 카테고리 계층 F1 · 엔티티 정규화 F1(부분 일치 병기) · 리드문 유사도를 `abtest.score` 에 추가(`prism/metaeval.py`). 비교표에 행 추가 · 종합 점수(등급 0.4 + 메타 4항목 각 0.15) · 게이트(등급 `eval_gate` · 메타 `meta_gate` 기본 0.6) 통과 뒤 종합 점수로 순위.
   리드문은 임베딩 코사인(`embed.cosine` · 실키 있을 때 평가 1회분을 모아 계산 · 방법은 `summary_sim_method` 로 리포트에 남긴다)이고,
   키가 없거나 호출이 실패하면 문자 2-gram F1 로 폴백한다. **폴백일 때는 어순만 달라도 점수가 낮아 리드문 게이트만 0.4(`metaeval.SUMMARY_FALLBACK_GATE`)를 쓴다** — 코사인이면 메타 게이트 0.6 그대로.
2. 완료(1차): 진단 → 쿡북 → 반영 루프. 지표에서 문제점(사유 버킷 · 인텐트 누락/과다 · 카테고리 혼동/누락 · 엔티티 누락/과다 · 리드문 저유사)을 뽑고, 각 문제에 스테이지별 지시 문장을 붙여 화면에 보인다. "프롬프트에 반영" 은 `/cookbook-apply` 로 공통 스테이지 프롬프트 끝에 얹고, 다시 비교 실행으로 확인한다. 내려가면 사전·정책 › 메타 프롬프트에서 문장을 지운다.
3. 남음: 부트스트랩 신뢰구간(현재 이항 CI) · 오토파일럿 후보 모델 목록 인자 · 쿡북 반영분의 자동 원복(학습 배치의 회귀 가드와 연결).

## 출처

- scikit-learn 지표 문서 https://scikit-learn.org/stable/modules/model_evaluation.html
- 계층 분류 지표 재검토 https://arxiv.org/html/2410.01305v2
- nervaluate(SemEval 2013 task 9 방식) https://github.com/MantisAI/nervaluate
- 엔티티 평가 해설 https://www.davidsbatista.net/blog/2018/05/09/Named_Entity_Evaluation/
- G-Eval https://arxiv.org/abs/2303.16634 · BERTScore https://arxiv.org/abs/1904.09675
- 한국어 요약 평가 RDASS https://aclanthology.org/2020.coling-main.491/
- Dror 2018 통계 검정 가이드 https://aclanthology.org/P18-1128/
- promptfoo assertions https://www.promptfoo.dev/docs/configuration/expected-outputs/
- Ragas semantic similarity https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/semantic_similarity/
