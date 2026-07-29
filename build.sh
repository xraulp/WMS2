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

echo "-----> Loading initial data (if exists)..."
if [ -f data_clean.json ]; then
    python manage.py loaddata data_clean.json
else
    echo "data_clean.json not found, skipping"
fi

echo "-----> Creating superuser..."
python create_superuser.py

echo "-----> Collecting static files..."
python manage.py collectstatic --noinput