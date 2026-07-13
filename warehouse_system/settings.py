import os
import dj_database_url 
from pathlib import Path
from dotenv import load_dotenv
BASE_DIR = Path(__file__).resolve().parent.parent
env_path = BASE_DIR / '.env'
load_dotenv(dotenv_path=env_path)

SECRET_KEY = os.environ.get ('SECRET_KEY')

#DEBUG = os.environ.get ('DEBUG') =='True'
DEBUG = False  # ¡MUY IMPORTANTE! Desactiva el modo debug

#ALLOWED_HOSTS = ['rdeluna.pythonanywhere.com', 'www.rdeluna.pythonanywhere.com', 'localhost', '127.0.0.1']
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,.onrender.com').split(',')
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'warehouse',
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
DATABASES = {
    'default': dj_database_url.config(
        default=os.environ.get('DATABASE_URL'),
        conn_max_age=600,
        ssl_require=True  # Recomendado para conexiones seguras
    )
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

# ─── TWILIO WHATSAPP ──────────────────────────────────────────────────────────
# Get these from console.twilio.com
TWILIO_ACCOUNT_SID   = os.environ.get ('TWILIO_ACCOUNT_SID')  # Account SID
TWILIO_AUTH_TOKEN    = os.environ.get ('TWILIO_AUTH_TOKEN')    # Auth Token
TWILIO_WHATSAPP_FROM = os.environ.get ('TWILIO_WHATSAPP_FROM')             # Your Twilio WhatsApp number
