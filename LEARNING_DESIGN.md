# 학습데이터 추출·게임화 고도화 설계 (승인 대기)

작성: 2026-07-02. 목적: 콘텐츠 분류·운영 특화 LLM 구축을 위한 (1) 게임화 고도화 (2) 휴먼루프 학습데이터/요구사항 추출 구조의 설계안과 논문 근거 정리. 본 문서의 모든 원리·구조 제안은 아래 검증된 논문에만 근거한다(서지 전수 검증 2026-07-02).

## 1. 현행 게임화 진단 (코드 사실)

- 서버 점수식 `pts = 검수행수*10 + 교정수*25` (store.py:440). 판정의 정오를 골든·합의와 대조하는 로직 없음. 무성의 클릭과 정확한 검수가 동일 보상.
- 역인센티브: 히어로·배지 "정확도" = good 비율(store.py:417). 전부 '정확' 클릭 시 지표 상승.
- 배지 12종 중 10종이 볼륨·스트릭·레벨 기반. 스트릭은 하루 1클릭 유지. /feedback 레이트리밋 없음.
- '오늘의 미션'은 판정·보상 없는 안내 배너 2종. '개선 채택'(+25pt)은 실제 채택과 무관(REAP plan 생성만으로 성립).
- split(불일치)은 집계되나 검수 화면 미노출, 골든에서 조용히 제외될 뿐 재검토 흐름 없음.
- 진척율 분모 = 전체 results 수(store.py:242,433), 검수 대상(YELLOW)이 아님.
- 클라 토스트 점수(+5/+15)와 서버 산정(0/+25) 불일치. 특히 '분류 채움'은 서버 0점.
- 게임 상태(점수·레벨·스트릭)는 전부 feedback 재계산·비영속. 서버 영속은 배지·캐릭터뿐.

문헌 정합: Mekler et al. 2017은 이미지 주석 과제에서 포인트·레벨·리더보드가 수량만 늘리고 품질·내적 동기에는 효과가 없음을 실증. 현행 체계는 이 한계 그대로.

## 2. 게임화 고도화 방안 (전 항목 논문 근거)

- G-1. 골드 문항 삽입 + 검수자 정확도 산정: 골든셋 일부를 정답 알려진 문항으로 큐에 무작위 삽입, 검수자별 골드 정확도 산정. [Oleson 2011; Kittur 2008]
- G-2. 품질 가중 보상: 점수에 골드 정확도 배율 적용. '정확도 90%' 배지 기준을 good 비율에서 골드 정확도로 교체(역인센티브 제거). [Snow 2008]
- G-3. 합의 기반 지연 보상: 사후 합의 성립 + 내 판정 일치 시 보너스. [von Ahn & Dabbish 2004]
- G-4. 검수자 신뢰도 모델: Dawid-Skene EM 오류율(초기엔 합의 일치율 근사). 골든 확정 다수결의 가중치로도 사용(현행 1명 good 확정 가능 문제 보완). [Dawid & Skene 1979; Raykar 2010]
- G-5. 판정 가능한 미션 + 우선순위 큐: 미션에 완료 판정·보상. 대상 = 모델 불확실 콘텐츠·split 재검토. [Lewis & Gale 1994; Settles 2009; Aroyo & Welty 2015]
- G-6. 요소-욕구 매핑 배지 재편: 질 기반 배지 3종 추가(골드 정확도·합의 기여·불일치 해결). 보상은 통제가 아닌 유능감 정보 제공 형태. [Sailer 2017; Ryan & Deci 2000]
- 한계 고지: 게임화 효과는 맥락 의존적이므로 도입 후 골드 정확도·일치도 지표로 실측까지가 설계. [Hamari 2014]
- 부수 수정(버그성): 진척율 분모 YELLOW 화, 토스트·서버 점수 일치화, /feedback 레이트리밋.

## 3. 학습데이터 추출 구조 (핵심)

원천 신호: feedback(verdict·stage·note 요소태그·REAP 4필드·ts·reviewer·team), golden(content·expected), patch 교정, contents(item_meta·quality_meta).

