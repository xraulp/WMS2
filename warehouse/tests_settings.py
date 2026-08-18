"""Pruebas sobre el propio archivo de configuración.

`settings.py` se degradó durante meses de una forma que ninguna prueba
funcional detecta, porque el proyecto seguía arrancando: un bloque de
configuración de R2 escrito dos veces donde el segundo pisaba al primero, un
literal JSON suelto que Python evaluaba y tiraba, y un diagnóstico que abría una
conexión de red a Cloudflare en cada arranque, incluso al correr estas pruebas.

Se examina el texto del archivo con el árbol de sintaxis en vez de mirar los
valores ya resueltos: una asignación repetida es invisible desde
`django.conf.settings`, que solo conserva la última.
"""

import ast
import io
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

RUTA_SETTINGS = Path(settings.BASE_DIR) / 'warehouse_system' / 'settings.py'


def _arbol():
    return ast.parse(io.open(RUTA_SETTINGS, encoding='utf-8').read())


class ArchivoDeConfiguracionTests(SimpleTestCase):

    def test_ninguna_configuracion_se_asigna_dos_veces(self):
        """Una segunda asignación al mismo nombre deja muerta a la primera.

        Solo se miran las asignaciones del nivel superior del módulo: las que
        viven dentro de un `if`/`else` (BASE_DIR, DATABASES, el backend de
        correo) son alternativas excluyentes, no duplicados.
        """
        vistos, repetidos = set(), []
        for nodo in _arbol().body:
            if not isinstance(nodo, ast.Assign):
                continue
            for destino in nodo.targets:
                if isinstance(destino, ast.Name):
                    if destino.id in vistos:
                        repetidos.append(f'{destino.id} (linea {nodo.lineno})')
                    vistos.add(destino.id)
        self.assertEqual(repetidos, [], f'Configuraciones asignadas dos veces: {repetidos}')

    def test_no_hay_expresiones_sueltas(self):
        """Un valor escrito sin asignar a nada se evalúa y se descarta.

        Es lo que pasaba con la política CORS del bucket, pegada tal cual en
        medio del archivo: parecía configurar algo y no configuraba nada.
        Los `print` de arranque son llamadas, así que no cuentan.
        """
        sueltas = [
            nodo.lineno for nodo in _arbol().body
            if isinstance(nodo, ast.Expr)
            and not isinstance(nodo.value, ast.Call)
            and not isinstance(nodo.value, ast.Constant)
        ]
        self.assertEqual(sueltas, [], f'Expresiones sin efecto en las lineas {sueltas}')

    def test_la_configuracion_no_abre_conexiones_de_red(self):
        """Importar settings es lo primero que hace Django, y lo hace siempre.

        Cualquier cliente de red creado aquí se paga en cada arranque de
        gunicorn, cada `runserver` y cada corrida de pruebas. El diagnóstico de
        R2 vive ahora en `python manage.py check_r2`.
        """
        texto = io.open(RUTA_SETTINGS, encoding='utf-8').read()
        for nodo in ast.walk(_arbol()):
            if isinstance(nodo, (ast.Import, ast.ImportFrom)):
                modulos = (
                    [a.name for a in nodo.names]
                    if isinstance(nodo, ast.Import) else [nodo.module or '']
                )
                for modulo in modulos:
                    self.assertNotIn(
                        modulo.split('.')[0], {'boto3', 'botocore', 'requests', 'smtplib'},
                        f'settings.py importa {modulo}, que habla por red',
                    )
        self.assertNotIn('list_objects', texto)


class AlmacenamientoTests(SimpleTestCase):

    def test_media_url_no_contiene_el_dominio_sin_resolver(self):
        """Sin AWS_S3_CUSTOM_DOMAIN, MEDIA_URL quedaba en 'https://None/'."""
        self.assertNotIn('None', settings.MEDIA_URL)
        self.assertTrue(settings.MEDIA_URL.endswith('/'))

    def test_el_backend_por_defecto_es_r2(self):
        """Django >= 5.1 ignora DEFAULT_FILE_STORAGE: manda STORAGES."""
        self.assertEqual(
            settings.STORAGES['default']['BACKEND'],
            'storages.backends.s3boto3.S3Boto3Storage',
        )
