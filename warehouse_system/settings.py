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
STATIC_ROOT = BASE_DIR / 'staticfiles'

# El proyecto sirve sus estáticos desde las carpetas `static/` de cada app, no
# desde una raíz común: el directorio `static/` del proyecto no existe ni está
# en el repositorio. Declararlo a secas hacía que Django avisara con
# staticfiles.W004 en cada `check` y en cada `collectstatic` del deploy. Se
# respeta si alguien lo crea.
STATICFILES_DIRS = [BASE_DIR / 'static'] if (BASE_DIR / 'static').is_dir() else []

# ====================================================
# ARCHIVOS MEDIA - CLOUDFLARE R2
# ====================================================
# R2 habla el protocolo de S3, así que se usa django-storages con el backend
# s3boto3 apuntado al endpoint de Cloudflare. Las credenciales llegan en
# variables R2_* y se exponen con los nombres AWS_* porque son esos los que
# django-storages busca.
#
# Este bloque estaba escrito dos veces en el archivo, y el segundo pisaba al
# primero renglón por renglón. Quedaba uno solo vigente, pero cualquier cambio
# hecho en el de arriba no tenía ningún efecto.
AWS_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
AWS_STORAGE_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME')
AWS_S3_ENDPOINT_URL = os.environ.get('R2_ENDPOINT_URL')
AWS_S3_REGION_NAME = 'auto'
AWS_S3_SIGNATURE_VERSION = 's3v4'
AWS_S3_USE_SSL = True
AWS_S3_OBJECT_PARAMETERS = {'CacheControl': 'max-age=86400'}

# django-storages sobrescribe el objeto que ya ocupe la ruta mientras no se le
# diga esto, y eso destruyo dos archivos en produccion: dos documentos con el
# mismo nombre subidos el mismo dia daban la misma ruta y el segundo pisaba al
# primero, sin error y sin rastro. Con `False`, Django le añade un sufijo al
# nombre en vez de pisar. `ruta_documento` ya evita la colision por su cuenta;
# esto es la red debajo, porque el valor por omision falla en silencio.
AWS_S3_FILE_OVERWRITE = False

# Dominio público desde el que se sirven los archivos. Es lo que decide la URL
# que devuelve FieldFile.url; sin él django-storages firma URLs contra el
# endpoint de R2, que caducan. Vive en el entorno porque el dominio de
# desarrollo del bucket y el definitivo son distintos.
AWS_S3_CUSTOM_DOMAIN = os.environ.get('AWS_S3_CUSTOM_DOMAIN')

# MEDIA_URL no interviene en cómo se sirven los archivos de R2 - de eso se
# encarga AWS_S3_CUSTOM_DOMAIN -, pero Django la exige y urls.py la usa. Antes
# se armaba siempre con el dominio, así que sin la variable definida quedaba en
# la cadena literal 'https://None/'.
MEDIA_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/' if AWS_S3_CUSTOM_DOMAIN else '/media/'

# Django >= 5.1 ya no lee DEFAULT_FILE_STORAGE: el backend se declara aquí. El
# archivo definía aquella variable en dos sitios y hasta la imprimía al
# arrancar, pero no tenía efecto alguno sobre este proyecto (Django 6.0).
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# La política CORS del bucket se configura en el panel de Cloudflare, no aquí.
# Estaba pegada en este archivo como un literal JSON suelto que Python evaluaba
# y tiraba en cada arranque. Quedó documentada en docs/configurar-r2.md.
#
# Para comprobar que las credenciales y el bucket responden:
#     python manage.py check_r2

# ====================================================
# FACTURACIÓN DE LA PLATAFORMA
# ====================================================
# Quién factura, para la cabecera del PDF de las facturas. Vive en el entorno y
# no en el código por la misma razón por la que el nombre de la empresa dejó de
# estar escrito a mano en la pantalla de entrada: quien opera esta plataforma
# puede no ser quien la escribió. Sin configurar nada sale un nombre neutro, que
# es preferible a inventar uno.
PLATFORM_BILLING_NAME = os.environ.get('PLATFORM_BILLING_NAME', '')
PLATFORM_BILLING_EMAIL = os.environ.get('PLATFORM_BILLING_EMAIL', '')
PLATFORM_BILLING_ADDRESS = os.environ.get('PLATFORM_BILLING_ADDRESS', '')

# ====================================================
# CONFIGURACIÓN DE EMAIL
# ====================================================
# Render bloquea la salida SMTP (puertos 25, 465 y 587) en los servicios web del
# plan gratuito desde septiembre de 2025, así que en producción el correo sale
# por la API HTTPS de Resend. El SMTP se conserva porque sigue funcionando desde
# la red local, que es donde se prueba.
#
#   EMAIL_PROVIDER=resend  -> API de Resend (necesita RESEND_API_KEY)
#   EMAIL_PROVIDER=smtp    -> servidor SMTP propio (variables EMAIL_HOST y demás)
#   sin definir             -> Resend si hay RESEND_API_KEY, SMTP si no
#
# `EMAIL_BACKEND` explícito en el entorno gana sobre todo lo anterior, para poder
# forzar el backend de consola al depurar.
RESEND_API_KEY = os.getenv('RESEND_API_KEY', '')
EMAIL_PROVIDER = os.getenv('EMAIL_PROVIDER', '').strip().lower()

if not EMAIL_PROVIDER:
    EMAIL_PROVIDER = 'resend' if RESEND_API_KEY else 'smtp'

if EMAIL_PROVIDER == 'resend':
    _EMAIL_BACKEND_DEFAULT = 'warehouse.email_backends.ResendBackend'
else:
    _EMAIL_BACKEND_DEFAULT = 'django.core.mail.backends.smtp.EmailBackend'

EMAIL_BACKEND = os.getenv('EMAIL_BACKEND') or _EMAIL_BACKEND_DEFAULT
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

# Tope por archivo adjunto. Los expedientes traen fotos y hasta video; además de
# que ningún servidor acepta un correo de 40 MB, leerlo entero en memoria en un
# plan chico es la forma rápida de quedarse sin RAM. Resend rechaza los envíos
# que pasan de 40 MB en total, y el base64 infla el tamaño un 33%.
EMAIL_MAX_ATTACHMENT_MB = int(os.getenv('EMAIL_MAX_ATTACHMENT_MB', 5))

print(f"[INFO] Correo: proveedor={EMAIL_PROVIDER} backend={EMAIL_BACKEND}")

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
