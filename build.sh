#!/usr/bin/env bash
# build.sh - Script de construcción para Render

echo "-----> Installing Python dependencies..."
pip install -r requirements.txt

echo "-----> Running migrations..."
python manage.py migrate

echo "-----> Loading initial data (skipped - solo se usa manualmente para carga inicial)..."
# python manage.py loaddata data_clean.json
# NOTA: Este paso se desactivó porque sobrescribía datos reales de producción
# (usuarios, catálogo, operaciones) en cada deploy con el snapshot de data_clean.json.
# Si alguna vez necesitas recargar ese fixture a propósito, córrelo manualmente
# desde la Shell de Render: python manage.py loaddata data_clean.json

echo "-----> Creating superuser..."
python create_superuser.py

echo "-----> Compiling translations..."
# Compilador propio: `compilemessages` llama a msgfmt, un binario de GNU
# gettext que no tiene por que estar en el contenedor. Los .mo estan ademas
# versionados, asi que un fallo aqui no deja la interfaz sin traducir.
python scripts/compilar_traducciones.py

echo "-----> Collecting static files..."
python manage.py collectstatic --noinput
