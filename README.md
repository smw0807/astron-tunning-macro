# 아스트론 아이템 개조 매크로

BlueStacks 인스턴스에 ADB로 접속해서 `com.ctugames.astron` 의 아이템 개조를
**성공할 때까지 자동 반복**한다. 결과 판정은 화면 텍스트 OCR(한국어).

**GUI 로 좌표를 찍고 설정한 뒤 그 자리에서 실행**하는 것을 권장한다:

```
개조매크로.bat        (또는)   python -m src.gui
```

GUI 사용 흐름:
1. 상단에서 ADB 경로/인스턴스명 확인 → **연결/새로고침**. 왼쪽에 게임 화면이 뜬다.
2. 왼쪽 화면에서 **클릭 = 좌표**, **드래그 = 영역**.
3. `개조 시퀀스` 탭에서 `＋선택좌표 tap` 으로 아이템 클릭·개조 버튼을 순서대로 추가.
4. `결과 판정` 탭에서 성공/실패 키워드 입력, `선택 영역 적용`으로 결과 메시지 영역 지정.
5. `OCR 테스트` 탭으로 영역이 문구를 잘 읽는지 확인.
6. `설정 저장` → `실행` 탭에서 **DRY-RUN** 으로 흐름 점검 후 **시작**.

## 구성

| 파일 | 역할 |
|---|---|
| `config.yaml` | ADB 경로/인스턴스, 좌표, 대기시간, 안전장치, 개조 시퀀스, 결과 키워드 |
| `src/gui.py` | 설정 GUI + 매크로 실행/로그/OCR 테스트 |
| `src/adb.py` | HD-Adb.exe 래퍼 (connect / tap / swipe / screencap) |
| `src/ocr.py` | RapidOCR(한글) 래퍼 + 키워드 검색 |
| `src/runner.py` | config 로드/저장 + 시퀀스 실행기 |
| `src/macro.py` | 개조 반복 루프 (`Macro` 클래스, CLI 겸용) |
| `tools/calibrate.py` | (CLI) 스크린샷 + 좌표격자 + OCR 덤프 |
| `tools/tap.py` | (CLI) 단발 탭/스와이프/시퀀스 테스트 |
| `models/` | 한국어 인식 모델(`korean_rec.onnx`) + 사전(`korean_dict.txt`) |

## 설치

```
pip install -r requirements.txt
```

한국어 OCR 모델이 없다면:
```
curl -sL -o models/korean_rec.onnx  "https://huggingface.co/spaces/RapidAI/RapidOCR/resolve/63dfa2a06aff8237d6a27c0007390c55f5fe59a3/models/text_rec/korean_mobile_v2.0_rec_infer.onnx"
curl -sL -o models/korean_dict.txt  "https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/release/2.7/ppocr/utils/dict/korean_dict.txt"
```

## 캘리브레이션 (최초 1회)

좌표는 모두 **960x540 캡처 기준 픽셀**이다.

1. 게임을 개조 가능한 화면(무기상점 등)에 둔다.
2. `python -m tools.calibrate` — `captures/cal_grid.png`(좌표격자)와 OCR 덤프 확인.
3. 개조를 **수동으로 1회** 진행하면서 각 단계를 캡처:
   - `개조` 탭 위치
   - 개조할 아이템 클릭 위치 (인벤토리 슬롯)
   - `개조` 실행 버튼 위치
   - 확인 팝업의 `예`/`확인` 버튼 위치와 영역
   - **성공** 메시지 문구와 위치 → `result.success_keywords`
   - **실패** 메시지 문구와 위치 → `result.fail_keywords`
   - 실패 팝업 닫는 버튼 → `result.dismiss_sequence`
4. `config.yaml` 의 `attempt_sequence`, `result` 를 채운다.
5. `python -m tools.tap seq` 로 시퀀스 1회만 실행해 검증.

## 실행 (CLI)

GUI 대신 커맨드로도 실행할 수 있다.

```
python -m src.macro --dry-run      # 입력 없이 판정 흐름만 확인
python -m src.macro --max 50       # 실제 실행, 최대 50회
python -m src.macro                # config 의 max_attempts 까지
```

성공 시 알림음 후 중단. 매 시도의 결과 스크린샷은 `captures/run_*/` 에 저장.

## 안전장치 (`config.yaml` › `safety`)

- `max_attempts` — 초과 시 중단
- `min_gold` — 골드가 이보다 적으면 중단 (0 = 검사 안 함)
- `stop_on_unknown_screen` — 성공/실패 문구를 못 읽으면 중단 (오작동 방지)
