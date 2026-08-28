# 지문·문항·정답·해설 자산 그래프

crop 좌표와 의미 관계를 분리한다.

```text
crop-manifest.json      원본 페이지, bbox, Fragment 렌더링
catalog.json            Bundle, Stimulus, Question, AnswerEntry, Solution, 분류
selection-manifest.json 선택 문항, 새 번호, 용지와 조판 설정
composition-report.json 실제 페이지·단 배치와 QA 결과
```

## 자산 단위

| 단위 | 역할 |
|---|---|
| Fragment | 한 원본 페이지의 안전한 사각 crop |
| Stimulus | 공통 지문·자료를 이루는 Fragment 목록 |
| Question | 문제 Fragment와 필요한 Stimulus 참조 |
| AnswerEntry | 새 정답표를 그리기 위한 구조화된 정답 |
| Solution | 문항 해설 Fragment 목록 |
| Bundle | 함께 배치 가능한 Stimulus와 관련 Question |
| Assembly | 선택 순서, 새 번호, 출력 설정, 배치 결과 |

지문과 문항을 영구 합치지 않는다. 다중 페이지 자산은 Fragment로 나누고 `F01`, `F02`
읽기 순서를 보존한다.

## 안정적인 ID

ID는 경로와 무관하고 section 내 번호 재시작에도 충돌하지 않아야 한다.

```text
exam_id     LOCAL_2026_KOR_A
bundle_id   LOCAL_2026_KOR_A_B041-042
stimulus_id LOCAL_2026_KOR_A_B041-042_S01
question_id LOCAL_2026_KOR_A_MAIN_Q041
solution_id LOCAL_2026_KOR_A_MAIN_Q041_SOL
```

최소 유일성 범위는 `exam_id + section + original_number`다. 원본 경로와 해시는
provenance에 기록하지만 ID를 매 실행마다 바꾸는 근거로 쓰지 않는다.

## 권장 파일명

```text
LOCAL_2026_KOR_A_B041-042__STIM01__F01.webp
LOCAL_2026_KOR_A_B041-042__Q041__F01.webp
LOCAL_2026_KOR_A_B041-042__Q042__F01.webp
LOCAL_2026_KOR_A_B041-042__Q041__SOL__F01.webp
```

파일명은 사람이 찾기 위한 보조 수단이다. 실제 관계는 JSON ID 참조를 따른다.

## 카탈로그 예시

```json
{
  "schema_version": 1,
  "exam_id": "LOCAL_2026_KOR_A",
  "subject": "korean",
  "source_pair": {
    "problem_pdf": "inputs/problem.pdf",
    "solution_pdf": "inputs/solution.pdf",
    "pairing_status": "verified"
  },
  "bundles": [
    {
      "id": "LOCAL_2026_KOR_A_B041-042",
      "stimulus_ids": ["LOCAL_2026_KOR_A_B041-042_S01"],
      "question_ids": [
        "LOCAL_2026_KOR_A_MAIN_Q041",
        "LOCAL_2026_KOR_A_MAIN_Q042"
      ]
    }
  ],
  "stimuli": [
    {
      "id": "LOCAL_2026_KOR_A_B041-042_S01",
      "fragment_ids": ["stim_p01", "stim_p02"],
      "asset_files": ["...__STIM01__F01.webp", "...__STIM01__F02.webp"]
    }
  ],
  "questions": [
    {
      "id": "LOCAL_2026_KOR_A_MAIN_Q041",
      "section": "main",
      "original_number": 41,
      "bundle_id": "LOCAL_2026_KOR_A_B041-042",
      "stimulus_ids": ["LOCAL_2026_KOR_A_B041-042_S01"],
      "fragment_ids": ["q041_p01"],
      "answer_entry_id": "LOCAL_2026_KOR_A_MAIN_Q041_ANS",
      "solution_id": "LOCAL_2026_KOR_A_MAIN_Q041_SOL"
    }
  ]
}
```

## Fragment 필수 정보

- 원본 식별자, 페이지, 정규화 bbox
- `role`: stimulus, question, solution, answer_table, label
- 읽기 순서
- 렌더 자산 폭·높이와 해시
- `break_policy`, `safe_breaks`, `protected_ranges`, `keep_with_next`
- 표·그림·수식·선택지 포함 여부
- 번호 또는 범위 라벨 bbox
- crop 검수 상태와 분류 검수 상태

안전 분할점은 원본 페이지/단 경계, 기존 Fragment 경계, 충분한 가로 흰 띠, 사람이
승인한 문단 여백 중 하나로 증명한다. 문장 행, 수식 행, 표, 그래프, 선택지 내부는
안전점이 아니다.

## 지문 묶음 규칙

- Stimulus는 한 번 저장하고 여러 Question이 참조한다.
- 큰 지문 안의 특정 문항용 추가 자료는 별도 Stimulus로 만든다.
- 한 Question이 여러 Stimulus를 참조하면 실제 읽기 순서대로 기록한다.
- 개별 문항만 선택해도 필요한 모든 Stimulus를 포함한다.
- 같은 Bundle의 선택 문항이 연속이면 Stimulus를 한 번만 배치한다.
- 선택 순서에서 같은 Bundle이 떨어졌다가 다시 나오면 문맥을 위해 지문을 다시 배치한다.

## 문제-정답-해설 불변식

- 모든 `question_id`는 유일하다.
- 문제와 해설은 같은 `question_id` 계열을 참조한다.
- 새 번호 대응은 문제, 정답표, 해설이 공유한다.
- 정답표와 해설 본문이 충돌하면 자동 선택하지 않는다.
- 서답형 수식 OCR이 불확실하면 `needs_review`로 남긴다.
- 답지 필수 모드에서는 모든 선택 문항에 AnswerEntry와 Solution이 있어야 한다.

