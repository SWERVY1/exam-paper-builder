# 합성 데모

이 폴더의 데모는 실제 시험 자료를 포함하지 않습니다.

```powershell
python examples/make_demo_assets.py
exam-split compose-exam examples/demo-manifest.json --output output/demo
```

저장소에는 `examples/demo-assets`와 `examples/demo-manifest.json`이 이미 포함되어
있습니다. 첫 명령은 합성 fixture를 같은 내용으로 다시 생성합니다. 두 번째 명령은 A4
2단 합성 문제지, 답지, 조판 보고서, 전 페이지 QA PNG를 만듭니다. 생성된 PDF와
`output` 폴더는 `.gitignore` 대상입니다.
