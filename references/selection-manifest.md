# 선택 manifest와 새 번호

자연어 요청을 조판에 바로 넘기지 않고 JSON으로 고정한다. 조판 manifest는 선택 근거,
자산 관계, 번호, 용지, 답지 모드, 출처 표기의 단일 진실 공급원이다.

## 최소 구조

```json
{
  "version": 1,
  "build_id": "mock-001",
  "title": "Practice Exam 1",
  "subject": "korean",
  "font_path": "fonts/korean-body.ttf",
  "source_label": {"mode": "none"},
  "paper": {
    "size": "B4-JIS",
    "orientation": "portrait",
    "columns": 2,
    "flow_mode": "continuous-strip",
    "margins_mm": {"top": 12, "right": 12, "bottom": 12, "left": 12},
    "gutter_mm": 8,
    "header_mm": 12,
    "footer_mm": 7,
    "asset_gap_mm": 3,
    "min_effective_dpi": 150,
    "min_scale_fraction": 0.72,
    "qa_dpi": 120
  },
  "answer": {"mode": "key-and-solutions"},
  "stimuli": [],
  "questions": [],
  "selection": []
}
```

## 출처 표기

조판 직전 사용자에게 반드시 물어본다.

| 사용자 선택 | manifest 값 | 문항 위 표시 |
|---|---|---|
| 표시 안 함 | `none` | 없음 |
| 연도만 | `year` | `YYYY년` |
| 연도+월 | `year-month` | `YYYY년 MM월 시행` |

선택이 없으면 조판하지 않는다. `none`일 때 개별 문항의 `annotation_text`도 허용하지
않는다. `year`와 `year-month`는 구조화된 출처 값으로 문구를 만들며 파일명 문자열을
그대로 노출하지 않는다.

## 자산 객체

```json
{
  "path": "assets/LOCAL_B001__Q003__F01.webp",
  "break_policy": "flow",
  "safe_breaks": [0.41, 0.73],
  "protected_ranges": [[0.46, 0.61]],
  "keep_with_next": false,
  "min_scale_fraction": 0.78
}
```

- `safe_breaks`와 `protected_ranges`는 이미지 위에서 아래로 0~1 좌표다.
- `flow`는 승인점과 충분한 흰 띠, `safe-only`는 승인점만 사용한다.
- `keep-together`는 현재 단에 맞지 않으면 전체를 다음 단으로 넘긴다.
- 기존 Fragment 경계가 안전하면 다시 긴 한 파일로 합치지 않는다.

## 공통 지문과 문항

```json
{
  "stimuli": [
    {
      "id": "B001_S01",
      "assets": ["assets/B001__STIM01__F01.webp"],
      "range_renumber": {
        "mode": "mask",
        "asset_index": 0,
        "bbox": [0.01, 0.01, 0.18, 0.08],
        "text_template": "[{first}~{last}]",
        "single_text_template": "[{first}]"
      }
    }
  ],
  "questions": [
    {
      "id": "Q017",
      "bundle_id": "B001",
      "original_number": 17,
      "stimulus_ids": ["B001_S01"],
      "assets": ["assets/B001__Q017__F01.webp"],
      "answer": "2",
      "solution_assets": ["assets/B001__Q017__SOL__F01.webp"],
      "renumber": {
        "mode": "mask-in-place",
        "asset_index": 0,
        "bbox": [0.015, 0.012, 0.105, 0.095],
        "text_template": "{number}.",
        "number_font_path": "fonts/source-number-matched.ttf"
      },
      "solution_renumber": {
        "mode": "source-number-absent",
        "asset_index": 0
      }
    }
  ]
}
```

## 번호 교체 모드

- `mask-in-place`: 원번호 bbox만 지우고 마침표 오른쪽 끝, 아래선, 잉크 높이를 따라
  같은 위치에 새 번호를 그린다. 원번호 앵커가 있는 문제의 기본 모드다.
- `mask`: 검수된 흰 배경 bbox 안에 새 번호를 맞춰 그린다.
- `source-number-absent`: crop에서 원번호가 실제로 빠진 자산 앞에 새 번호 행을 붙인다.
- `preserve`: 원번호와 새 번호가 같을 때만 쓴다.

원번호가 남은 이미지에 `source-number-absent`를 쓰지 않는다. 복잡한 배경이나 발문과
겹친 번호 bbox는 자동 마스킹하지 않는다.

문항 위 출처 표기를 쓸 때는 `renumber`에 승인된 빈 여백과 서체를 지정한다.

```json
{
  "annotation_text": "2026년 06월 시행",
  "annotation_font_path": "fonts/source-body-matched.ttf",
  "annotation_scale": 0.5,
  "annotation_gap_fraction": 0.22,
  "annotation_foreground": "#4A4A4A"
}
```

실제 잉크 높이는 본문 대표 글자 높이의 `0.45~0.55` 범위로 맞춘다. 위 여백이
부족하면 본문을 이동하거나 전폭 라벨을 붙이지 말고 검수 대상으로 보낸다.

## 선택 배열

```json
{
  "selection": [
    {"question_id": "Q017", "new_number": 1},
    {"question_id": "Q018", "new_number": 2}
  ]
}
```

- `question_id`는 중복될 수 없다.
- `new_number`는 배열 순서대로 정확히 `1..N`이다.
- 문제, 정답표, 해설은 같은 대응표를 쓴다.
- 같은 Bundle이 연속되면 공통 지문을 한 번만 배치한다.
- 같은 Bundle이 떨어졌다가 다시 나오면 각 연속 run에 지문을 다시 배치한다.
- 범위 라벨은 각 연속 run의 새 번호만 사용한다.

## 결정론적 선택 감사 정보

`select-manifest` 결과의 `selection_audit`에 다음을 남긴다.

- 원본 catalog 해시 또는 버전
- 필터와 제외 목록
- bundle 정책
- seed
- 후보 수와 선택 수
- 선택된 question/bundle ID
- 요청 수를 정확히 충족했는지 여부

`whole-bundle`에서 일부 문항만 필터에 맞거나 정확한 요청 수를 만들 수 없으면 부분
결과를 쓰지 않는다.

