import sys

# Windows 콘솔(cp949)에서 한글 출력 시 UnicodeEncodeError 방지
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
