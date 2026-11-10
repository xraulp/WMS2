"""
Armar el pedimento de un cruce nuevo, entrando desde la tarea.

Lo que se prueba:

1. Que la casa no ve aqui los pedimentos ya pagados, salvo el que abre desde
   su propia tarea, y que esos pagados tampoco cuentan para el candado.
2. Que "Armar pedimento" abre uno con el embarque dentro, listo para capturar.
3. Que el numero tecleado aqui cae en su campo de la pestana Operaciones.
"""
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile

from .models import Pedimento, PedimentoBundle, PedimentoDocument, UserProfile
from .tests_pedimentos import BaseDeAlmacen

URL = '/pedimentos/'


class Base(BaseDeAlmacen):

    def setUp(self):
        self.client.force_login(self.jefa)
        self.entrada = self.operacion('ED261108-0001', bundle_qty=4)

    def ped(self, orden=1, **extra):
        return Pedimento.objects.create(tenant=self.tenant, customer=self.cliente,
                                        orden=orden, created_by=self.jefa, **extra)

    def pagado(self, orden=9, patente='1515'):
        return self.ped(orden=orden, ped_aduana='80', ped_patente=patente,
                        ped_consecutivo='6000001', estado=Pedimento.PAGADO)

    def con_numero(self, ped, consecutivo='6003555', patente='1780'):
        return self.client.post(f'/pedimentos/{ped.pk}/number/', {
            'ped_aduana': '24', 'ped_patente': patente,
            'ped_consecutivo': consecutivo})

    def dentro(self, ped, op=None, bultos=4):
        return PedimentoBundle.objects.create(
            pedimento=ped, operation=op or self.entrada, bultos=bultos)

    def visibles(self, **parametros):
        respuesta = self.client.get(URL, {'customer': self.cliente.pk, **parametros})
        return [p.pk for p in respuesta.context['pedimentos']]


class LosPagadosNoEstorbanTests(Base):

    def test_la_casa_no_ve_los_pagados(self):
        pagado, abierto = self.pagado(), self.ped()
        self.assertEqual(self.visibles(), [abierto.pk])
        self.assertNotIn(pagado.pk, self.visibles())

    def test_el_pagado_se_abre_solo_desde_su_tarea(self):
        pagado, _ = self.pagado(), self.ped()
        self.assertEqual(self.visibles(solo=pagado.pk), [pagado.pk])

    def test_un_solo_que_no_es_numero_no_rompe_la_pantalla(self):
        self.pagado()
        self.assertEqual(self.visibles(solo='abc'), [])


