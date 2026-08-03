#!/usr/bin/env bash
# build.sh - Script de construcción para Render

#!/usr/bin/env bash

#!/usr/bin/env bash

#!/usr/bin/env bash

#!/usr/bin/env bash

#!/usr/bin/env bash

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

echo "-----> Collecting static files..."
python manage.py collectstatic --noinput