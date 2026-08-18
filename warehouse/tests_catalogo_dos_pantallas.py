"""
El catalogo partido en dos pantallas: la operativa y la de clientes.

Eran una sola tabla con un desplegable de categoria, lo que ademas de mezclar
dos trabajos distintos hacia imposible separar los permisos: quien podia
mantener los carriers podia crear clientes.

Lo que se comprueba aqui es que cada pantalla ve lo suyo, que lo que se crea
refresca la tabla correcta -son dos tablas en la misma pagina, con dos id- y que
quien no puede editar clientes los sigue viendo, en solo lectura.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from .models import Catalog, Tenant, UserProfile


class BaseCatalogo(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')

        def usuario(nombre, rol):
            u = User.objects.create_user(nombre, password='x')
            UserProfile.objects.create(user=u, tenant=cls.tenant, role=rol,
                                       delete_password='borrar123')
            return u

        cls.admin   = usuario('admin_tenant', 'admin')
        cls.manager = usuario('manager', 'manager')

        cls.cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Ferretera del Bajio')
        cls.carrier = Catalog.objects.create(
            tenant=cls.tenant, category='CARRIER', name='Transportes del Norte')
        cls.shipper = Catalog.objects.create(
            tenant=cls.tenant, category='SHIPPER', name='Embarques Rapidos')


class SeparacionDeLasDosTablasTests(BaseCatalogo):

    def test_la_pantalla_operativa_no_lista_clientes(self):
        self.client.force_login(self.admin)

        html = self.client.get('/catalog/list/?scope=operational').content.decode()

        self.assertIn('Transportes del Norte', html)
        self.assertNotIn('Ferretera del Bajio', html)

    def test_la_pantalla_de_clientes_no_lista_el_catalogo_operativo(self):
        self.client.force_login(self.admin)

        html = self.client.get('/catalog/list/?scope=customers').content.decode()

        self.assertIn('Ferretera del Bajio', html)
        self.assertNotIn('Transportes del Norte', html)

    def test_sin_ambito_cae_en_la_operativa(self):
        """
        La operativa es la de menos permiso, asi que es el default seguro: un
        enlace viejo sin `scope` no puede acabar mostrando la de clientes con
        sus botones.
        """
        self.client.force_login(self.admin)

        html = self.client.get('/catalog/list/').content.decode()

        self.assertNotIn('Ferretera del Bajio', html)

    def test_un_ambito_inventado_tampoco_abre_la_de_clientes(self):
        self.client.force_login(self.admin)

        html = self.client.get('/catalog/list/?scope=todo').content.decode()

        self.assertNotIn('Ferretera del Bajio', html)


class RefrescoDeLaTablaCorrectaTests(BaseCatalogo):
    """
    Las dos tablas conviven en la misma pagina, cada una con su id. Si el alta
    devolviera siempre el mismo id, crear un cliente repintaria la tabla
    operativa con la lista de clientes dentro.
    """

    def test_crear_un_cliente_refresca_la_tabla_de_clientes(self):
        self.client.force_login(self.admin)

        html = self.client.post('/catalog/create/', {
            'category': 'CUSTOMER', 'name': 'Cliente Nuevo'}).content.decode()

        self.assertIn('id="customer-table"', html)
        self.assertNotIn('id="catalog-table"', html)
        self.assertIn('Cliente Nuevo', html)

    def test_crear_un_carrier_refresca_la_tabla_operativa(self):
        self.client.force_login(self.admin)

        html = self.client.post('/catalog/create/', {
            'category': 'CARRIER', 'name': 'Carrier Nuevo'}).content.decode()

        self.assertIn('id="catalog-table"', html)
        self.assertNotIn('id="customer-table"', html)

    def test_editar_un_cliente_devuelve_solo_clientes(self):
        self.client.force_login(self.admin)

        html = self.client.post(f'/catalog/{self.cliente.pk}/edit/',
                                {'name': 'Ferretera del Bajio'}).content.decode()

        self.assertIn('Ferretera del Bajio', html)
        self.assertNotIn('Transportes del Norte', html)

    def test_archivar_un_carrier_devuelve_solo_operativos(self):
        self.client.force_login(self.admin)

        html = self.client.post(f'/catalog/{self.carrier.pk}/delete/').content.decode()

        self.assertIn('Embarques Rapidos', html)
        self.assertNotIn('Ferretera del Bajio', html)


class ClientesEnSoloLecturaTests(BaseCatalogo):
    """
    Manager y staff no pueden mantener la lista de clientes, pero si necesitan
    consultarla -el correo de contacto, el telefono-, asi que la ven sin los
    botones de alta, edicion y baja.
    """

    def test_el_manager_ve_los_clientes(self):
        self.client.force_login(self.manager)

        html = self.client.get('/catalog/list/?scope=customers').content.decode()

        self.assertIn('Ferretera del Bajio', html)

    def test_pero_sin_los_botones_de_edicion(self):
        self.client.force_login(self.manager)

        html = self.client.get('/catalog/list/?scope=customers').content.decode()

        self.assertNotIn('openCatalogEdit', html)
        self.assertNotIn('Archive', html)
        self.assertNotIn('Import', html)

    def test_el_admin_si_los_tiene(self):
        self.client.force_login(self.admin)

        html = self.client.get('/catalog/list/?scope=customers').content.decode()

        self.assertIn('openCatalogEdit', html)
        self.assertIn('Archive', html)

    def test_el_manager_conserva_los_botones_del_catalogo_operativo(self):
        self.client.force_login(self.manager)

        html = self.client.get('/catalog/list/?scope=operational').content.decode()

        self.assertIn('openCatalogEdit', html)
        self.assertIn('Archive', html)


class PestanasDelTableroTests(BaseCatalogo):

    def test_el_admin_ve_las_dos_pestanas(self):
        self.client.force_login(self.admin)

        html = self.client.get('/dashboard/').content.decode()

        self.assertIn('id="tab-catalog"', html)
        self.assertIn('id="tab-customers"', html)

    def test_el_manager_tambien_las_ve(self):
        """Las ve, pero la de clientes le sale en solo lectura."""
        self.client.force_login(self.manager)

        html = self.client.get('/dashboard/').content.decode()

        self.assertIn('id="tab-customers"', html)

    def test_el_modal_de_edicion_se_pinta_una_sola_vez(self):
        """
        Vivia dentro del partial de la tabla. Con dos tablas en la pagina eso
        duplicaba el mismo id y el segundo modal no habria respondido, asi que
        se saco fuera.
        """
        self.client.force_login(self.admin)

        html = self.client.get('/dashboard/').content.decode()

        self.assertEqual(html.count('id="cat-edit-modal"'), 1)
        self.assertEqual(html.count('function openCatalogEdit'), 1)

    def test_el_desplegable_operativo_ya_no_ofrece_clientes(self):
        """
        La categoria del alta operativa no puede incluir CUSTOMER: para eso esta
        la otra pantalla, donde va fija en un campo oculto.
        """
        self.client.force_login(self.admin)

        html = self.client.get('/dashboard/').content.decode()

        self.assertNotIn('<option value="CUSTOMER">', html)
        self.assertIn('name="category" value="CUSTOMER"', html)
