"""
La tabla de Operaciones con columnas juntas y grupos que se esconden.

Dos promesas: juntar columnas no pierde ningun dato -- cada valor que tenia su
columna propia sigue saliendo en la fila --, y los grupos que alguien esconde
se guardan en su perfil y la tabla ya llega asi la proxima vez.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from .models import Catalog, Tenant, UserProfile, Warehouse, WarehouseOperation


class BaseDeColumnas(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.usuario = User.objects.create_user('operador', password='x')
        cls.perfil = UserProfile.objects.create(
            user=cls.usuario, tenant=cls.tenant, role='admin')
        cliente = Catalog.objects.create(
            category='CUSTOMER', name='Acme', tenant=cls.tenant)
        bodega = Warehouse.objects.create(tenant=cls.tenant, code='LRD', name='Laredo')
        cls.op = WarehouseOperation.objects.create(
            tenant=cls.tenant, operation_type='ENTRY', custom_id='ACME-1',
            customer=cliente, created_by=cls.usuario, warehouse=bodega,
            entry_dispatched='SD-77', invoice='FAC-1', po_order='PO-55',
            carrier_name_manual='Fletes Rapidos', pro='PRO-123',
            trailer='CAJA-9', seal='SELLO-4',
            bundle_qty=12, bundle_type_manual='Tarimas',
            weight_lbs=Decimal('1200'), weight_kgs=Decimal('544.31'),
            description='Rollos de acero', note='Fragil', damage=True,
            ref_aa='AA-1', ref_dys='DYS-2', pedimento='24 1780 6004444',
            email_sent=True)

    def setUp(self):
        self.client.force_login(self.usuario)

    def tabla(self):
        respuesta = self.client.get('/operations/search/')
        self.assertEqual(respuesta.status_code, 200)
        return respuesta.content.decode()


class NadaSePierdeTests(BaseDeColumnas):

    def test_cada_dato_sigue_en_la_fila(self):
        html = self.tabla()
        for valor in ['ACME-1', 'SD-77', 'LRD', 'FAC-1', 'PO-55',
                      'Fletes Rapidos', 'PRO-123', 'CAJA-9', 'SELLO-4',
                      '12', 'Tarimas', '1200', '544.31', 'Rollos de acero',
                      'Fragil', 'AA-1', 'DYS-2', '24 1780 6004444', 'operador']:
            self.assertIn(valor, html, valor)


class GruposTests(BaseDeColumnas):

    def test_de_entrada_no_hay_nada_escondido(self):
        html = self.tabla()
        self.assertIn('class="ops-raiz"', html)
        self.assertNotIn('ocultar-', html.split('<style>')[0].split('class="ops-raiz')[1][:5])

    def test_se_guarda_en_el_perfil(self):
        self.client.post('/preferencias/columnas/', {'ocultas': 'carga,aduana'})
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.grupos_ocultos(), ['carga', 'aduana'])

    def test_un_nombre_desconocido_se_descarta(self):
        self.client.post('/preferencias/columnas/', {'ocultas': 'carga,inventado'})
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.columnas_ocultas, 'carga')

    def test_vacio_vuelve_a_mostrar_todo(self):
        self.client.post('/preferencias/columnas/', {'ocultas': 'carga'})
        self.client.post('/preferencias/columnas/', {'ocultas': ''})
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.grupos_ocultos(), [])

    def test_la_tabla_llega_con_lo_escondido(self):
        self.perfil.columnas_ocultas = 'transporte,auditoria'
        self.perfil.save()
        html = self.tabla()
        self.assertIn('class="ops-raiz ocultar-transporte ocultar-auditoria"', html)
        # La casilla de lo escondido llega apagada y el boton dice cuantos.
        self.assertIn('data-grupo="transporte" onchange="cambiarGrupo(this)">', html)
        self.assertIn('data-grupo="carga" onchange="cambiarGrupo(this)" checked>', html)
        self.assertIn('· 2 ', html)
        # Esconder no quita el dato del HTML: solo lo deja de pintar el CSS.
        self.assertIn('PRO-123', html)

    def test_hace_falta_haber_entrado(self):
        self.client.logout()
        respuesta = self.client.post('/preferencias/columnas/', {'ocultas': 'carga'})
        self.assertEqual(respuesta.status_code, 302)
