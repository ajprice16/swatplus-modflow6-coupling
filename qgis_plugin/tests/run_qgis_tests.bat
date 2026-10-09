@echo off
rem Run the headless QGIS tests, or capture GUI screenshots, with the Python of a Windows QGIS install.
rem
rem   run_qgis_tests.bat "C:\Program Files\QGIS 3.44.15"                 run the tests
rem   run_qgis_tests.bat "C:\OSGeo4W"                                     OSGeo4W installs work too
rem   run_qgis_tests.bat "C:\Program Files\QGIS 4.2.3" capture OUTFOLDER   screenshots + OUTFOLDER\index.html
rem
rem Point several QGIS versions at the same capture folder to compare them side by side.
rem Tests use a throw-away profile folder, never your QGIS profile.
setlocal EnableExtensions
set "QGIS_ROOT=%~1"
if "%QGIS_ROOT%"=="" (
  echo usage: %~nx0 "QGIS install folder" [capture OUTFOLDER]
  exit /b 2
)
set "PYQGIS="
for %%B in (python-qgis-ltr.bat python-qgis.bat python-qgis-qt6.bat python-qgis-dev.bat) do (
  if not defined PYQGIS if exist "%QGIS_ROOT%\bin\%%B" set "PYQGIS=%QGIS_ROOT%\bin\%%B"
)
if not defined PYQGIS (
  echo No python-qgis*.bat found in "%QGIS_ROOT%\bin"
  exit /b 2
)
if not defined QT_QPA_PLATFORM set "QT_QPA_PLATFORM=offscreen"
set "PYTHONDONTWRITEBYTECODE=1"
cd /d "%~dp0qgis"
echo Using %PYQGIS%
if /i "%~2"=="capture" (
  if "%~3"=="" (
    echo usage: %~nx0 "QGIS install folder" capture OUTFOLDER
    exit /b 2
  )
  call "%PYQGIS%" capture_gui.py --out "%~3"
) else (
  call "%PYQGIS%" run_unittest.py
)
exit /b %ERRORLEVEL%