class SoloEsePedimentoTests(Base):

    def test_desde_la_tarea_no_sale_ningun_otro(self):
        self.pagado(orden=1)
        self.ped(orden=2)
        este = self.ped(orden=3)
        self.assertEqual(self.visibles(solo=este.pk), [este.pk])

    def test_no_salen_el_selector_los_cuadritos_ni_lo_suelto(self):
        este = self.ped()
        self.operacion('ED261108-0009', bundle_qty=2)
        respuesta = self.client.get(URL, {'customer': self.cliente.pk, 'solo': este.pk})
        self.assertNotContains(respuesta, 'class="vistazo"')
        self.assertNotContains(respuesta, 'class="suelto"')
        self.assertNotContains(respuesta, 'name="customer" onchange')

    def test_asignar_solo_ofrece_sus_propios_embarques(self):
        este = self.ped()
        self.dentro(este, bultos=3)
        otra = self.operacion('ED261108-0010', bundle_qty=2)
        respuesta = self.client.get(URL, {'customer': self.cliente.pk, 'solo': este.pk})
        ofrecidas = [op.pk for op in respuesta.context['sin_repartir']]
        self.assertEqual(ofrecidas, [self.entrada.pk])
        self.assertNotIn(otra.pk, ofrecidas)

    def test_el_de_otro_cliente_no_se_cuela(self):
        from .models import Catalog
        otro = Catalog.objects.create(category='CUSTOMER', name='Otro',
                                      tenant=self.tenant)
        ajeno = Pedimento.objects.create(tenant=self.tenant, customer=otro,
                                         orden=1, created_by=self.jefa)
        self.assertEqual(self.visibles(solo=ajeno.pk), [])

    def test_cada_accion_vuelve_a_ese_solo(self):
        ped = self.ped()
        respuesta = self.con_numero(ped)
        self.assertIn(f'solo={ped.pk}', respuesta['Location'])

    def test_un_error_se_pinta_sobre_ese_solo(self):
        self.ped(orden=2, ped_aduana='24', ped_patente='1515',
                 ped_consecutivo='6000001')
        ped = self.ped()
        respuesta = self.con_numero(ped, patente='1780')
        self.assertEqual([p.pk for p in respuesta.context['pedimentos']], [ped.pk])

    def test_borrar_el_que_se_veia_solo_regresa_a_la_tarea(self):
        ped = self.ped()
        respuesta = self.client.post(f'/pedimentos/{ped.pk}/delete/', {'solo': ped.pk})
        self.assertIn('cruces', respuesta['Location'])

    def test_los_pagados_no_cuentan_en_los_cuadritos_ni_en_el_agente(self):
        self.pagado()
        self.ped()
        respuesta = self.client.get(URL, {'customer': self.cliente.pk})
        self.assertEqual(respuesta.context['resumen']['pedimentos'], 1)
        self.assertEqual(respuesta.context['resumen']['con_numero'], 0)
        self.assertEqual(respuesta.context['patente'], '')

    def test_el_cliente_sigue_viendo_los_suyos(self):
        pagado = self.pagado()
        duenio = User.objects.create_user('duenio', password='x')
        UserProfile.objects.create(user=duenio, tenant=self.tenant,
                                   role='customer', customer=self.cliente)
        self.client.force_login(duenio)
        self.assertEqual(self.visibles(), [pagado.pk])

    def test_un_pagado_de_otro_agente_no_bloquea_el_numero_nuevo(self):
        self.pagado(patente='1515')
        ped = self.ped()
        self.assertEqual(self.con_numero(ped, patente='1780').status_code, 302)
        ped.refresh_from_db()
        self.assertEqual(ped.numero, '24-1780-6003555')

    def test_uno_en_curso_de_otro_agente_si_bloquea(self):
        self.ped(orden=2, ped_aduana='24', ped_patente='1515',
                 ped_consecutivo='6000001')
        ped = self.ped()
        self.assertEqual(self.con_numero(ped, patente='1780').status_code, 422)
        ped.refresh_from_db()
        self.assertFalse(ped.tiene_numero)

    def test_al_marcarlo_pagado_sigue_a_la_vista_esa_vez(self):
        ped = self.ped(estado=Pedimento.VALIDADO)
        PedimentoDocument.objects.create(
            pedimento=ped, ranura=PedimentoDocument.PEDIMENTO_PAGADO,
            original_name='p.pdf', file=SimpleUploadedFile('p.pdf', b'%PDF x'))
        respuesta = self.client.post(f'/pedimentos/{ped.pk}/paid/')
        self.assertIn(f'solo={ped.pk}', respuesta['Location'])


class ArmarDesdeLaTareaTests(Base):

    def armar(self, op=None):
        return self.client.post('/pedimentos/new/', {
            'customer': self.cliente.pk, 'operation': (op or self.entrada).pk})

    def test_nace_con_el_embarque_dentro_y_abre_su_tarjeta(self):
        respuesta = self.armar()
        ped = Pedimento.objects.get()
        self.assertEqual(ped.renglones.get().bultos, 4)
        self.assertIn(f'#ped-{ped.pk}', respuesta['Location'])

    def test_solo_mete_lo_que_le_queda_libre(self):
        self.dentro(self.ped(orden=3), bultos=3)
        self.armar()
        nuevo = Pedimento.objects.get(orden=4)
        self.assertEqual(nuevo.renglones.get().bultos, 1)

    def test_el_numero_de_la_entrada_se_adopta(self):
        op = self.operacion('ED261108-0002', bundle_qty=2, ped_aduana='24',
                            ped_patente='1780', ped_consecutivo='6003555')
        self.armar(op)
        self.assertEqual(Pedimento.objects.get().numero, '24-1780-6003555')

    def test_repetir_no_deja_pedimentos_vacios_de_mas(self):
        self.ped(orden=1)
        self.armar()
        self.assertEqual(Pedimento.objects.count(), 1)

    def test_el_candado_avisa_dentro_de_la_pantalla(self):
        self.ped(orden=1, ped_aduana='24', ped_patente='1515',
                 ped_consecutivo='6000001')
        op = self.operacion('ED261108-0003', bundle_qty=2, ped_aduana='24',
                            ped_patente='1780', ped_consecutivo='6003555')
        self.assertEqual(self.armar(op).status_code, 422)
        self.assertFalse(PedimentoBundle.objects.filter(operation=op).exists())

    def test_el_embarque_de_otro_cliente_no_entra(self):
        from .models import Catalog
        otro = Catalog.objects.create(category='CUSTOMER', name='Otro',
                                      tenant=self.tenant)
        ajena = self.operacion('ED261108-0004', bundle_qty=2)
        ajena.customer = otro
        ajena.save()
        self.assertEqual(self.armar(ajena).status_code, 404)

    def test_sale_la_subida_de_fotos_si_el_expediente_no_trae(self):
        self.entrada.has_serial_numbers = True
        self.entrada.save()
        self.armar()
        respuesta = self.client.get(URL, {'customer': self.cliente.pk})
        self.assertContains(respuesta, 'value="FOTOS_SERIES"')


