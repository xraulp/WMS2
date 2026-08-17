import os
import dj_database_url
from pathlib import Path

# ====================================================
# CARGA DE .env SOLO EN ENTORNO LOCAL (DESARROLLO)
# ====================================================
IS_PRODUCTION = os.environ.get('RENDER', False)

if not IS_PRODUCTION:
    try:
        from dotenv import load_dotenv
        BASE_DIR = Path(__file__).resolve().parent.parent
        env_path = BASE_DIR / '.env'
        load_dotenv(dotenv_path=env_path)
        print("[INFO] .env cargado desde archivo local")
    except ImportError:
        print("[WARN] python-dotenv no instalado, omitiendo carga de .env")
else:
    print("[INFO] Entorno de producción (Render), usando variables de entorno del sistema")

# ====================================================
# CONFIGURACIÓN BASE
# ====================================================
if 'BASE_DIR' not in locals():
    BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY')
DEBUG = os.environ.get('DEBUG', 'False') == 'True'
# Los hosts del entorno se SUMAN a los base, no los reemplazan: antes había un
# segundo ALLOWED_HOSTS más abajo que pisaba a este, así que la variable de
# entorno nunca se usó. Al empezar a respetarla, sustituir habría podido dejar
# fuera '.onrender.com' y tumbar producción.
ALLOWED_HOSTS = ['localhost', '127.0.0.1', '.onrender.com']
ALLOWED_HOSTS += [
    h.strip() for h in os.getenv('ALLOWED_HOSTS', '').split(',')
    if h.strip() and h.strip() not in ALLOWED_HOSTS
]

# ====================================================
# URL PÚBLICA DE LA APLICACIÓN
# ====================================================
# Se usa para armar los enlaces y códigos QR que salen impresos en los PDF.
# No puede deducirse del request porque los PDF también se generan fuera de uno
# (tareas, envíos por correo), así que vive en configuración.
#
# RENDER_EXTERNAL_URL la inyecta Render sola, así que en producción esto queda
# resuelto sin configurar nada. SITE_BASE_URL la pisa cuando haya dominio propio.
SITE_BASE_URL = (
    os.environ.get('SITE_BASE_URL')
    or os.environ.get('RENDER_EXTERNAL_URL')
    or 'http://localhost:8000'
).rstrip('/')

# Dominio raíz bajo el cual cada tenant tiene su propio subdominio
# (ej. 'wms.com' → los enlaces de DYSER salen como https://dyser.wms.com).
# Vacío = todos los tenants comparten SITE_BASE_URL, que es la situación de hoy:
# Render en plan free no permite subdominios propios.
TENANT_BASE_DOMAIN = os.environ.get('TENANT_BASE_DOMAIN', '').strip().lstrip('.')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'warehouse',
    'storages',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    #'warehouse.middleware.TenantPermissionsMiddleware', # pendiente: 'role' es CharField, no FK a Role todavia
    #'warehouse.middleware.TenantContextMiddleware',      # pendiente: revisar si aplica con TemplateResponse
    'warehouse.middleware.TenantMiddleware',
]

ROOT_URLCONF = 'warehouse_system.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'warehouse_system.wsgi.application'

# NOTA: aquí había un segundo ALLOWED_HOSTS que pisaba al de arriba y dejaba
# la variable de entorno ALLOWED_HOSTS sin efecto. Sus valores se fusionaron
# en el default de la línea 30.

# ====================================================
# CONFIGURACIÓN DE BASE DE DATOS (SIMPLE Y ROBUSTA)
# ====================================================
DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL:
    try:
        DATABASES = {
            'default': dj_database_url.parse(DATABASE_URL, conn_max_age=600, ssl_require=True)
        }
        print("[INFO] Base de datos configurada desde DATABASE_URL")
    except Exception as e:
        print(f"[ERROR] Falló la configuración de PostgreSQL: {e}")
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': BASE_DIR / 'db.sqlite3',
            }
        }
else:
    print("[WARN] DATABASE_URL no definida, usando SQLite")
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# ====================================================
# VALIDACIÓN DE CONTRASEÑAS
# ====================================================
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ====================================================
# INTERNACIONALIZACIÓN
# ====================================================
LANGUAGE_CODE = 'es-mx'
TIME_ZONE = 'America/Chicago'
USE_I18N = True
USE_TZ = True

