@echo off
rem WorkBuddy / CodeBuddy hook launcher for Windows: membrain-hook <event>. Never fails the host.
setlocal
if "%MEMBRAIN_PACKAGE%"=="" set "MEMBRAIN_PACKAGE=membrain-ai[embedded,semantic] @ https://github.com/chriswu727/membrain/archive/defc3c5cc3cac7f63661b6e8c0ece982f08bbb91.zip"
where uvx >nul 2>nul || exit /b 0
if "%~1"=="start" (
  start "" /b cmd /c "uv tool install --quiet --python 3.12 "%MEMBRAIN_PACKAGE%" >nul 2>nul & uvx --offline --python 3.12 --from membrain-ai membrain plugin start >nul 2>nul"
  exit /b 0
)
uvx --offline --python 3.12 --from membrain-ai membrain plugin hook %1 --host workbuddy 2>nul
exit /b 0
