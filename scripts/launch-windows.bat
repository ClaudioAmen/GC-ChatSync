@echo off
rem ==============================================================================
rem GC-ChatSync Windows Launcher
rem Opens GUI if launched without args, or runs CLI wrapper if args are provided
rem ==============================================================================

setlocal

set ACTION=%1
if "%ACTION%"=="" set ACTION=gui

where gc-chatsync >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    if "%ACTION%"=="gui" (
        gc-chatsync gui
    ) else (
        gc-chatsync %*
    )
    goto :eof
)

set SCRIPT_DIR=%~dp0..
set PYTHONPATH=%SCRIPT_DIR%\src;%PYTHONPATH%

if "%ACTION%"=="gui" (
    python -m gc_chatsync.cli gui
) else (
    python -m gc_chatsync.cli %*
)

endlocal
