# Exam Paper Builder


로컬에 있는 문제지·답지 PDF를 분석하고, 지문·문항·정답·해설을 서로 연결된 자산으로
분리한 뒤, 선택한 문항만 새 번호로 다시 조판하는 Codex 스킬입니다.

문제를 단순히 한 장씩 붙이는 대신 **단 너비의 가상 연속 스트립**에 먼저 이어 놓고
안전한 행간에서 나눕니다. 긴 지문이나 문제가 왼쪽 단의 끝을 넘으면 오른쪽 단으로,
다시 넘으면 다음 페이지로 이어집니다. 글자·수식·표·그림을 임의로 절단하지 않습니다.

> 이 저장소에는 시험 문제, 해설, 문항 이미지, 글꼴 또는 생성된 문제집이 포함되지
> 않습니다. 이용 권한이 있는 로컬 파일만 입력으로 사용하세요.

## 주요 기능

- 문제 PDF와 답지 PDF의 구조를 서로 따로 분석
- 텍스트 PDF, 스캔 PDF, 혼합 PDF 진단 및 검수 큐 생성
- 공통 지문, 개별 문항, 정답, 해설을 별도 파일로 보존
- `bundle_id`와 JSON 참조로 지문-문항-해설 관계 유지
- 국어·영어 실무 유형 분류와 다른 과목의 단원별 분류
- 필터와 seed를 기록하는 재현 가능한 문항 선택
- 원문 번호를 새 시험지 순서에 맞게 문제·정답표·해설에서 함께 변경
- A3, A4, B4-JIS, B4-ISO, 4×6판 8절, 국전 8절 출력
- 문제지 PDF와 정답·해설 PDF를 각각 생성
- 전 페이지 렌더링, 빈 페이지, 잘림, 겹침, 용지 크기, 번호 연결 검증

## 동작 흐름

| 단계 | 입력 | 결과 |
|---|---|---|
| 1. 진단 | 로컬 문제·답지 PDF | 페이지 크기, 회전, 텍스트/OCR, 단 구성 보고서 |
| 2. 구조 제안 | 진단된 PDF | 문항·공통 지문·해설 crop 제안과 검수 미리보기 |
| 3. 자산화 | 승인된 crop | Fragment, Stimulus, Question, AnswerEntry, Solution |
| 4. 분류 | 자산과 발문 | 유형·단원·형식 태그와 신뢰도 |
| 5. 선택 | 자연어 조건 또는 필터 | 재현 가능한 `selection-manifest.json` |
| 6. 조판 | 선택 manifest | 새 번호의 문제지 PDF와 답지 PDF |
| 7. 검수 | 생성 PDF | 전 페이지 PNG와 `composition-report.json` |

## 설치

### Codex에서 GitHub 저장소로 설치

공개 저장소를 만든 뒤 Codex에서 `$skill-installer`를 호출하고 다음처럼 요청합니다.

```text
https://github.com/SWERVY1/exam-paper-builder 저장소의 스킬을 설치해줘.
```

설치 후 스킬이 바로 보이지 않으면 Codex를 다시 시작합니다. 이 저장소는 루트에
`SKILL.md`가 있는 독립 스킬 구조입니다.

### 로컬 실행 도구 설치

Windows PowerShell 기준입니다.

```powershell
git clone https://github.com/SWERVY1/exam-paper-builder.git
cd exam-paper-builder
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install -e .
.\.venv\Scripts\exam-split.exe --version
```

한국어 콘솔 출력이 깨지면 현재 세션에 다음 값을 설정합니다.

```powershell
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
```

## 가장 빠른 체험

저작권 자료 없이 동작을 확인할 수 있는 합성 자산과 생성기가 포함되어 있습니다.

```powershell
.\.venv\Scripts\python.exe examples\make_demo_assets.py
.\.venv\Scripts\exam-split.exe compose-exam `
  examples\demo-manifest.json --output output\demo
