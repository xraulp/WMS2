@echo off
rem Respaldo semanal del WMS a un disco local. Lo corre el Programador de tareas
rem de Windows; tambien se puede correr con doble clic.
rem
rem Cambia DESTINO si el respaldo debe ir a otro disco (de preferencia uno
rem externo o de red, no el mismo de la PC).

set DESTINO=%USERPROFILE%\Respaldos WMS

cd /d "%~dp0.."
if not exist "%DESTINO%" mkdir "%DESTINO%"
"venv\Scripts\python.exe" manage.py respaldo_local --destino "%DESTINO%" >> "%DESTINO%\respaldo.log" 2>&1
echo ---- %DATE% %TIME% (codigo %ERRORLEVEL%) >> "%DESTINO%\respaldo.log"
