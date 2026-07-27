#!/usr/bin/env bash
# build.sh - Script de construcción para Render

#!/usr/bin/env bash

#!/usr/bin/env bash

#!/usr/bin/env bash

echo "-----> Installing Python dependencies..."
pip install -r requirements.txt

echo "-----> Eliminando historial de migraciones de warehouse..."
python manage.py shell -c "
from django.db import connection
with connection.cursor() as cursor:
    cursor.execute(\"DELETE FROM django_migrations WHERE app = 'warehouse';\")
    print('Historial eliminado')
"

echo "-----> Aplicando migraciones..."
python manage.py migrate

echo "-----> Cargando datos iniciales..."
if [ -f data_clean.json ]; then
    python manage.py loaddata data_clean.json
fi

echo "-----> Creando superusuario..."
python create_superuser.py

echo "-----> Recolectando estáticos..."
python manage.py collectstatic --noinput