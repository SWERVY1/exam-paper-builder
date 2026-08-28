---
name: exam-paper-builder
description: Analyze local exam question and solution PDFs, split them into linked passage, question, answer, and solution assets, classify them, and compose selected items through a continuous single-column flow into verified A3, A4, B4, or Korean 8-jeol problem and answer PDFs. Use for mock exams, worksheets, question banks, and solution booklets. Do not use to download, host, or publish source exams.
---

# Exam Paper Builder

로컬 시험 문제지와 답지를 분석하고 구조화한 뒤, 사용자가 고른 문항으로 새 번호의
문제지 PDF와 정답·해설 PDF를 각각 만든다.

## 안전 범위

1. 원본 PDF를 읽기 전용으로 다룬다. 수정·이동·삭제하지 않는다.
2. 사용자가 이용 권한을 가진 로컬 파일만 입력으로 받는다.
3. 시험 자료 다운로드, 로그인·접근 제한 우회, 재호스팅, 외부 게시를 수행하지 않는다.
4. 원본 문제·해설·문항 이미지를 스킬 패키지나 예시 출력에 포함하지 않는다.
5. OCR 전체 본문을 기본 저장하지 않는다. 구조 판별에 필요한 최소 텍스트와 좌표만 쓴다.

## 시작할 때 읽을 자료

- 처음 보는 PDF 또는 형식이 섞인 폴더: `references/input-analysis.md`
- 지문·문항·답·해설의 ID와 관계: `references/asset-graph.md`
- 국어·영어 유형 또는 다른 과목 단원: `references/classification-taxonomy.md`
- 선택 조건, seed, 새 번호, 출처 표기: `references/selection-manifest.md`
- A3/A4/B4/8절 조판과 하드 게이트: `references/composition-and-qc.md`

필요한 문서만 읽고, 사용자의 원본 파일을 문서 예제로 복사하지 않는다.

## 1. 입력과 목표를 확정한다

다음을 사용자 요청 또는 파일에서 확인한다.

- 문제 PDF 또는 문제 폴더
- 답지·해설 PDF 또는 폴더
- 과목, 시험 구간, 선택과목 구분
- 선택 조건: 문항 ID, 유형, 단원, 난이도, 개수, 제외 목록, seed
- 공통 지문 묶음 정책: 개별 문항 또는 묶음 전체
- 출력 용지: `A3`, `A4`, `B4-JIS`, `B4-ISO`, `8JEOL`, `GUKJEON-8JEOL`
- 방향, 단 수, 여백, 제본 여백
- 답지 형식: `key-only`, `solutions-only`, `key-and-solutions`
- 해설 없는 문항 허용 여부

문제 위 출처 표기는 PDF를 만들기 직전에 반드시 물어본다. 다음 중 사용자의 명시적
선택이 없으면 `compose-exam`을 실행하지 않는다.

- `표시 안 함` → `source_label.mode: none`
- `연도만` → `source_label.mode: year`
- `연도+월` → `source_label.mode: year-month`

사용자가 단순히 “기본값으로”라고 답해도 출처 표기 모드를 추정하지 않는다.

## 2. 문제지와 답지를 각각 진단한다

먼저 설치 상태와 명령을 확인한다.

```powershell
exam-split --version
exam-split inspect "입력/문제지.pdf"
exam-split inspect "입력/답지.pdf"
```

각 PDF에 대해 다음을 기록한다.

- 페이지 수, 페이지별 실제 크기, 회전, 잘린 MediaBox
- 텍스트 레이어, 스캔, 혼합 여부
- 페이지별 단 수와 읽기 순서
- 머리말·꼬리말·쪽 번호·과목 띠
- 문항 번호열, 번호 누락·중복·재시작
- 공통 지문 범위와 중첩 자료
- 페이지 또는 단을 넘는 지문·문항·해설
- 정답표 위치와 해설 본문 레이아웃
- OCR fallback 사용 여부와 검수 상태

문제지의 레이아웃 프로필을 답지에 재사용하지 않는다. 처음 보는 형식은 대표 샘플을
승인하기 전까지 폴더 전체에 같은 좌표를 적용하지 않는다.

