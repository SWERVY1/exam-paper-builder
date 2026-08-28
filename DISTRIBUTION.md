# 공개 배포 가이드

이 디렉터리 자체가 공개 GitHub 저장소의 루트입니다. 상위 개발 프로젝트 전체가 아니라
`public-release/exam-paper-builder` **안의 파일만** 공개하세요.

## 공개 구성

```text
exam-paper-builder/
├─ SKILL.md                 스킬 진입점
├─ agents/openai.yaml       Codex 표시 정보
├─ src/                     실행 코드
├─ references/              필요할 때 읽는 세부 지침
├─ examples/                저작권 자료가 없는 합성 데모
├─ tests/                   합성·단위 테스트
├─ assets/                  공개 소개 이미지 1개
├─ .github/workflows/       Windows CI
├─ README.md
├─ CONTRIBUTING.md
├─ SECURITY.md
├─ CHANGELOG.md
├─ LICENSE
└─ pyproject.toml
```

포함하지 않는 항목:

- 실제 문제지·답지 PDF
- 문항·해설 crop 이미지
- 생성된 문제집과 QA PNG
- 사설 글꼴 파일
- 개인 경로, 원본 파일 목록, 내부 통계 보고서
- 다운로드·로그인 우회·재호스팅 코드

## GitHub에 처음 올리기

GitHub에서 빈 공개 저장소 `exam-paper-builder`를 만든 뒤 다음을 실행합니다. 저장소를
만들 때 GitHub의 README와 License 자동 생성을 선택하지 마세요. 이 폴더에 이미 있습니다.

```powershell
cd <PATH-TO-RELEASE>\exam-paper-builder
git init
git add .
git status
git commit -m "Initial public release v0.5.0"
git branch -M main
git remote add origin https://github.com/SWERVY1/exam-paper-builder.git
git push -u origin main
```

`git status`에서 PDF, WebP, 입력 폴더, `output`, `work`, `private`가 보이면 커밋하지
말고 원인을 확인합니다.

## 배포 직전 검사

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\pip.exe install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe examples\make_demo_assets.py
.\.venv\Scripts\exam-split.exe compose-exam `
  examples\demo-manifest.json --output output\release-smoke
```

다음 문자열이 공개 파일에 남아 있지 않은지도 검사합니다.

```powershell
rg -n "C:\\Users|AppData|PRIVATE|BEGIN (RSA|OPENSSH) PRIVATE KEY" .
```

예상 가능한 `DISTRIBUTION.md`의 검사 예시 문구를 제외하고 실제 개인 경로나 비밀 값이
나오면 배포를 중단합니다.

## 권장 GitHub 설정

- About: `Turn local exam PDFs into verified, renumbered mock exams.`
- Topics: `codex-skill`, `exam`, `pdf`, `worksheet`, `korean`, `education`
- Issues와 Discussions: 공개 지원을 받을 계획이 있을 때만 활성화
- Security advisories: 활성화
- 기본 브랜치 보호: CI 통과 후 병합
- Releases: `v0.5.0`처럼 코드 버전과 맞춘 태그 사용

## 설치 안내 범위

GitHub 저장소의 독립 스킬은 Codex 데스크톱, CLI, IDE에서 로컬 설치·실험용으로 쓸 수
있습니다. 더 넓은 설치 배포나 ChatGPT 웹·모바일의 공용 플러그인 디렉터리를 목표로
한다면 다음 단계에서 별도 플러그인 패키지로 감싸야 합니다. 현재 공개 폴더는 스킬
소스 배포이며 플러그인 제출물이 아닙니다.

## 새 버전 체크리스트

- `SKILL.md`의 설명과 실제 기능이 일치한다.
- `pyproject.toml`, `src/exam_image_splitter/__init__.py`, `CHANGELOG.md` 버전이 같다.
- 새 기능에 합성 테스트가 있다.
- 공개 테스트가 통과한다.
- 합성 데모의 문제지·답지와 전 페이지 QA가 통과한다.
- 실제 시험 자료와 개인 경로가 포함되지 않았다.
- README의 지원 용지·한계·예시가 현재 코드와 일치한다.
- GitHub Actions가 통과한 뒤 release 태그를 만든다.
