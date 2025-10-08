@echo off
rem --- DougaMeijin Debug Runner (Log to Desktop) ---

set "LOG_PATH=%USERPROFILE%\Desktop"

echo DougaMeijinを起動します...
echo ログはデスクトップに出力されます:
echo %LOG_PATH%\stdout.txt
echo %LOG_PATH%\stderr.txt

"%ProgramFiles%\DougaMeijin\DougaMeijin.exe" > "%LOG_PATH%\stdout.txt" 2> "%LOG_PATH%\stderr.txt"

echo アプリケーションが終了しました。
echo デスクトップのログファイルを確認してください。
pause