여러 파일이면 인벤토리와 pairing 보고서를 만든다.

```powershell
exam-split inventory "입력/문제" work/problem-inventory.json
exam-split inventory "입력/답지" work/solution-inventory.json
exam-split pair-corpus "입력/문제" "입력/답지" work/pairing-report.json
```

파일명 stem 외에 과목, 시험 구간, 번호열을 교차 확인한다. 후보가 둘 이상이거나 한쪽이
없으면 자동 확정하지 않는다.

## 3. 구조를 제안하고 검수한다

문제지에서 번호와 공통 범위를 찾아 crop 제안을 만든다.

```powershell
exam-split detect-anchors "입력/문제지.pdf" --output work/anchors.json
exam-split propose "입력/문제지.pdf" work/problem-proposal.json `
  --exam-id LOCAL_EXAM --format webp --store-fragments
exam-split preview-proposal work/problem-proposal.json --output work/problem-preview
```

답지는 별도 프로필로 제안한다. 실제 문제지에서 확인한 문항 수를 넘긴다.

```powershell
exam-split propose-solution "입력/답지.pdf" work/solution-proposal.json `
  --exam-id LOCAL_EXAM --format webp --expected-count 45 --store-fragments
exam-split extract-answer-key "입력/답지.pdf" work/answer-key.json `
  --expected-count 45
```

다음 중 하나라도 있으면 `needs_review`를 유지한다.

- OCR만으로 번호를 찾음
- 번호가 누락·중복·재시작함
- 페이지별 크기나 회전이 달라짐
- 공통 지문 범위가 겹침
- 문제와 답지 pairing이 모호함
- 표·그림·선택지의 하단 경계가 불확실함
- 정답표에서 완전한 `1..N` 번호열을 얻지 못함

## 4. 관계형 자산으로 분리한다

다음 단위를 서로 다른 파일과 JSON 항목으로 보존한다.

- `Fragment`: 한 원본 페이지의 안전한 사각 crop
- `Stimulus`: 하나 이상의 공통 지문·자료 Fragment
- `Question`: 문제 Fragment와 참조하는 Stimulus 목록
- `AnswerEntry`: 새 정답표를 그리기 위한 정답 데이터
- `Solution`: 하나 이상의 해설 Fragment
- `Bundle`: 함께 다룰 지문과 관련 문항
- `Assembly`: 선택 문항, 새 번호, 용지, 실제 배치 결과

예를 들어 하나의 지문에 41번과 42번이 연결되면 지문, 41번, 42번을 서로 다른 파일로
저장하고 같은 `bundle_id`로 묶는다. 다중 페이지 지문은 `F01`, `F02` 순서를 보존한다.
관계의 단일 진실 공급원은 파일명이 아니라 JSON ID 참조다.

각 조판 자산에 다음을 기록한다.

- `break_policy`: `flow`, `safe-only`, `keep-together`
- `safe_breaks`: 0~1 정규화 안전 분할점
- `protected_ranges`: 표·그림·수식·선택지처럼 보호할 구간
- `keep_with_next`: 번호나 라벨의 고아 방지
- `min_scale_fraction`: 최소 축소율

## 5. 유형과 단원을 분류한다

구조 분할을 승인한 자산만 분류한다. 먼저 Stimulus/Bundle을 분류하고, 지문과 개별
발문을 함께 보고 Question 태그를 붙인다.

- 국어: 독서, 문학, 화법, 작문, 언어, 매체와 세부 기능 태그
- 영어: 듣기, 목적·심경, 주장·요지·주제·제목, 어법, 어휘, 빈칸, 무관문장,
  순서, 문장삽입, 요약, 장문독해
- 수학·사회·과학·한국사·직업탐구: 해당 시험 시점의 교육과정 단원 경로

`primary_category`, `subtype`, `unit_path`, `skill_tags`, `format_tags`,
`confidence`, `taxonomy_version`, `review_status`를 기록한다. 낮은 신뢰도를 임의의 한
분류로 확정하지 않는다.

## 6. 선택을 결정론적 manifest로 고정한다

자연어 조건을 바로 조판 루프에 넘기지 않는다. 후보 수, 필터, 제외 목록, seed,
선택된 ID, bundle 정책, 원번호-새번호 대응을 `selection-manifest.json`에 기록한다.

```powershell
exam-split select-manifest work/question-bank.json `
  work/selection-manifest.json --count 20 --seed 2401 `
  --bundle-policy whole-bundle --primary-category 독서
