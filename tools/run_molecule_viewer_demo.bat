@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."
set "PYTHONPATH=%PROJECT_ROOT%\src"

py -c "import keyring; import paramiko; import PySide6; import vtkmodules; from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor" >nul 2>&1
if errorlevel 1 (
    echo Required Moltage dependencies are unavailable.
    echo Install the project's declared dependencies with:
    echo     py -m pip install keyring paramiko PySide6 vtk
    endlocal & exit /b 1
)

py "%~dp0molecule_viewer_demo.py"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
