@echo off
chcp 65001 >nul
cd /d "%~dp0app"
echo.
echo  ================================================
echo   참고문헌 표준화·검증 에이전트 서버를 시작합니다
echo   잠시 후 브라우저가 자동으로 열립니다.
echo   종료하려면 이 창에서 Ctrl+C 를 누르세요.
echo  ================================================
echo.
start "" cmd /c "timeout /t 3 >nul & start http://localhost:8765"
python -m uvicorn main:app --host 127.0.0.1 --port 8765
