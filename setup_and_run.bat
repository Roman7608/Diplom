@echo off
echo Updating project from git...
where git >nul 2>nul
if %errorlevel% equ 0 (
    git pull
) else (
    echo Git not found. Skipping update.
)

echo Creating virtual environment...
if exist venv (
    echo Virtual environment already exists. Skipping creation.
) else (
    python -m venv venv
    if errorlevel 1 (
        echo Failed to create virtual environment. Make sure Python is installed.
        pause
        exit /b 1
    )
)

echo Activating virtual environment...
call venv\Scripts\activate.bat

echo Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo ========================================
echo Setup complete!
echo ========================================
echo.
echo Make sure you have created .env file with:
echo   BOT_TOKEN=your_bot_token
echo   GIGACHAT_CLIENT_ID=your_client_id
echo   GIGACHAT_AUTH_KEY=your_auth_key
echo.
echo Starting bot...
echo.

python -m app.main

pause

