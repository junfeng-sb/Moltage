@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."
set "PYTHONPATH=%PROJECT_ROOT%\src"
py "%~dp0xyz_to_geometry_demo.py"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
