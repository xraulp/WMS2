import os
import dj_database_url
from pathlib import Path

# ====================================================
# CARGA DE .env SOLO EN ENTORNO LOCAL (DESARROLLO)
# ====================================================
# Render inyecta automáticamente la variable RENDER=True en producción
IS_PRODUCTION = os.environ.get('RENDER', False)

if not IS_PRODUCTION:
    # Solo cargar dotenv en desarrollo local
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
# BASE_DIR ya está definido arriba si se cargó .env, pero lo definimos por si acaso
if 'BASE_DIR' not in locals():
    BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY')

# DEBUG: en producción siempre False, en local se puede activar con variable
DEBUG = os.environ.get('DEBUG', 'False') == 'True'

# ALLOWED_HOSTS
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,.onrender.com').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'warehouse',
    'storages',  # ← Agrega esta línea para django-storages (R2)
]
MIDDLEWARE = [
    
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # ¡Justo después de SecurityMiddleware!
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'warehouse.middleware.MobileRedirectMiddleware',
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

#DATABASES = {
    #'default': {
        #'ENGINE': 'django.db.backends.sqlite3',
        #'NAME': BASE_DIR / 'db.sqlite3',
   # }
#}

# Configuración de Base de Datos con fallback seguro
import os
import dj_database_url
from pathlib import Path

# ... (el resto de tu código)

# Configuración de base de datos forzada
import os
import dj_database_url

# Obtener la variable de entorno
raw_url = os.environ.get('DATABASE_URL', '')

# Limpiar: eliminar cualquier prefijo como "DATABASE_URL="
if raw_url.startswith('DATABASE_URL='):
    raw_url = raw_url.replace('DATABASE_URL=', '', 1)

# Si la URL es válida, configurar PostgreSQL; si no, usar SQLite
if raw_url and raw_url.startswith('postgresql://'):
    try:
        DATABASES = {
            'default': dj_database_url.parse(raw_url, conn_max_age=600, ssl_require=True)
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
    print("[WARN] DATABASE_URL no válida, usando SQLite")
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'es-mx'
TIME_ZONE = 'America/Chicago'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/'
LOGIN_REDIRECT_URL = '/'


# settings.py
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.getenv('EMAIL_HOST')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS') == 'True'
EMAIL_USE_SSL = os.getenv('EMAIL_USE_SSL') == 'True'
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL')

# settings.py
#EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
# Forzamos el host y puerto si el .env falla por alguna razón
#EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp-relay.brevo.com')
#EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))
#EMAIL_USE_TLS = str(os.getenv('EMAIL_USE_TLS')).lower() == 'true'
#EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER')
#EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD')

# settings.py
#EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
#EMAIL_HOST = os.getenv('EMAIL_HOST')
#EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))  # El puerto debe ser un número entero
#EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS') == 'True'  # Esto convierte el texto 'True' en un valor real
#EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER')
#EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD')
#DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', EMAIL_HOST_USER)


# ─── EMAIL CONFIGURATION ─────────────────────────────────────────────────────
# Configure your SMTP settings here
#EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
#EMAIL_HOST = os.environ.get ('EMAIL_HOST')      # e.g. smtp.gmail.com
#EMAIL_PORT = os.environ.get('EMAIL_PORT')
#EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS')
#EMAIL_USE_SSL = os.environ.get ('EMAIL_USE_SSL')
#EMAIL_HOST_USER = os.environ.get ('EMAIL_HOST_USER')
#EMAIL_HOST_PASSWORD = os.environ.get ('EMAIL_HOST_PASSWORD')
#DEFAULT_FROM_EMAIL = os.environ.get ('DEFAULT_FROM_EMAIL')

# ==================================================
# CONFIGURACIÓN DE ALMACENAMIENTO: CLOUDFLARE R2
# ==================================================
# En warehouse_system/settings.py

# Cloudflare R2
AWS_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
AWS_STORAGE_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME')
AWS_S3_ENDPOINT_URL = os.environ.get('R2_ENDPOINT_URL')
AWS_S3_REGION_NAME = 'auto'
AWS_S3_SIGNATURE_VERSION = 's3v4'
AWS_S3_OBJECT_PARAMETERS = {'CacheControl': 'max-age=86400'}
AWS_S3_USE_SSL = True

# ⚠️ La URL de desarrollo público (con barra final)
AWS_S3_CUSTOM_DOMAIN = 'pub-7aa64bbc50bd414e93e88ea59d6561a7.r2.dev'
MEDIA_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/'

DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'



# 1. Definir las variables de entorno para las credenciales de R2
#AWS_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
#AWS_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
#AWS_STORAGE_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME')
#AWS_S3_ENDPOINT_URL = os.environ.get('R2_ENDPOINT_URL')

# 2. Configuración adicional requerida por django-storages
#AWS_S3_REGION_NAME = 'auto'  # Cloudflare R2 usa 'auto' para la región
#AWS_S3_OBJECT_PARAMETERS = {
#    'CacheControl': 'max-age=86400',  # 1 día de caché para los archivos
#}
#AWS_S3_CUSTOM_DOMAIN = f'{AWS_STORAGE_BUCKET_NAME}.r2.cloudflarestorage.com' if AWS_STORAGE_BUCKET_NAME else None
#MEDIA_URL = f'https://pub-7aa64bbc50bd414e93e88ea59d6561a7.r2.dev/' 
#DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'

# 3. Configurar el storage backend para archivos multimedia (media)
#    Esto hace que todos los archivos subidos (fotos, documentos) se guarden en R2.
#DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'

# 4. (Opcional) Configurar el storage backend para archivos estáticos (static)
#    Si quieres que tus archivos CSS, JS, etc., también se sirvan desde R2,
#    descomenta la siguiente línea y comenta o elimina la configuración de WhiteNoise.
# STATICFILES_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
# STATIC_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/'
# Si usas WhiteNoise (tu configuración actual), no necesitas cambiar nada para los estáticos.

# ==================================================
# CONFIGURACIÓN DE ALMACENAMIENTO: CLOUDFLARE R2
# ==================================================

AWS_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
AWS_STORAGE_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME')
AWS_S3_ENDPOINT_URL = os.environ.get('R2_ENDPOINT_URL')
AWS_S3_REGION_NAME = 'auto'
AWS_S3_OBJECT_PARAMETERS = {'CacheControl': 'max-age=86400'}

DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'




# ─── TWILIO WHATSAPP ──────────────────────────────────────────────────────────
# Get these from console.twilio.com
TWILIO_ACCOUNT_SID   = os.environ.get ('TWILIO_ACCOUNT_SID')  # Account SID
TWILIO_AUTH_TOKEN    = os.environ.get ('TWILIO_AUTH_TOKEN')    # Auth Token
TWILIO_WHATSAPP_FROM = os.environ.get ('TWILIO_WHATSAPP_FROM')             # Your Twilio WhatsApp number