```

동일한 입력·필터·seed는 동일한 선택 순서를 만들어야 한다. 사용자가 요구한 수를 정확히
만들 수 없으면 부분 manifest를 완료 결과처럼 쓰지 않는다.

새 번호는 배열 순서대로 정확히 `1..N`이어야 한다. 원본 3번을 첫 문항으로 선택하면
문제 번호, 정답표, 해설 제목이 모두 1번을 사용한다. 공통 지문의 `[3~4]` 같은 범위도
새 번호 범위로 다시 만든다.

## 7. 가상 연속 스트립으로 조판한다

문제지 논리 순서를 `Stimulus Fragment → 관련 Question Fragment → 다음 Bundle`로 만든
뒤, 모든 자산을 단 너비로 맞춘 하나의 가상 y축에 놓는다. 실제로 비정상적으로 긴
비트맵이나 PDF 페이지를 만들 필요는 없다.

단 본문 높이 `H`마다 다음 순서로 배치한다.

1. 현재 단에 자산 나머지가 들어가면 그대로 놓는다.
2. 넘으면 현재 절단선 이전의 마지막 승인 분할점을 찾는다.
3. `flow`는 승인점 또는 충분한 가로 흰 띠, `safe-only`는 승인점만 쓴다.
4. 윗조각을 현재 단에 두고 아랫조각을 다음 단 맨 위에 같은 배율로 잇는다.
5. 2단이면 `1쪽 왼쪽 → 1쪽 오른쪽 → 2쪽 왼쪽` 순서다.
6. 보호 구간을 가르거나 픽셀을 누락·중복하지 않는다.
7. 안전점이 없으면 `keep-together`로 다음 단에 넘긴다.
8. 빈 단에도 맞지 않으면 축소 하한까지만 줄이고, 그래도 실패하면 중단한다.

문제지와 답지를 별도 PDF로 만든다.

```powershell
exam-split compose-exam work/selection-manifest.json --output output/pdf
```

같은 `build_id`를 기본적으로 덮어쓰지 않는다. 사용자가 교체를 승인한 경우에만
`--overwrite`를 쓴다. 최종 납품에서는 `--no-render-qa`를 쓰지 않는다.

## 8. 완료 하드 게이트

PDF 생성 전 다음을 검사한다.

- 선택 문항 수와 새 번호 연속성
- 문제-정답-해설 참조 무결성
- 필요한 공통 지문 포함과 중복 배치 정책
- 원번호를 바꿀 모든 문제·해설의 안전한 번호 bbox 또는 번호 부재 증명
- 사용자가 선택한 `source_label.mode`와 표기 문자열의 일치

PDF 생성 후 다음을 계산해 검사한다.

- 문제지와 답지 파일 수
- 모든 페이지의 목표 MediaBox
- 문제 수, 정답 수, 해설 수의 일치
- 페이지 밖 배치와 겹침 0건
- 분할 자산 원본 y 범위의 누락·중복 0건
- 보호 구간을 가르는 split 0건
- 최소 유효 DPI와 최소 축소율
- 빈 페이지 0건
- QA PNG 전 페이지 렌더 성공

모든 페이지를 실제로 보고 번호, 글꼴, 한글 glyph, 단 넘김, 문장 행, 표·수식·선택지,
정답표와 해설 번호를 확인한다. 접촉표만 보고 완료하지 않는다.

요청 수, pairing, 조판 또는 QA가 실패하면 상태를 `partial` 또는 `blocked`로 보고하고
부족한 수와 대상 ID를 명시한다. 실패한 출력을 완성본으로 전달하지 않는다.

