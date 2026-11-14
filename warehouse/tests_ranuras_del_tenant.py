"""
Las ranuras de envio del pedimento, configuradas por cada tenant, y los kilos
del renglon del pedimento.

Lo que se prueba: que un tenant que nunca entra a configurar sigue igual que
antes; que lo que marca como obligatorio es lo que frena el envio a revision y
lo que se pinta en rojo; que sus ranuras propias reciben archivos y salen en el
ZIP; que solo la casa de gerente para arriba configura; y que los kilos del
renglon salen de la entrada cuando va completa y no se inventan cuando va
partida.
"""
import io
import zipfile
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile

from .models import (Pedimento, PedimentoBundle, PedimentoDocument,
                     RanuraDePedimento, UserProfile)
from .tests_pedimentos import BaseDeAlmacen


URL = '/pedimentos/slots/'


class RanurasDelTenantTests(BaseDeAlmacen):

    def setUp(self):
        self.client.force_login(self.jefa)
        self.entrada = self.operacion('ED261114-0001', bundle_qty=4)
        self.ped = Pedimento.objects.create(
            tenant=self.tenant, customer=self.cliente, orden=1,
            created_by=self.jefa, ped_aduana='24', ped_patente='1515',
            ped_consecutivo='6005000')
        PedimentoBundle.objects.create(pedimento=self.ped,
                                       operation=self.entrada, bultos=4)

    def archivo(self, nombre='x.pdf'):
        return SimpleUploadedFile(nombre, b'%PDF-1.4 x',
                                  content_type='application/pdf')

    def subir(self, ranura, nombre='doc.pdf'):
        return self.client.post('/pedimentos/%d/upload/' % self.ped.pk,
                                {'ranura': ranura, 'archivo': self.archivo(nombre)})

    def ranura(self, clave):
        RanuraDePedimento.de_tenant(self.tenant)
        return RanuraDePedimento.objects.get(tenant=self.tenant, clave=clave)

    def guardar(self, cambios=None, **extra):
        """Postea el formulario con lo que hay, cambiando lo de `cambios`."""
        datos = dict(extra)
        for r in RanuraDePedimento.de_tenant(self.tenant):
            activa, obligatoria = (cambios or {}).get(
                r.clave, (r.activa, r.obligatoria))
            if activa:
                datos[f'activa_{r.pk}'] = 'on'
            if obligatoria:
                datos[f'obligatoria_{r.pk}'] = 'on'
            if r.propia:
                datos[f'nombre_{r.pk}'] = r.nombre
        return self.client.post(URL, datos)

    def faltan(self):
        return [str(f) for f in self.ped.faltantes_para_revision]

    # -- Lo de fabrica --------------------------------------------------------

    def test_sin_configurar_quedan_las_de_siempre(self):
        claves = [r.clave for r in RanuraDePedimento.de_envio(self.tenant)]
        self.assertEqual(claves, ['M3', 'PROFORMA', 'COVE', 'CARTA_318', 'OTROS'])
        obligatorias = {r.clave for r in RanuraDePedimento.de_envio(self.tenant)
                        if r.obligatoria}
        self.assertEqual(obligatorias,
                         set(PedimentoDocument.RANURAS_PARA_REVISION))
        faltan = self.faltan()
        self.assertIn('COVE', faltan)
        self.assertNotIn('M3 file', faltan)

    # -- Lo que decide el tenant ---------------------------------------------

    def test_marcar_m3_obligatoria_la_pide(self):
        self.guardar({'M3': (True, True)})
        self.assertTrue(self.ranura('M3').obligatoria)
        self.assertIn('M3 file', self.faltan())

    def test_quitar_el_cove_deja_enviar_sin_el(self):
        self.guardar({'COVE': (True, False)})
        self.subir('PROFORMA')
        self.subir('CARTA_318')
        self.assertEqual(self.faltan(), [])
        self.assertTrue(self.ped.puede_enviarse_a_revision)

    def test_una_apagada_no_sale_ni_se_pide(self):
        self.guardar({'COVE': (False, True)})
        self.assertFalse(self.ranura('COVE').obligatoria)
        self.assertNotIn('COVE', self.faltan())
        respuesta = self.client.get('/pedimentos/', {'customer': self.cliente.pk})
        ped = respuesta.context['pedimentos'][0]
        self.assertNotIn('COVE', [c for c, *_ in ped.ranuras_envio])

    def test_el_rojo_es_solo_de_las_obligatorias(self):
        respuesta = self.client.get('/pedimentos/', {'customer': self.cliente.pk})
        html = respuesta.content.decode()
        # Tres obligatorias vacias de fabrica; M3 y Otros, punteadas sin rojo.
        self.assertEqual(html.count('class="ranura vacia pide"'), 3)
        self.assertEqual(html.count('class="ranura vacia"'), 2)

    def test_mover_cambia_el_orden(self):
        cove = self.ranura('COVE')
        self.guardar(mover=f'{cove.pk}:arriba')
        claves = [r.clave for r in RanuraDePedimento.de_envio(self.tenant)]
        self.assertEqual(claves[:3], ['M3', 'COVE', 'PROFORMA'])

    # -- Las propias ----------------------------------------------------------

    def test_una_propia_recibe_archivos_y_sale_en_el_zip(self):
        self.guardar(nueva='Carta porte', nueva_obligatoria='on', agregar='1')
        propia = RanuraDePedimento.objects.get(tenant=self.tenant, propia=True)
        self.assertEqual(propia.clave, 'EXTRA_1')
        self.assertIn('Carta porte', self.faltan())

        self.subir(propia.clave, 'cp.pdf')
        self.assertNotIn('Carta porte', self.faltan())

        respuesta = self.client.get('/pedimentos/%d/zip/' % self.ped.pk)
        contenido = b''.join(respuesta.streaming_content) \
            if hasattr(respuesta, 'streaming_content') else respuesta.content
        nombres = zipfile.ZipFile(io.BytesIO(contenido)).namelist()
        self.assertTrue(any('Carta porte' in n for n in nombres), nombres)

    def test_una_propia_con_archivos_se_apaga_en_vez_de_borrarse(self):
        self.guardar(nueva='Carta porte', agregar='1')
        propia = RanuraDePedimento.objects.get(tenant=self.tenant, propia=True)
        self.subir(propia.clave)
        self.guardar(quitar=str(propia.pk))
        propia.refresh_from_db()
        self.assertFalse(propia.activa)

    def test_una_propia_vacia_se_borra(self):
        self.guardar(nueva='Carta porte', agregar='1')
        propia = RanuraDePedimento.objects.get(tenant=self.tenant, propia=True)
        self.guardar(quitar=str(propia.pk))
        self.assertFalse(RanuraDePedimento.objects.filter(pk=propia.pk).exists())

    def test_una_clave_inventada_no_se_sube(self):
        respuesta = self.subir('EXTRA_99')
        self.assertEqual(respuesta.status_code, 404)

    # -- Quien entra ----------------------------------------------------------

    def test_el_staff_y_el_cliente_no_configuran(self):
        for rol in ('staff', 'customer'):
            u = User.objects.create_user(f'u_{rol}', password='x')
            UserProfile.objects.create(user=u, tenant=self.tenant, role=rol,
                                       customer=self.cliente if rol == 'customer' else None)
            self.client.force_login(u)
            self.assertEqual(self.client.get(URL).status_code, 404, rol)

    def test_la_casa_entra(self):
        self.assertEqual(self.client.get(URL).status_code, 200)


