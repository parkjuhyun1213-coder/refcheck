@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  ================================================
echo   참고문헌 에이전트 - 무료(규칙 기반) 모드
echo   Claude API를 호출하지 않아 비용이 들지 않습니다.
echo   종료하려면 이 창에서 Ctrl+C 를 누르세요.
echo  ================================================
echo.
start "" cmd /c "timeout /t 4 >nul & start http://localhost:8765"
python free_mode.py
echo.
pause
