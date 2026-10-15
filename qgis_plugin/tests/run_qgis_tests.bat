@echo off
rem Run the headless QGIS tests, or capture GUI screenshots, with the Python of a Windows QGIS install.
rem
rem   run_qgis_tests.bat "QGIS folder"                              tests, every QGIS found in the folder
rem   run_qgis_tests.bat "QGIS folder" capture OUTFOLDER            screenshots + OUTFOLDER\index.html
rem   run_qgis_tests.bat "QGIS folder" --variant ltr [capture ...]  only one QGIS in the folder
rem
rem "QGIS folder" is a standalone install (for example C:\Program Files\QGIS 3.44.15) or an OSGeo4W root
rem (for example C:\OSGeo4W). An OSGeo4W root can hold several QGIS builds, each with its own launcher:
rem bin\python-qgis-ltr.bat (variant "ltr"), bin\python-qgis.bat ("qgis"), python-qgis-qt6.bat ("qt6"),
rem python-qgis-dev.bat ("dev"). Every launcher found is used in turn, each in a separate cmd process,
rem because the OSGeo4W launchers change PATH and the next build would otherwise load the wrong DLLs.
rem Capturing several builds into the same OUTFOLDER puts them side by side in index.html.
rem Tests use a throw-away profile folder, never your QGIS profile.
setlocal EnableExtensions EnableDelayedExpansion
set "QGIS_ROOT=%~1"
if "%QGIS_ROOT%"=="" goto :usage
if not exist "%QGIS_ROOT%\bin\" (
  echo "%QGIS_ROOT%\bin" does not exist
  exit /b 2
)
set "MODE=test"
set "OUT="
set "ONLY="
shift
:args
if "%~1"=="" goto :args_done
if /i "%~1"=="capture" (
  if "%~2"=="" goto :usage
  set "MODE=capture"
  set "OUT=%~2"
  shift
  shift
  goto :args
)
if /i "%~1"=="--variant" (
  if "%~2"=="" goto :usage
  set "ONLY=%~2"
  shift
  shift
  goto :args
)
echo unknown argument: %1
goto :usage
:args_done

if "%MODE%"=="capture" (
  rem absolute path: the scripts below run from tests\qgis
  for %%I in ("%OUT%") do set "OUT=%%~fI"
  if not exist "!OUT!\" mkdir "!OUT!"
)
if not defined QT_QPA_PLATFORM set "QT_QPA_PLATFORM=offscreen"
rem The offscreen platform finds no fonts on Windows unless told where they are.
if not defined QT_QPA_FONTDIR set "QT_QPA_FONTDIR=%WINDIR%\Fonts"
set "PYTHONDONTWRITEBYTECODE=1"
cd /d "%~dp0qgis"

set /a FOUND=0
set /a FAILED=0
set "FAILED_LIST="
set "RAN_LIST="
for %%B in ("%QGIS_ROOT%\bin\python-qgis*.bat") do (
  set "VAR=%%~nB"
  set "VAR=!VAR:python-qgis=!"
  if "!VAR!"=="" (set "VAR=qgis") else (if "!VAR:~0,1!"=="-" set "VAR=!VAR:~1!")
  set "USE=1"
  if defined ONLY if /i not "!VAR!"=="!ONLY!" set "USE=0"
  if "!USE!"=="1" call :run_one "%%~fB" "!VAR!"
)

if %FOUND%==0 (
  if defined ONLY (echo No launcher for variant "%ONLY%" in "%QGIS_ROOT%\bin") else (echo No python-qgis*.bat found in "%QGIS_ROOT%\bin")
  exit /b 2
)
echo.
echo Ran %FOUND% QGIS build(s):%RAN_LIST%
if "%MODE%"=="capture" echo Comparison page: %OUT%\index.html
if %FAILED% gtr 0 (
  echo FAILED:%FAILED_LIST%
  exit /b 1
)
echo All passed.
exit /b 0

:run_one
rem %1 = launcher .bat, %2 = variant name. A fresh cmd keeps the launcher's PATH changes out of this one.
set /a FOUND+=1
set "RAN_LIST=%RAN_LIST% %~2"
echo.
echo ===== %~2  (%~nx1) =====
if "%MODE%"=="capture" (
  cmd /d /c ""%~1" capture_gui.py --out "%OUT%""
) else (
  cmd /d /c ""%~1" run_unittest.py"
)
if errorlevel 1 (
  set /a FAILED+=1
  set "FAILED_LIST=%FAILED_LIST% %~2"
)
exit /b 0

:usage
echo usage: %~nx0 "QGIS install folder or OSGeo4W root" [--variant ltr^|qgis^|qt6^|dev] [capture OUTFOLDER]
exit /b 2