### 3종 데이터셋
- D-1 SFT 분류(JSONL): 입력=콘텐츠, 출력=카테고리·인텐트·등급·사유·리드문. 골든셋에서 생성. taxonomy는 프롬프트 외재화(재학습 없이 개편 가능). [Zhou 2023 LIMA; Inan 2023 Llama Guard]
- D-2 선호쌍(DPO): rejected=모델 원출력, chosen=사람 교정본. 전제: patch 전 원본 보존(현재 소실, 개선 소요 #1). [Rafailov 2023 DPO]
- D-3 Rationale: REAP explain/plan + 검수 노트 → (입력, 라벨, 판단근거). 필요 라벨 수 절감 지렛대. [Hsieh 2023 Distilling Step-by-Step]

### 통계 층 (관리자 페이지 표시)
- 클래스 커버리지: Tier1 21종·등급·인텐트별 보유 vs 목표 vs 부족.
- 일치도: 단순 일치율 + Krippendorff's alpha(참고 지표, 임계값 기계 적용 금지). [Hayes & Krippendorff 2007; Artstein & Poesio 2008]
- 검수자 신뢰도 표: 합의 일치율 → Dawid-Skene EM(축적 후). G-4와 공유.
- 라벨 오류 플래깅: 모델 예측확률 vs 골든 라벨로 오류 의심 자동 플래그 → 재검토 큐. [Northcutt 2021 Confident Learning; Northcutt 2021 NeurIPS(벤치마크 평균 3.3% 라벨 오류)]
- 평가 신뢰구간: 모든 정확도에 SEM=sqrt(p(1-p)/n) 기반 95% CI 병기, CI 겹치면 판정 불가 표시. [Miller 2024]
- 불일치 보존·노출: split 목록 + 원시 의견 분포(upsert 구조가 이미 검수자당 1의견 보존). [Plank 2022; Uma 2021]

### 학습 소요 기준치(자동 산출 대비표)
| 용도 | 기준치 | 근거 |
|---|---|---|
| 분류 부트스트랩 하한 | 클래스당 8예시(21종 → 최소 168) | SetFit(Tunstall 2022) |
| 운영급 분류 LLM | 라벨 ~13.5k | Llama Guard(Inan 2023) |
| SFT 정렬 | 고품질 ~1k | LIMA(Zhou 2023) |
| 선호쌍 참고 상한 | 보상모델 33k 규모 | InstructGPT(Ouyang 2022) |
| 평가셋 | 큐레이션 ~100건/축 + CI | tinyBenchmarks(Maia Polo 2024); Miller 2024 |
| 학습 자원 | GPU 1장급 | LoRA(Hu 2021); QLoRA(Dettmers 2023, 48GB 1장 65B) |

모델 구조 제안(논문 범위 한정): 오픈 베이스 LLM + LoRA/QLoRA 어댑터, taxonomy 프롬프트 외재화(Llama Guard), rationale 멀티태스크(Distilling Step-by-Step), SFT(LIMA) 후 검수 선호쌍 DPO.

### 관리자 페이지 구현 스코프
새 패널 "학습 데이터 현황"(관리자 그룹): 타일(골든 수·클래스 커버리지%·일치도 α·오류 의심 n·추출 가능 SFT/DPO/rationale 건수), 클래스별 보유/목표/부족 표(출처 각주), 검수자 신뢰도 표, 불일치 목록, JSONL 내보내기 3종. 서버 라우트: /learn-data(통계), /learn-export?kind=sft|dpo|rationale(다운로드), 관리자 게이트.

## 4. 기타 개선 소요 (승인 시 착수)

1. patch 교정 전 원본 소실 → 교정 로그(before/after) 테이블 신설. D-2 전제.
2. 교정 요소(element) 미영속(note 태그 파싱 의존) → feedback element 컬럼.
3. 같은 검수자·같은 콘텐츠 다중 요소 교정이 upsert로 덮여 소실 → append 교정 로그(#1과 동일 테이블).
4. 진척율 분모 오류(전체 results vs YELLOW).
5. 토스트·서버 점수 불일치.
6. 검수 엔드포인트 레이트리밋 부재.
7. 게임 상태 비영속(feedback 초기화 시 성취 소실) → 이벤트 로그화.
8. split 집계 UI 미노출.
9. config.json db_path가 저장소 밖 옛 경로(~/Desktop/metacli) 참조.

## 5. 참고문헌 (전수 서지 검증 2026-07-02)

### 라벨 집계·일치도·노이즈
- Dawid & Skene 1979, "Maximum Likelihood Estimation of Observer Error-Rates Using the EM Algorithm", JRSS-C 28(1), DOI 10.2307/2346806
- Snow, O'Connor, Jurafsky & Ng 2008, "Cheap and Fast: But is it Good? Evaluating Non-Expert Annotations for Natural Language Tasks", EMNLP, ACL D08-1027
- Raykar et al. 2010, "Learning From Crowds", JMLR 11
- Cohen 1960, "A Coefficient of Agreement for Nominal Scales", EPM 20(1), DOI 10.1177/001316446002000104
- Artstein & Poesio 2008, "Inter-Coder Agreement for Computational Linguistics", CL 34(4), DOI 10.1162/coli.07-034-R2
- Hayes & Krippendorff 2007, "Answering the Call for a Standard Reliability Measure for Coding Data", CMM 1(1), DOI 10.1080/19312450709336664
- Northcutt, Jiang & Chuang 2021, "Confident Learning", JAIR 70, arXiv:1911.00068
- Northcutt, Athalye & Mueller 2021, "Pervasive Label Errors in Test Sets Destabilize ML Benchmarks", NeurIPS D&B, arXiv:2103.14749
- Klie, Webber & Gurevych 2023, "Annotation Error Detection", CL 49(1), arXiv:2206.02280

### 불일치·능동학습
- Aroyo & Welty 2015, "Truth Is a Lie: Crowd Truth and the Seven Myths of Human Annotation", AI Magazine 36(1), DOI 10.1609/aimag.v36i1.2564
- Plank 2022, "The 'Problem' of Human Label Variation", EMNLP, arXiv:2211.02570
- Uma et al. 2021, "Learning from Disagreement: A Survey", JAIR 72, DOI 10.1613/jair.1.12752
- Settles 2009, "Active Learning Literature Survey", UW-Madison TR1648
- Lewis & Gale 1994, "A Sequential Algorithm for Training Text Classifiers", SIGIR, arXiv:cmp-lg/9407020
- Margatina et al. 2023, "Active Learning Principles for In-Context Learning with LLMs", Findings of EMNLP, arXiv:2305.14264

### 게임화·크라우드 품질
- Hamari, Koivisto & Sarsa 2014, "Does Gamification Work?", HICSS, DOI 10.1109/HICSS.2014.377
- Mekler et al. 2017, "Towards Understanding the Effects of Individual Gamification Elements on Intrinsic Motivation and Performance", CHB 71, DOI 10.1016/j.chb.2015.08.048 (주의: 게임 요소는 수량만 증가, 품질 효과 없음이 결론. 품질 근거로 인용 금지)
- von Ahn & Dabbish 2004, "Labeling Images with a Computer Game", CHI, DOI 10.1145/985692.985733
- Cooper et al. 2010, "Predicting protein structures with a multiplayer online game", Nature 466, DOI 10.1038/nature09304
- Oleson et al. 2011, "Programmatic Gold: Targeted and Scalable Quality Assurance in Crowdsourcing", AAAI HCOMP WS-11-11
- Kittur, Chi & Suh 2008, "Crowdsourcing user studies with Mechanical Turk", CHI, DOI 10.1145/1357054.1357127
- Ryan & Deci 2000, "Self-determination theory and the facilitation of intrinsic motivation...", Am Psych 55(1), DOI 10.1037/0003-066X.55.1.68
- Sailer et al. 2017, "How Gamification Motivates", CHB 69, DOI 10.1016/j.chb.2016.12.033
- Ryan, Rigby & Przybylski 2006, "The Motivational Pull of Video Games", Motivation and Emotion 30(4), DOI 10.1007/s11031-006-9051-8

### LLM 학습·평가 소요
- Zhou et al. 2023, "LIMA: Less Is More for Alignment", NeurIPS, arXiv:2305.11206
- Ouyang et al. 2022, "Training language models to follow instructions with human feedback", NeurIPS, arXiv:2203.02155
- Hu et al. 2021, "LoRA: Low-Rank Adaptation of Large Language Models", ICLR 2022, arXiv:2106.09685
- Dettmers et al. 2023, "QLoRA: Efficient Finetuning of Quantized LLMs", NeurIPS, arXiv:2305.14314
- Rafailov et al. 2023, "Direct Preference Optimization", NeurIPS, arXiv:2305.18290
- Hsieh et al. 2023, "Distilling Step-by-Step!", Findings of ACL, arXiv:2305.02301
- Tunstall et al. 2022, "Efficient Few-Shot Learning Without Prompts (SetFit)", arXiv:2209.11055
- Mosbach et al. 2023, "Few-shot Fine-tuning vs. In-context Learning", Findings of ACL, DOI 10.18653/v1/2023.findings-acl.779
- Maia Polo et al. 2024, "tinyBenchmarks: evaluating LLMs with fewer examples", ICML, arXiv:2402.14992
- Miller 2024, "Adding Error Bars to Evals", arXiv:2411.00640
- Markov et al. 2023, "A Holistic Approach to Undesired Content Detection in the Real World", AAAI, DOI 10.1609/aaai.v37i12.26752
- Inan et al. 2023, "Llama Guard", arXiv:2312.06674
