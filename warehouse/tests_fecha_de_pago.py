"""
La fecha de pago que da el cliente al aprobar, y la columna "Creado" de la
tarea de cruce con los pasos del pedimento (22 nov 2026).

1. Aprobar pide la fecha de pago: sin ella, o con una de antes de hoy, no se
   aprueba.
2. La fecha se guarda, sale en el pedimento y en el correo a la casa, y se
   borra al reabrir.
3. La columna de la tarea se llama "Creado" y, debajo de quien y cuando,
   enseña "Env. rev.", "Validar y pagar" y el dia de pago.
"""
from datetime import timedelta

from django.core import mail
from django.utils import timezone, translation

from .models import CrossingTaskItem, Pedimento, PedimentoBundle
from .tests_cruces import BaseDeCruces


class Base(BaseDeCruces):

    def setUp(self):
        super().setUp()
        self.lopez = self.duenio      # el usuario del cliente
        self.t = self.tarea()
        self.entrada = self.operacion('ED261122-0001', bundle_qty=2, invoice='649785')
        CrossingTaskItem.objects.create(task=self.t, operation=self.entrada,
                                        added_by=self.jefa)
        self.ped = Pedimento.objects.create(
            tenant=self.tenant, customer=self.cliente, orden=1,
            ped_aduana='24', ped_patente='1780', ped_consecutivo='6003555',
            estado=Pedimento.EN_REVISION, created_by=self.jefa,
            enviado_a_revision_en=timezone.now())
        PedimentoBundle.objects.create(pedimento=self.ped, operation=self.entrada,
                                       bultos=2)
        self.hoy = timezone.localdate()

    def aprobar(self, fecha):
        self.client.force_login(self.lopez)
        datos = {} if fecha is None else {'fecha_de_pago': fecha}
        respuesta = self.client.post(f'/pedimentos/{self.ped.pk}/approve/', datos)
        self.ped.refresh_from_db()
        return respuesta

    def tarea_html(self, idioma=None):
        self.client.force_login(self.jefa)
        if idioma:
            with translation.override(idioma):
                return self.client.get('/cruces/', {'customer': self.cliente.pk},
                                       HTTP_ACCEPT_LANGUAGE=idioma).content.decode()
        return self.client.get('/cruces/', {'customer': self.cliente.pk}).content.decode()


class AprobarPideLaFechaTests(Base):

    def test_el_formulario_la_pide(self):
        self.client.force_login(self.lopez)
        respuesta = self.client.get('/pedimentos/', {'customer': self.cliente.pk,
                                                     'solo': self.ped.pk})
        self.assertContains(respuesta, 'name="fecha_de_pago" required')
        self.assertContains(respuesta, f'min="{self.hoy.isoformat()}"')

    def test_sin_fecha_no_se_aprueba(self):
        respuesta = self.aprobar(None)
        self.assertEqual(respuesta.status_code, 422)
        self.assertEqual(self.ped.estado, Pedimento.EN_REVISION)
        self.aprobar('no-es-fecha')
        self.assertEqual(self.ped.estado, Pedimento.EN_REVISION)

    def test_una_de_antes_de_hoy_no(self):
        respuesta = self.aprobar((self.hoy - timedelta(days=1)).isoformat())
        self.assertEqual(respuesta.status_code, 422)
        self.assertContains(respuesta, 'before today', status_code=422)
        self.assertEqual(self.ped.estado, Pedimento.EN_REVISION)

    def test_con_fecha_se_aprueba_y_se_guarda(self):
        dia = self.hoy + timedelta(days=3)
        self.aprobar(dia.isoformat())
        self.assertEqual(self.ped.estado, Pedimento.APROBADO)
        self.assertEqual(self.ped.fecha_de_pago, dia)

    def test_hoy_tambien_vale(self):
        self.aprobar(self.hoy.isoformat())
        self.assertEqual(self.ped.fecha_de_pago, self.hoy)

    def test_sale_en_el_pedimento_y_en_el_correo(self):
        self.jefa.email = 'jefa@dyser.com'
        self.jefa.save(update_fields=['email'])
        mail.outbox = []
        dia = self.hoy + timedelta(days=2)
        self.aprobar(dia.isoformat())
        respuesta = self.client.get('/pedimentos/', {'customer': self.cliente.pk,
                                                     'solo': self.ped.pk})
        self.assertContains(respuesta, 'Payment ' + dia.strftime('%d/%m/%y'))
        self.assertTrue(any(dia.strftime('%d/%m/%Y') in c.body for c in mail.outbox))

    def test_reabrir_la_borra(self):
        self.aprobar(self.hoy.isoformat())
        self.assertTrue(self.ped.reabrir(self.jefa))
        self.ped.refresh_from_db()
        self.assertEqual(self.ped.estado, Pedimento.BORRADOR)
        self.assertIsNone(self.ped.fecha_de_pago)


