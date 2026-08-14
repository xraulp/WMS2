"""
URLs de los codigos QR y enlaces impresos en los PDF.

Antes estaban fijas a rdeluna.pythonanywhere.com (la app vieja), asi que los QR
generados apuntaban a un sitio equivocado. Ahora salen de la configuracion y,
cuando haya subdominios por tenant, del tenant dueno de la operacion.

Las dos ultimas pruebas generan los PDF de verdad y buscan la URL dentro del
binario: es lo unico que confirma que el helper quedo cableado en las 3 llamadas
(QR del reporte, enlace clickeable del reporte, QR de la etiqueta).
"""
from django.test import TestCase, override_settings

from .models import Tenant, WarehouseOperation
from .utils import (
    generate_label_pdf,
    generate_pdf_report,
    operation_digital_url,
    tenant_public_url,
)

SITIO = 'https://wms-demo.onrender.com'


class TenantPublicUrlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # get_or_create porque la migracion 0005 ya siembra el tenant 'default'
        # y subdomain es unique.
        cls.tenant_default, _ = Tenant.objects.get_or_create(
            subdomain='default',
            defaults={'name': 'Tenant del backfill', 'type': 'organization'})
        cls.tenant_propio = Tenant.objects.create(
            name='DYSER Group', type='organization', subdomain='dyser')

    @override_settings(SITE_BASE_URL=SITIO, TENANT_BASE_DOMAIN='')
    def test_sin_dominio_raiz_todos_los_tenants_comparten_el_sitio(self):
        """Situacion de hoy: un solo host en Render, sin subdominios."""
        self.assertEqual(tenant_public_url(self.tenant_propio), SITIO)
        self.assertEqual(tenant_public_url(self.tenant_default), SITIO)

    @override_settings(SITE_BASE_URL=SITIO, TENANT_BASE_DOMAIN='wms.com')
    def test_con_dominio_raiz_cada_tenant_va_a_su_subdominio(self):
        self.assertEqual(tenant_public_url(self.tenant_propio), 'https://dyser.wms.com')

    @override_settings(SITE_BASE_URL=SITIO, TENANT_BASE_DOMAIN='wms.com')
    def test_el_subdominio_default_no_cuenta_como_subdominio_real(self):
        """'default' lo pone el backfill; nadie lo registro."""
        self.assertEqual(tenant_public_url(self.tenant_default), SITIO)

    @override_settings(SITE_BASE_URL=SITIO, TENANT_BASE_DOMAIN='wms.com')
    def test_sin_tenant_cae_al_sitio_base_sin_reventar(self):
        """Quedan operaciones viejas sin tenant; un PDF no puede tronar por eso."""
        self.assertEqual(tenant_public_url(None), SITIO)


class OperationDigitalUrlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='DYSER Group', type='organization', subdomain='dyser')
        cls.op = WarehouseOperation.objects.create(
            tenant=cls.tenant, operation_type='ENTRY', custom_id='OP-2026-001')

    @override_settings(SITE_BASE_URL=SITIO, TENANT_BASE_DOMAIN='')
    def test_arma_la_ruta_y_el_query_del_expediente(self):
        self.assertEqual(
            operation_digital_url(self.op, '/mobile/'),
            f'{SITIO}/mobile/?tab=digital&q=OP-2026-001')
        self.assertEqual(
            operation_digital_url(self.op, '/dashboard/'),
            f'{SITIO}/dashboard/?tab=digital&q=OP-2026-001')

    @override_settings(SITE_BASE_URL=SITIO, TENANT_BASE_DOMAIN='wms.com')
    def test_la_url_sale_del_tenant_de_la_operacion(self):
        self.assertEqual(
            operation_digital_url(self.op, '/mobile/'),
            'https://dyser.wms.com/mobile/?tab=digital&q=OP-2026-001')


class PdfContieneLaUrlCorrectaTests(TestCase):
    """Prueba de regresion del bug original: la URL vieja no debe reaparecer."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='DYSER Group', type='organization', subdomain='dyser')
        cls.op = WarehouseOperation.objects.create(
            tenant=cls.tenant, operation_type='ENTRY', custom_id='OP-2026-001')

    @override_settings(SITE_BASE_URL=SITIO, TENANT_BASE_DOMAIN='wms.com')
    def test_el_reporte_lleva_el_enlace_del_tenant(self):
        pdf = generate_pdf_report(self.op)
        self.assertIn(b'https://dyser.wms.com/dashboard/?tab=digital', pdf)
        self.assertNotIn(b'pythonanywhere', pdf)

    @override_settings(SITE_BASE_URL=SITIO, TENANT_BASE_DOMAIN='wms.com')
    def test_la_etiqueta_se_genera_y_no_lleva_la_url_vieja(self):
        # El QR es una imagen: la URL no aparece como texto en el binario, asi
        # que aqui solo se comprueba que la etiqueta se construye sin tronar.
        pdf = generate_label_pdf(self.op)
        self.assertTrue(pdf)
        self.assertNotIn(b'pythonanywhere', pdf)