class KilosDelRenglonTests(BaseDeAlmacen):

    def setUp(self):
        self.entrada = self.operacion('ED261114-0002', bundle_qty=2,
                                      weight_lbs=Decimal('49'),
                                      weight_kgs=Decimal('22.23'))
        self.ped = Pedimento.objects.create(
            tenant=self.tenant, customer=self.cliente, orden=1)

    def test_entrada_completa_toma_los_kilos_de_la_entrada(self):
        r = PedimentoBundle.objects.create(pedimento=self.ped,
                                           operation=self.entrada, bultos=2)
        self.assertIsNone(r.weight_kgs)
        self.assertEqual(r.kilos, Decimal('22.23'))
        self.assertEqual(self.ped.total_kilos, Decimal('22.23'))

    def test_entrada_partida_sin_kilos_no_se_inventa(self):
        r = PedimentoBundle.objects.create(pedimento=self.ped,
                                           operation=self.entrada, bultos=1)
        self.assertIsNone(r.kilos)

    def test_los_kilos_tecleados_se_conservan(self):
        r = PedimentoBundle.objects.create(pedimento=self.ped,
                                           operation=self.entrada, bultos=1,
                                           weight_kgs=Decimal('10.5'))
        r.refresh_from_db()
        self.assertEqual(r.weight_kgs, Decimal('10.50'))
        self.assertEqual(r.kilos, Decimal('10.50'))

    def test_la_tarjeta_dice_dos_y_no_dos_de_dos(self):
        PedimentoBundle.objects.create(pedimento=self.ped,
                                       operation=self.entrada, bultos=2)
        self.client.force_login(self.jefa)
        html = self.client.get('/pedimentos/', {'customer': self.cliente.pk}
                               ).content.decode()
        self.assertNotIn('Lbs', html)
        self.assertIn('22.23', html)
        self.assertNotIn('2 <span class="soft">of 2', html)
