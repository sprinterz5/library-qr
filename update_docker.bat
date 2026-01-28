@echo off
echo Pulling latest changes...
git pull

echo.
echo Rebuilding Docker container...
docker-compose up -d --build

echo.
echo Done! Application updated.
pause
