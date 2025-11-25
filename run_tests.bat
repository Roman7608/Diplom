@echo off
call venv\Scripts\activate.bat
set PYTHONPATH=.
python -m pytest tests/test_handlers.py -v -W ignore::DeprecationWarning
pause

