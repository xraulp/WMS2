#!/usr/bin/env bash
# build.sh - Script de construcción para Render

echo "-----> Installing Python dependencies..."
pip install -r requirements.txt

echo "-----> Running Django migrations..."
python manage.py migrate

echo "-----> Loading initial data..."          # ← Agrega esto
python manage.py loaddata data.json            # ← Agrega esto

echo "-----> Collecting static files..."
python manage.py collectstatic --noinput
