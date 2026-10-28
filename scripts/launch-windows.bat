@echo off
rem ==============================================================================
rem GC-ChatSync Windows Launcher
rem Orchestrates Pull -> Antigravity Execution -> Push
rem ==============================================================================

setlocal

rem Check if gc-chatsync command is available in PATH
where gc-chatsync >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    gc-chatsync run %*
    goto :eof
)

rem Fallback to python module execution
set SCRIPT_DIR=%~dp0..
set PYTHONPATH=%SCRIPT_DIR%\src;%PYTHONPATH%
python -m gc_chatsync.cli run %*

endlocal
