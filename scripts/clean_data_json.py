# scripts/clean_data_json.py
import json
from datetime import datetime

# Ruta del archivo original y de salida
input_file = 'data.json'
output_file = 'data_clean.json'

# Campos obsoletos en UserProfile
FIELDS_TO_REMOVE = ['plain_password', 'customer', 'delete_password']

# Cargar el JSON original
with open(input_file, 'r', encoding='utf-8') as f:
    data = json.load(f)

# Recorrer cada objeto y limpiar campos
cleaned_data = []
for obj in data:
    model = obj.get('model')
    if model == 'warehouse.userprofile':
        # Eliminar campos obsoletos
        for field in FIELDS_TO_REMOVE:
            obj['fields'].pop(field, None)
        # Agregar created_at con la fecha actual
        obj['fields']['created_at'] = datetime.now().isoformat()
    cleaned_data.append(obj)

# Guardar el nuevo archivo
with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(cleaned_data, f, indent=2, ensure_ascii=False)

print(f"✅ Archivo limpiado guardado como {output_file}")
print(f"   - Se eliminaron los campos: {', '.join(FIELDS_TO_REMOVE)} de UserProfile")
print(f"   - Se agregó 'created_at' con la fecha actual a todos los UserProfile")