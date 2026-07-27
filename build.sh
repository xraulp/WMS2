#!/usr/bin/env bash
# build.sh - Script de construcción para Render

#!/usr/bin/env bash

echo "-----> Installing Python dependencies..."
pip install -r requirements.txt

echo "-----> Forzando migración limpia (reset de migraciones)..."
python manage.py migrate --fake-initial || echo "No se pudo ejecutar migrate --fake-initial"

echo "-----> Eliminando historial de migraciones de warehouse..."
python manage.py shell -c "
from django.db import connection
with connection.cursor() as cursor:
    cursor.execute('DELETE FROM django_migrations WHERE app = \"warehouse\";')
    print('Historial de migraciones eliminado para warehouse')
"

echo "-----> Aplicando migraciones desde cero..."
python manage.py migrate

echo "-----> Cargando datos iniciales (si existe data_clean.json)..."
if [ -f data_clean.json ]; then
    python manage.py loaddata data_clean.json
else
    echo "data_clean.json no encontrado, omitiendo carga de datos"
fi

echo "-----> Creando superusuario si no existe..."
python create_superuser.py

echo "-----> Recolectando archivos estáticos..."
python manage.py collectstatic --noinput
