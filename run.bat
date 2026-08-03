@echo off
title Arty - Trading Bot Forex
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
pause
