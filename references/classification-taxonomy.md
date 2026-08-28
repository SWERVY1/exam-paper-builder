# 과목별 유형·단원 분류

문항을 폴더 하나에만 넣지 말고 검색·균형 선발·교육과정 버전을 함께 표현하는 필드를
사용한다.

```json
{
  "taxonomy_version": "korean-exam-v1",
  "primary_category": "reading",
  "subtype": "science_technology",
  "unit_path": ["독서", "과학·기술"],
  "skill_tags": ["내용추론", "자료적용"],
  "format_tags": ["shared_passage", "five_choice", "figure"],
  "confidence": 0.91,
  "review_status": "approved"
}
```

## 분류 순서

1. 파일명과 표지에서 과목, 시험 구간, 교육과정 버전을 확인한다.
2. Stimulus와 Bundle의 장르·단원을 먼저 분류한다.
3. 개별 발문, 보기, 자료를 보고 Question의 세부 유형과 기능 태그를 붙인다.
4. 답지의 출제 의도나 단원명은 보조 근거로만 사용한다.
5. 원번호만으로 유형을 확정하지 않는다.
6. 근거가 충돌하면 복수 보조 태그와 `needs_review`를 사용한다.

권장 기본 정책은 `0.90` 이상 자동 후보, `0.70~0.90` 검수 권장, `0.70` 미만 검수
필수다. 실제 형식군 파일럿 정확도에 따라 임계값을 조정한다.

## 국어

| 1차 분류 | 세부 예시 |
|---|---|
| 독서(비문학) | 인문, 사회, 과학, 기술, 예술, 주제 통합 |
| 문학 | 현대시, 고전시가, 현대소설, 고전소설, 극, 수필, 갈래 복합 |
| 화법 | 발표, 연설, 토의, 토론, 대화, 면접 |
| 작문 | 정보 전달, 설득, 보고서, 건의문, 고쳐쓰기 |
| 언어(문법) | 음운, 단어, 문장, 담화, 의미, 국어사, 규범 |
| 매체 | 매체 언어, 자료 수용·생산, 복합 양식, 비판적 이해 |

기능 태그 예시:

- 내용 일치/불일치
- 주제·제목·핵심 정보
- 세부 정보 추론
- 관점·태도·서술 방식
- 구체적 사례 적용
- 자료 해석
- 어휘의 문맥적 의미
- 표현상 특징과 작품 비교
- 문법 개념 적용

공통 지문의 1차 분류는 Bundle에 두고 개별 Question은 이를 상속한 뒤 발문 기능 태그를
추가한다.

## 영어

| `primary_category` | 포함 유형 |
|---|---|
| `listening` | 듣기 전 유형 |
| `purpose_tone` | 목적, 심경·분위기, 관계, 장소, 할 일 |
| `main_idea` | 주장, 요지, 주제, 제목 |
| `factual` | 도표, 안내문, 내용 일치·불일치 |
| `grammar` | 어법 |
| `vocabulary` | 어휘, 문맥상 낱말 쓰임 |
| `blank` | 빈칸 추론 |
| `coherence` | 무관한 문장, 글의 순서, 문장 삽입 |
| `summary` | 요약문 완성 |
| `long_reading` | 장문 2문항 이상 묶음 |

순서와 삽입을 같은 `coherence` 아래 두더라도 `subtype`은 `order`와 `insertion`으로
구분한다. 장문 묶음은 Stimulus를 하나로 저장하고 문항마다 별도 기능 태그를 붙인다.

## 수학·사회·과학·한국사·직업탐구

`unit_path`를 해당 시험 시점의 교육과정 기준으로 기록한다.

```json
{
  "taxonomy_version": "local-curriculum-v1",
  "primary_category": "unit",
  "unit_path": ["대단원", "중단원", "소단원"],
  "skill_tags": ["개념이해", "자료해석"],
  "format_tags": ["multiple_choice", "table"]
}
```

현재 교육과정 단원명을 과거 시험에 소급하지 않는다. 단원표가 없으면 사용자가 제공한
분류표 또는 시험 시점 자료를 확인하고 `taxonomy_version`을 고정한다.

## 형식 태그

조판과 필터에 다음 태그를 함께 쓸 수 있다.

- `shared_passage`
- `multi_page`
- `figure`, `table`, `graph`, `formula`
- `five_choice`, `short_answer`
- `listening_script`
- `section_restart`
- `ocr_review_required`
- `keep_together_required`

분류는 자산 crop과 별도 JSON에 저장한다. 폴더 이동만으로 관계나 분류 정보를 표현하지
않는다.