# ====================================================
# ARCHIVOS ESTÁTICOS
# ====================================================
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# ====================================================
# ARCHIVOS MEDIA - R2
# ====================================================
# Configuración de Cloudflare R2
AWS_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
AWS_STORAGE_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME')
AWS_S3_ENDPOINT_URL = os.environ.get('R2_ENDPOINT_URL')
AWS_S3_REGION_NAME = 'auto'
AWS_S3_SIGNATURE_VERSION = 's3v4'
AWS_S3_OBJECT_PARAMETERS = {'CacheControl': 'max-age=86400'}
AWS_S3_USE_SSL = True

# URL pública de desarrollo de R2 (con barra final)
AWS_S3_CUSTOM_DOMAIN = 'pub-7aa64bbc50bd414e93e88ea59d6561a7.r2.dev'
MEDIA_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/'

# Backend de almacenamiento
DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'

# ====================================================
# CONFIGURACIÓN DE EMAIL
# ====================================================
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.getenv('EMAIL_HOST')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS') == 'True'
EMAIL_USE_SSL = os.getenv('EMAIL_USE_SSL') == 'True'
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL')

# Segundos de espera para conectar con el servidor de correo. Sin esto, un
# servidor que no contesta deja la petición colgada hasta que gunicorn mata al
# worker por timeout, y el operador ve un "Internal Server Error" en vez del
# aviso de que el correo no salió. Pasó en producción: la salida al puerto 465
# desde Render se queda esperando sin respuesta.
EMAIL_TIMEOUT = int(os.getenv('EMAIL_TIMEOUT', 10))

# ====================================================
# CONFIGURACIÓN DE TWILIO (WHATSAPP)
# ====================================================
TWILIO_ACCOUNT_SID   = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN    = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_FROM = os.environ.get('TWILIO_WHATSAPP_FROM')

# ====================================================
# CONFIGURACIÓN POR DEFECTO DE DJANGO
# ====================================================
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/'
LOGIN_REDIRECT_URL = '/'

# ====================================================
# CONFIGURACIÓN FORZADA DE R2 (NO MODIFICAR)
# ====================================================
import os
AWS_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
AWS_STORAGE_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME')
AWS_S3_ENDPOINT_URL = os.environ.get('R2_ENDPOINT_URL')
AWS_S3_REGION_NAME = 'auto'
AWS_S3_SIGNATURE_VERSION = 's3v4'
AWS_S3_USE_SSL = True
AWS_S3_OBJECT_PARAMETERS = {'CacheControl': 'max-age=86400'}

AWS_S3_CUSTOM_DOMAIN = os.environ.get('AWS_S3_CUSTOM_DOMAIN')
MEDIA_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/'

#DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'

# ====================================================
# CONFIGURACIÓN DE ALMACENAMIENTO (DJANGO >= 5.1)
# ====================================================
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}



# Verificar que la configuración se aplicó (mensajes en logs)
print(f"[DEBUG] R2_BUCKET_NAME: {AWS_STORAGE_BUCKET_NAME}")
print(f"[DEBUG] MEDIA_URL: {MEDIA_URL}")
print(f"[DEBUG] DEFAULT_FILE_STORAGE: {DEFAULT_FILE_STORAGE}")


[
  {
    "AllowedOrigins": ["*"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3600
  }
]

# ====================================================
# DIAGNÓSTICO DE CONEXIÓN A R2 (TEMPORAL)
# ====================================================
try:
    import boto3
    from botocore.exceptions import NoCredentialsError, ClientError

    print("[DEBUG] Intentando conectar a R2...")
    s3 = boto3.client(
        's3',
        endpoint_url=AWS_S3_ENDPOINT_URL,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_S3_REGION_NAME
    )
    # Intentar listar objetos (solo 1) para verificar credenciales
    response = s3.list_objects_v2(Bucket=AWS_STORAGE_BUCKET_NAME, MaxKeys=1)
    print(f"[DEBUG] ✅ Conexión a R2 exitosa. Bucket: {AWS_STORAGE_BUCKET_NAME}")
    print(f"[DEBUG]   - Contiene {response.get('KeyCount', 0)} objetos.")
except NoCredentialsError:
    print("[DEBUG] ❌ No se encontraron credenciales de AWS.")
except ClientError as e:
    error_code = e.response['Error']['Code']
    print(f"[DEBUG] ❌ Error de conexión a R2: {error_code} - {e}")
except Exception as e:
    print(f"[DEBUG] ❌ Error inesperado: {e}")