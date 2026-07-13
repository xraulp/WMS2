#!/usr/bin/env bash
# build.sh - Script de construcción para Render

echo "-----> Installing Python dependencies..."
pip install -r requirements.txt

echo "-----> Running Django migrations..."
python manage.py migrate

echo "-----> Collecting static files..."
python manage.py collectstatic --noinput
