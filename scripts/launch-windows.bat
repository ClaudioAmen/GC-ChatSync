@echo off
rem ==============================================================================
rem GC-ChatSync Windows Launcher
rem Opens GUI if launched without args, or runs CLI wrapper if args are provided
rem ==============================================================================

setlocal

set ACTION=%1
if "%ACTION%"=="" set ACTION=gui

rem 1. Check Python 3.14 Scripts direct path
if exist "%LOCALAPPDATA%\Programs\Python\Python314\Scripts\gc-chatsync.exe" (
    if "%ACTION%"=="gui" (
        start "" "%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe" -m gc_chatsync.cli gui
    ) else (
        "%LOCALAPPDATA%\Programs\Python\Python314\Scripts\gc-chatsync.exe" %*
    )
    goto :eof
)

rem 2. Check gc-chatsync in PATH
where gc-chatsync >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    if "%ACTION%"=="gui" (
        start "" gc-chatsync gui
    ) else (
        gc-chatsync %*
    )
    goto :eof
)

rem 3. Check py launcher
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set SCRIPT_DIR=%~dp0..
    set PYTHONPATH=%SCRIPT_DIR%\src;%PYTHONPATH%
    if "%ACTION%"=="gui" (
        start "" py -m gc_chatsync.cli gui
    ) else (
        py -m gc_chatsync.cli %*
    )
    goto :eof
)

rem 4. Fallback to python
set SCRIPT_DIR=%~dp0..
set PYTHONPATH=%SCRIPT_DIR%\src;%PYTHONPATH%

if "%ACTION%"=="gui" (
    start "" python -m gc_chatsync.cli gui
) else (
    python -m gc_chatsync.cli %*
)

endlocal
