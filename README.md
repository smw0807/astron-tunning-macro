# 아스트로엔 아이템 개조 매크로

BlueStacks 인스턴스에 ADB로 접속해서 `com.ctugames.astron` 의 아이템을
**슬롯별로 구매 → 개조로 목표 레벨까지 올리기 → 다음 슬롯**으로 자동 반복한다.
결과 판정은 화면 텍스트 OCR(한국어).

```
개조매크로.bat        (또는)   python -m src.gui
```

## 전체 흐름

```
슬롯 1 …… 슬롯 N (slots.target_count):
  ├─ 구매 (buy_sequence)  ─ 골드/가방 부족이면 중단
  ├─ 개조 반복 (attempt_sequence):
  │    성공 → 레벨 +1, 목표 레벨 도달하면 다음 슬롯
  │    실패 → 연속 실패 카운트
  │      └ 연속 실패 == fail_limit(3) → 이 아이템은 막힘
  └─ 막힘 → 판매 (sell_sequence) → 같은 슬롯 재구매
모든 슬롯 완료 → 종료 (알림음)
```

중단: 목표 슬롯 수 완료 · `max_attempts` 초과 · `min_gold` 미만 · 구매 실패 ·
정지 버튼 · (설정 시) 결과 문구 인식 실패.

## GUI 사용 흐름

1. 상단 ADB 경로/인스턴스명 확인 → **연결/새로고침**. 왼쪽에 게임 화면(960×540).
2. 왼쪽 화면에서 **클릭 = 좌표**, **드래그 = 영역**.
3. `슬롯 / 구매` 탭:
   - 채울 슬롯 수, 각 슬롯 아이템 좌표(클릭 후 `← 선택 좌표`)
   - 구매 시퀀스 (구입탭 → 카테고리 → 아이템 → 구입 → 확인)
   - 판매 시퀀스 (판매탭 → `tap_slot` → 판매 → 확인)
   - 구매 실패 키워드
4. `개조 시퀀스` 탭: 개조탭 → `tap_slot`(현재 슬롯 아이템) → 개조버튼 → 확인
5. `결과 판정` 탭: 성공/실패/개조불가 키워드, 결과 메시지 영역, 팝업 닫기 시퀀스
6. `개조 규칙` 탭: 목표 레벨, 연속 실패 한계, 레벨 인식 영역
7. `OCR 테스트` 탭으로 각 영역이 문구/레벨을 읽는지 확인
8. `설정 저장` → `실행` 탭에서 **DRY-RUN** 점검 후 **시작**

## 구성

| 파일 | 역할 |
|---|---|
| `config.yaml` | 전체 설정 (GUI 저장 시 재생성) |
| `src/gui.py` | 설정 GUI + 매크로 실행/로그/OCR 테스트 |
| `src/macro.py` | 슬롯 순회 + 개조 루프 (`Macro` 클래스, CLI 겸용) |
| `src/adb.py` | HD-Adb.exe 래퍼 (connect / tap / swipe / screencap) |
| `src/ocr.py` | RapidOCR(한글) 래퍼 + 키워드 검색 |
| `src/runner.py` | config 로드/저장 + 시퀀스 실행기 (`tap_slot` 처리) |
| `tools/calibrate.py` | (CLI) 스크린샷 + 좌표격자 + OCR 덤프 |
| `tools/tap.py` | (CLI) 단발 탭/스와이프/시퀀스 테스트 |
| `models/` | 한국어 인식 모델 + 사전 |

## 설치

```
pip install -r requirements.txt
```

한국어 OCR 모델이 없다면:
```
curl -sL -o models/korean_rec.onnx  "https://huggingface.co/spaces/RapidAI/RapidOCR/resolve/63dfa2a06aff8237d6a27c0007390c55f5fe59a3/models/text_rec/korean_mobile_v2.0_rec_infer.onnx"
curl -sL -o models/korean_dict.txt  "https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/release/2.7/ppocr/utils/dict/korean_dict.txt"
```

## 시퀀스 스텝 종류

`buy_sequence` / `sell_sequence` / `attempt_sequence` / `result.*_sequence` 에서 사용:

| 스텝 | 의미 |
|---|---|
| `{tap: [x, y]}` | 좌표 탭 |
| `{tap_slot: true}` | **현재 처리 중인 슬롯의 아이템 좌표**를 탭 |
| `{swipe: [x1,y1,x2,y2,ms]}` | 드래그 |
| `{wait: 초}` | 대기 |
| `{key: 코드}` | 키 이벤트 (4 = 뒤로가기) |
| `{tap_if_text: {text: "확인", region: [x1,y1,x2,y2]}}` | 영역에 텍스트 있으면 그 위치 탭 |
| `{wait_text: {text: "취소", region: […], timeout: 3, required: true}}` | 영역에 텍스트 뜰 때까지 대기. `required` 면 안 뜰 때 시퀀스 중단(개조 미실행으로 보고 재시도) |

## 개조 규칙 (`config.yaml` › `modify`)

- 아이템 레벨 1~8, 개조 성공 1회 = 레벨 +1
- `target_level` — 슬롯 아이템을 이 레벨까지 올리면 그 슬롯 완료
- `start_level` — 새 아이템 시작 레벨
- `level_region` / `level_pattern` — 매 성공 후 `level_region` OCR 로 현재 레벨 확인.
  못 읽으면 `start_level + 성공횟수` 로 추정
- `fail_limit` — 연속 실패가 이 횟수(기본 3)면 그 아이템 판매 후 재구매

## 실행 (CLI)

```
python -m src.macro --dry-run     # 입력 없이 흐름만 확인
python -m src.macro --max 100     # 최대 시도 override
python -m src.macro               # config 대로
```

매 시도 결과 스크린샷은 `captures/run_*/` 에 저장.