class LaColumnaCreadoTests(Base):

    def test_se_llama_creado(self):
        self.assertIn('<th>Creado</th>', self.tarea_html('es'))

    def test_en_revision_dice_env_rev_con_fecha_y_hora(self):
        html = self.tarea_html('es')
        cuando = timezone.localtime(self.ped.enviado_a_revision_en).strftime('%d/%m %H:%M')
        self.assertIn('Env. rev.', html)
        self.assertIn(cuando, html)
        self.assertNotIn('>Validar y pagar<', html)

    def test_aprobado_dice_validar_y_pagar_y_el_dia_de_pago(self):
        dia = self.hoy + timedelta(days=4)
        self.aprobar(dia.isoformat())
        html = self.tarea_html('es')
        cuando = timezone.localtime(self.ped.aprobado_en).strftime('%d/%m %H:%M')
        self.assertIn('Validar y pagar', html)
        self.assertIn(cuando, html)
        self.assertIn('>Pago</span>', html)
        self.assertIn(dia.strftime('%d/%m/%y'), html)
        # En la columna Creado, en ese orden: quien y cuando, enviado,
        # validar y pagar, pago.
        celda = html[html.index('class="agr-lista"'):]
        celda = celda[:celda.index('</td>')]
        self.assertLess(celda.index(self.jefa.username), celda.index('Env. rev.'))
        self.assertLess(celda.index('Env. rev.'), celda.index('Validar y pagar'))
        self.assertLess(celda.index('Validar y pagar'), celda.index('>Pago<'))

    def test_cada_pedimento_es_un_bloque_con_sus_titulos_y_un_renglon(self):
        otra = self.operacion('ED261122-0002', bundle_qty=1)
        CrossingTaskItem.objects.create(task=self.t, operation=otra, added_by=self.jefa)
        html = self.tarea_html('es')
        # Dos pedimentos (uno armado, otro sin pedimento): dos bloques, cada
        # uno con su renglon de titulos.
        self.assertEqual(html.count('<tbody class="ped-bloque">'), 2)
        self.assertEqual(html.count('<tr class="ped-cab'), 2)
        bloque = html[html.index('<tbody class="ped-bloque">'):]
        bloque = bloque[:bloque.index('</tbody>')]
        cab = bloque[:bloque.index('</tr>')]
        for titulo in ('Cliente', 'Entrada', 'Factura', 'Estado', 'Pedimento', 'Creado'):
            self.assertIn(f'<th>{titulo}</th>', cab)
        # Un solo renglon de datos (mas el de Modificar, escondido).
        datos = bloque[bloque.index('</tr>') + 5:]
        self.assertEqual(datos.count('<tr class="acc-fila'), 1)
        self.assertEqual(datos.count('<tr'), 2)

    def test_sin_pedimento_solo_quien_y_cuando(self):
        Pedimento.objects.filter(pk=self.ped.pk).delete()
        html = self.tarea_html('es')
        self.assertNotIn('>Env. rev.<', html)
