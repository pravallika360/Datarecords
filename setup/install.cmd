@echo off
rem Moves the Claude Code config files into place. Run once from the Simple folder: setup\install.cmd
cd /d "%~dp0.."
if not exist .claude\agents mkdir .claude\agents
copy /Y setup\agents\*.md .claude\agents\ >nul
copy /Y setup\settings.json .claude\settings.json >nul
copy /Y setup\mcp.json .mcp.json >nul
echo Installed:
dir /b .claude\agents
echo .claude\settings.json
echo .mcp.json