```

합성 이미지와 manifest는 저장소에 함께 들어 있으며, 생성기로 언제든 다시 만들 수
있습니다. 실제 시험 문제는 포함하거나 내려받지 않습니다.

## Codex에 요청하는 예시

```text
$exam-paper-builder로 이 폴더의 문제지와 답지를 분석해줘.
공통 지문은 문항과 분리하고, 국어 독서 12문항을 seed 2401로 골라
B4-JIS 2단 문제지와 정답+해설지를 만들어줘.
```

```text
$exam-paper-builder로 영어 문제를 어법·어휘·빈칸·순서 유형별로 분류해줘.
아직 PDF는 만들지 말고 검수할 샘플과 분류 신뢰도만 보여줘.
```

```text
$exam-paper-builder로 A4 1단 시험지를 만들어줘.
원래 17번인 문항이 새 시험지의 첫 문항이면 문제와 해설 모두 1번으로 바꿔줘.
```

PDF를 만들기 직전에는 스킬이 각 문항 위의 출처 표기를 반드시 묻습니다.

- 표시 안 함
- 연도만
- 연도+월

이 선택이 없으면 조판을 시작하지 않습니다.

## 연속 스트립 조판

```text
지문 F01 → 지문 F02 → 문제 1 → 문제 2 → 문제 3 → …
                 │ 단 높이 H │ 단 높이 H │ 단 높이 H │
                   1쪽 왼쪽     1쪽 오른쪽   2쪽 왼쪽
```

각 자산에는 세 가지 절단 정책을 둘 수 있습니다.

- `flow`: 승인된 분할점이나 충분한 가로 흰 띠에서 다음 단으로 이어 붙임
- `safe-only`: manifest에 적힌 승인 지점에서만 분할
- `keep-together`: 표·그림·수식 묶음처럼 한 덩어리로 다음 단에 넘김

`protected_ranges`는 표, 그래프, 선택지, 수식 행처럼 절단하면 안 되는 구간을
보호합니다. 안전점이 없고 축소 한도도 넘으면 결과를 억지로 만들지 않고 실패합니다.

## 지원 용지

| 이름 | 크기 | 기본 단 수 |
|---|---:|---:|
| A3 | 297×420 mm | 2 |
| A4 | 210×297 mm | 2, 가독성 우선 시 1 |
| B4-JIS | 257×364 mm | 2 |
| B4-ISO | 250×353 mm | 2 |
| 8JEOL | 272×394 mm | 2 |
| GUKJEON-8JEOL | 234×318 mm | 2 |

`B4`는 B4-JIS의 별칭입니다. `8절`은 이 프로젝트에서 4×6판 8절을 뜻하며,
국전 8절은 별도 프로필입니다. 실제 인쇄소 규격이 다르면 먼저 재단 크기를 확인하세요.

## 결과 폴더

```text
output/<build_id>/
├─ <build_id>_problems_<paper>.pdf
├─ <build_id>_answers_<paper>.pdf
├─ selection-manifest.json
├─ composition-report.json
└─ qa/
   ├─ problems/
   └─ answers/
```

같은 `build_id`는 기본적으로 덮어쓰지 않습니다. 최종 결과는 임시 폴더에서 PDF와
검수 자료를 모두 만든 뒤 한 번에 전환합니다.

## 현재 한계

- 자동 crop은 제안 단계입니다. 처음 보는 형식은 사람이 대표 샘플을 승인해야 합니다.
- OCR 결과만으로 문항 경계를 확정하지 않습니다.
- 복잡한 배경 위 원번호, 서답형 수식 정답, 번호가 재시작되는 선택과목은 추가 검수가
  필요합니다.
- 기본 한국어 글꼴 탐색은 Windows에 맞춰져 있습니다. 다른 운영체제에서는 manifest의
  `font_path`에 한국어 TrueType 글꼴을 직접 지정해야 합니다.
- 이 스킬은 다운로드, 로그인 우회, 원본 재호스팅, 공개 게시 기능을 제공하지 않습니다.

## 개발과 검증

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

공개 배포 방법과 체크리스트는 [DISTRIBUTION.md](DISTRIBUTION.md), 기여 규칙은
[CONTRIBUTING.md](CONTRIBUTING.md)를 참고하세요.

## 라이선스와 자료 권리

코드와 이 저장소의 문서는 [MIT License](LICENSE)로 배포합니다. MIT 라이선스는
사용자가 입력하는 문제지·해설지의 이용 권한을 부여하지 않습니다. 입력 자료와 생성된
결과물의 복제·배포 가능 여부는 사용자가 별도로 확인해야 합니다.