class ElNumeroLlegaAOperacionesTests(Base):

    def numero_en_operaciones(self, op=None):
        op = op or self.entrada
        op.refresh_from_db()
        return op.pedimento, op.ped_aduana, op.ped_patente, op.ped_consecutivo

    def test_al_guardar_el_numero_se_captura_en_la_operacion(self):
        ped = self.ped()
        self.dentro(ped)
        self.con_numero(ped)
        self.assertEqual(self.numero_en_operaciones(),
                         ('24-1780-6003555', '24', '1780', '6003555'))

    def test_corregirlo_lo_corrige_tambien_alla(self):
        ped = self.ped()
        self.dentro(ped)
        self.con_numero(ped)
        self.con_numero(ped, consecutivo='6003556')
        self.assertEqual(self.numero_en_operaciones()[0], '24-1780-6003556')

    def test_borrarlo_lo_borra_tambien_alla(self):
        ped = self.ped()
        self.dentro(ped)
        self.con_numero(ped)
        self.client.post(f'/pedimentos/{ped.pk}/number/', {
            'ped_aduana': '', 'ped_patente': '', 'ped_consecutivo': ''})
        self.assertEqual(self.numero_en_operaciones(), ('', '', '', ''))

    def test_asignar_a_uno_con_numero_lo_lleva_a_la_operacion(self):
        ped = self.ped(ped_aduana='24', ped_patente='1780',
                       ped_consecutivo='6003555')
        self.client.post(f'/pedimentos/{ped.pk}/assign/', {
            'operation': self.entrada.pk, 'bultos': 4})
        self.assertEqual(self.numero_en_operaciones()[0], '24-1780-6003555')

    def test_al_adoptar_el_numero_lo_reciben_los_que_ya_iban_dentro(self):
        ped = self.ped()
        self.dentro(ped)
        tecleada = self.operacion('ED261108-0005', bundle_qty=2, ped_aduana='24',
                                  ped_patente='1780', ped_consecutivo='6003555')
        self.client.post(f'/pedimentos/{ped.pk}/assign/', {
            'operation': tecleada.pk, 'bultos': 2})
        self.assertEqual(self.numero_en_operaciones()[0], '24-1780-6003555')

    def test_partido_en_dos_pedimentos_lleva_los_dos_numeros(self):
        uno, dos = self.ped(orden=1), self.ped(orden=2)
        self.dentro(uno, bultos=2)
        self.dentro(dos, bultos=2)
        self.con_numero(uno, consecutivo='6000001')
        self.con_numero(dos, consecutivo='6000002')
        self.assertEqual(self.numero_en_operaciones(),
                         ('24-1780-6000001, 24-1780-6000002', '', '', ''))

    def test_el_segundo_pedimento_no_adopta_el_numero_del_primero(self):
        uno, dos = self.ped(orden=1), self.ped(orden=2)
        self.dentro(uno, bultos=2)
        self.con_numero(uno)
        self.client.post(f'/pedimentos/{dos.pk}/assign/', {
            'operation': self.entrada.pk, 'bultos': 2})
        dos.refresh_from_db()
        self.assertFalse(dos.tiene_numero)

    def test_sacar_el_embarque_le_quita_ese_numero(self):
        ped = self.ped()
        renglon = self.dentro(ped)
        self.con_numero(ped)
        self.client.post(f'/pedimentos/{ped.pk}/unassign/', {'renglon': renglon.pk})
        self.assertEqual(self.numero_en_operaciones(), ('', '', '', ''))

    def test_sacarlo_no_borra_un_numero_que_no_vino_de_aqui(self):
        ped = self.ped()
        renglon = self.dentro(ped)
        self.entrada.pedimento = 'PED viejo'
        self.entrada.save()
        self.client.post(f'/pedimentos/{ped.pk}/unassign/', {'renglon': renglon.pk})
        self.assertEqual(self.numero_en_operaciones()[0], 'PED viejo')
