#!/usr/bin/env bash
# build.sh - Script de construcción para Render

#!/usr/bin/env bash

echo "-----> Installing Python dependencies..."
pip install -r requirements.txt

echo "-----> Exporting DATABASE_URL..."
export DATABASE_URL="${DATABASE_URL}"   # <--- Agrega esta línea

echo "-----> Running Django migrations..."
python manage.py migrate

echo "-----> Loading initial data..."
python manage.py loaddata data.json

echo "-----> Creating superuser if not exists..."
python create_superuser.py

echo "-----> Collecting static files..."
python manage.py collectstatic --noinput