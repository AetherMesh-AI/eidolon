@echo off
rem Hermes Agent Gateway - Messaging Platform Integration
cd /d "@HOME@"
set "HERMES_HOME=@HOME@"
set "VIRTUAL_ENV=@VENV@"
set "PYTHONPATH=@PYTHONPATH@;%PYTHONPATH%"
"@PYTHON@" -m @MODULE@ @PROFILE@gateway run
exit /b 0
