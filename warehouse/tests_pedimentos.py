"""
El numero de pedimento como estructura, y el candado que sostiene.

Hasta ahora el pedimento era un campo de texto donde cabia cualquier cosa. Lo
que se prueba aqui es lo que cambia al dejar de serlo:

1. Que los quince digitos del Anexo 22 se componen y se desglosan bien, con los
   dos ejemplos reales que dio Diego -- `1780-6003555` y `1781-6000200` --.
2. Que la comprobacion que mas veces va a saltar salta: siete digitos en el
   consecutivo, ni seis ni ocho. Un cero de mas tecleando de prisa es el error
   que se cuela, y es el unico que se puede cazar antes de que el numero acabe
   en un documento oficial.
3. Que la aduana y la patente se leen de dentro del numero sin campo aparte, y
   que con eso el candado del agente aduanal funciona sin preguntarle a nadie.
4. Que las operaciones capturadas antes de todo esto -- las que tienen texto
   libre y ninguna casilla -- siguen funcionando y no bloquean a nadie.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from . import pedimentos
from .models import (Catalog, Pedimento, PedimentoBundle, Tenant, UserProfile,
                     WarehouseOperation)


# ── El numero por dentro ─────────────────────────────────────────────────────

class NumeroDePedimentoTests(TestCase):
    """`warehouse.pedimentos`, sin base de datos de por medio."""

    def test_los_dos_ejemplos_reales_cuadran(self):
        # Los que paso Diego, ya sin el cero de mas que se colo al escribirlos.
        self.assertEqual(pedimentos.numero_corrido('24', '1780', '6003555'),
                         '24-1780-6003555')
        self.assertEqual(pedimentos.numero_corrido('24', '1781', '6000200'),
                         '24-1781-6000200')

    def test_el_numero_completo_lleva_el_ano_de_validacion_delante(self):
        self.assertEqual(
            pedimentos.numero_completo('24', '1515', '6005000', '26'),
            '26 24 1515 6005000')

    def test_sin_ano_de_validacion_salen_los_tres_grupos_que_se_teclean(self):
        # Mientras el pedimento sigue en borrador el ano de validacion todavia
        # no ha ocurrido, asi que no se inventa.
        self.assertEqual(pedimentos.numero_completo('24', '1515', '6005000'),
                         '24 1515 6005000')

    def test_siete_digitos_ni_seis_ni_ocho(self):
        seis = pedimentos.errores_de_casillas('24', '1780', '600355')
        ocho = pedimentos.errores_de_casillas('24', '1780', '60035550')
        self.assertTrue(seis)
        self.assertTrue(ocho)
        # El mensaje cuenta los digitos que hay, que es lo que delata el cero
        # de mas. Sin el numero, "faltan digitos" no ayuda a encontrarlo.
        self.assertIn('6', str(seis[0]))
        self.assertIn('8', str(ocho[0]))

    def test_las_tres_casillas_vacias_no_son_un_error(self):
        # Un embarque se captura cuando llega; el numero lo da el agente
        # aduanal despues. Exigirlo al capturar seria pedirlo antes de que
        # exista.
        self.assertEqual(pedimentos.errores_de_casillas('', '', ''), [])

    def test_media_captura_no_se_guarda(self):
        self.assertEqual(pedimentos.numero_corrido('24', '178', ''), '')

    def test_desglosar_acepta_las_dos_formas_en_que_llega(self):
        # Los trece que se teclean, con guiones o sin ellos, y los quince
        # completos con el ano delante.
        self.assertEqual(pedimentos.desglosar('24-1780-6003555'),
                         ('24', '1780', '6003555'))
        self.assertEqual(pedimentos.desglosar('2417806003555'),
                         ('24', '1780', '6003555'))
        self.assertEqual(pedimentos.desglosar('26 24 1780 6003555'),
                         ('24', '1780', '6003555'))

    def test_un_numero_viejo_de_texto_libre_no_se_inventa_estructura(self):
        self.assertEqual(pedimentos.desglosar('PED 4455 rev B'), ('', '', ''))
        self.assertEqual(pedimentos.patente_de('PED 4455 rev B'), '')

    def test_la_aduana_y_la_patente_salen_de_dentro_del_numero(self):
        self.assertEqual(pedimentos.aduana_de('24-1780-6003555'), '24')
        self.assertEqual(pedimentos.patente_de('24-1780-6003555'), '1780')
        self.assertEqual(str(pedimentos.nombre_de_aduana('24')), 'Nuevo Laredo')
        self.assertEqual(str(pedimentos.nombre_de_aduana('80')), 'Colombia')


class CandadoDelAgenteAduanalTests(TestCase):
    """Un DODA solo puede llevar pedimentos de un mismo agente aduanal."""

    def test_dos_patentes_no_conviven(self):
        choca = pedimentos.choque('24-1781-6000200', ['24-1780-6003555'])
        self.assertIsNotNone(choca)
        self.assertEqual(choca['motivo'], 'PATENTE')
        # El aviso dice con cual choca. Un "valor invalido" no le sirve a quien
        # tiene que arreglarlo; saber cual es el otro pedimento lo convierte en
        # una instruccion.
        self.assertEqual(choca['choca_con'], '24-1780-6003555')
        self.assertIn('1781', str(choca['mensaje']))
        self.assertIn('1780', str(choca['mensaje']))

    def test_la_misma_patente_convive(self):
        self.assertIsNone(
            pedimentos.choque('24-1780-6003556', ['24-1780-6003555']))

    def test_dos_aduanas_tampoco_conviven(self):
        # Misma patente, distinta aduana: nadie mete en un cruce por Nuevo
        # Laredo un pedimento validado para Colombia. Es la misma comprobacion
        # y sale gratis, porque la aduana viaja en el mismo numero.
        choca = pedimentos.choque('80-1780-6003556', ['24-1780-6003555'])
        self.assertIsNotNone(choca)
        self.assertEqual(choca['motivo'], 'ADUANA')
        self.assertIn('Colombia', str(choca['mensaje']))
        self.assertIn('Nuevo Laredo', str(choca['mensaje']))

    def test_un_numero_sin_estructura_no_choca_con_nadie(self):
        # No se puede afirmar de que agente es, y bloquear por una sospecha
        # para el trabajo sin dar a cambio ninguna certeza.
        self.assertIsNone(pedimentos.choque('PED viejo', ['24-1780-6003555']))
        self.assertIsNone(pedimentos.choque('24-1780-6003555', ['PED viejo']))

    def test_la_lista_vacia_acepta_al_primero(self):
        self.assertIsNone(pedimentos.choque('24-1780-6003555', []))


# ── El pedimento dentro del sistema ──────────────────────────────────────────

class BaseDeAlmacen(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Dyser Group', type='organization', subdomain='dyser')
        cls.jefa = User.objects.create_user('jefa', password='x')
        UserProfile.objects.create(user=cls.jefa, tenant=cls.tenant, role='admin')
        cls.cliente = Catalog.objects.create(
            category='CUSTOMER', name='Acme', tenant=cls.tenant)

    def operacion(self, custom_id, **extra):
        return WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id=custom_id,
            customer=self.cliente, created_by=self.jefa, **extra)


class OperacionConPedimentoTests(BaseDeAlmacen):

    def test_las_casillas_componen_el_numero_al_guardar(self):
        op = self.operacion('ED-1', ped_aduana='24', ped_patente='1780',
                            ped_consecutivo='6003555')
        op.refresh_from_db()
        # `pedimento` sigue siendo el campo que sale en nombres de archivo,
        # reportes y busquedas, y ahora se escribe solo.
        self.assertEqual(op.pedimento, '24-1780-6003555')

    def test_media_captura_no_pisa_el_numero_que_ya_habia(self):
        op = self.operacion('ED-2', pedimento='PED viejo',
                            ped_aduana='24', ped_patente='17')
        op.refresh_from_db()
        self.assertEqual(op.pedimento, 'PED viejo')

    def test_una_operacion_vieja_responde_igual(self):
        # Sin casillas, la aduana y la patente se desglosan del texto que hay.
        op = self.operacion('ED-3', pedimento='24-1780-6003555')
        self.assertEqual(op.patente_aduanal, '1780')
        self.assertEqual(op.aduana_de_despacho, '24')

    def test_una_operacion_vieja_sin_estructura_no_afirma_nada(self):
        op = self.operacion('ED-4', pedimento='PED 4455 rev B')
        self.assertEqual(op.patente_aduanal, '')
        self.assertEqual(op.aduana_de_despacho, '')

    def test_la_casilla_de_numeros_de_serie_nace_apagada(self):
        # No se marca por defecto: quien recibe la mercancia es quien lo sabe.
        self.assertFalse(self.operacion('ED-5').has_serial_numbers)


class PedimentoTests(BaseDeAlmacen):

    def pedimento(self, orden=1, **extra):
        return Pedimento.objects.create(
            tenant=self.tenant, customer=self.cliente, orden=orden,
            created_by=self.jefa, **extra)

    def test_sin_numero_se_llama_por_su_orden(self):
        # Primero se agrupa la mercancia y despues el agente aduanal da el
        # numero, asi que un pedimento tiene que poder existir sin el.
        p = self.pedimento(orden=2)
        self.assertFalse(p.tiene_numero)
        self.assertEqual(p.etiqueta, 'Pedimento 2')

    def test_con_numero_se_llama_por_su_numero(self):
        p = self.pedimento(ped_aduana='24', ped_patente='1515',
                           ped_consecutivo='6005000')
        self.assertTrue(p.tiene_numero)
        self.assertEqual(p.etiqueta, '24-1515-6005000')
        self.assertEqual(p.numero_completo, '24 1515 6005000')
        p.anio_validacion = '26'
        self.assertEqual(p.numero_completo, '26 24 1515 6005000')

    def test_el_ejemplo_de_los_dos_proveedores(self):
        """
        El reparto que decidio todo este diseno.

        ED-0001 llega con dos pallets, uno de ABC LLC y otro de 123 LLC.
        ED-0002 llega con un pallet de ABC LLC. El cliente quiere juntar lo de
        ABC en un pedimento: eso parte una operacion entre dos pedimentos y
        junta dos operaciones en uno, que es justo lo que un campo de texto en
        la operacion no puede representar.
        """
        uno = self.operacion('ED260901-0001', bundle_qty=2)
        dos = self.operacion('ED260901-0002', bundle_qty=1)

        abc = self.pedimento(orden=1, ped_aduana='24', ped_patente='1515',
                             ped_consecutivo='6005000')
        resto = self.pedimento(orden=2, ped_aduana='24', ped_patente='1515',
                               ped_consecutivo='6005001')

        PedimentoBundle.objects.create(pedimento=abc, operation=uno, bultos=1)
        PedimentoBundle.objects.create(pedimento=abc, operation=dos, bultos=1)
        PedimentoBundle.objects.create(pedimento=resto, operation=uno, bultos=1)

        self.assertEqual(abc.total_bultos, 2)
        self.assertEqual(resto.total_bultos, 1)
        # La operacion partida sabe en que dos pedimentos quedo.
        self.assertEqual(uno.renglones_de_pedimento.count(), 2)

    def test_las_fotos_de_series_se_heredan_de_los_bultos(self):
        # No se decide a mano ni se marca "no aplica" cada vez: si alguno de
        # los bultos trae numeros de serie, la ranura aparece y es obligatoria.
        con = self.operacion('ED-S1', has_serial_numbers=True)
        sin = self.operacion('ED-S2')

        limpio = self.pedimento(orden=1)
        PedimentoBundle.objects.create(pedimento=limpio, operation=sin, bultos=1)
        self.assertFalse(limpio.necesita_fotos_de_series)

        mixto = self.pedimento(orden=2)
        PedimentoBundle.objects.create(pedimento=mixto, operation=sin, bultos=1)
        PedimentoBundle.objects.create(pedimento=mixto, operation=con, bultos=1)
        self.assertTrue(mixto.necesita_fotos_de_series)

    def test_el_candado_desde_el_pedimento(self):
        dyser = self.pedimento(orden=1, ped_aduana='24', ped_patente='1780',
                               ped_consecutivo='6003555')
        otro  = self.pedimento(orden=2, ped_aduana='24', ped_patente='1781',
                               ped_consecutivo='6000200')
        choca = otro.choque_con([dyser])
        self.assertIsNotNone(choca)
        self.assertEqual(choca['motivo'], 'PATENTE')
        self.assertIsNone(dyser.choque_con([dyser]))


class CapturaDesdeLaPantallaTests(BaseDeAlmacen):
    """Lo que pasa al mandar el formulario de nueva operacion."""

    def setUp(self):
        self.client.force_login(self.jefa)
        self.shipper = Catalog.objects.create(
            category='SHIPPER', name='ABC LLC', tenant=self.tenant)
        self.carrier = Catalog.objects.create(
            category='CARRIER', name='XPO', tenant=self.tenant)
        self.bundle = Catalog.objects.create(
            category='BUNDLE_TYPE', name='Pallet', tenant=self.tenant)

    def capturar(self, **extra):
        datos = {
            'date': '2026-09-07', 'operation_type': 'ENTRY',
            'customer_id': self.cliente.pk, 'shipper_id': self.shipper.pk,
            'carrier_id': self.carrier.pk, 'bundle_type_id': self.bundle.pk,
            'bundle_qty': '2', 'weight_lbs': '100', 'description': 'Cajas',
        }
        datos.update(extra)
        return self.client.post('/operations/create/', datos)

    def test_se_guardan_las_tres_casillas_y_el_numero_compuesto(self):
        respuesta = self.capturar(ped_aduana='24', ped_patente='1780',
                                  ped_consecutivo='6003555',
                                  eta='2026-09-10', has_serial_numbers='1')
        self.assertEqual(respuesta.status_code, 200)
        op = WarehouseOperation.objects.latest('id')
        self.assertEqual(op.pedimento, '24-1780-6003555')
        self.assertEqual(op.ped_patente, '1780')
        self.assertEqual(str(op.eta), '2026-09-10')
        self.assertTrue(op.has_serial_numbers)

    def test_un_cero_de_mas_se_caza_antes_de_guardar(self):
        antes = WarehouseOperation.objects.count()
        respuesta = self.capturar(ped_aduana='24', ped_patente='1780',
                                  ped_consecutivo='60035550')
        self.assertEqual(respuesta.status_code, 422)
        self.assertIn('7', respuesta.content.decode())
        self.assertEqual(WarehouseOperation.objects.count(), antes)

    def test_sin_pedimento_la_captura_pasa(self):
        # Es el caso normal: la mercancia llega antes que el numero.
        self.assertEqual(self.capturar().status_code, 200)
        self.assertEqual(WarehouseOperation.objects.latest('id').pedimento, '')

    def test_el_eta_mal_escrito_no_tumba_la_captura(self):
        # Quien recibe mercancia a las tres de la manana no tiene por que
        # pelearse con un formato de fecha en un campo opcional.
        self.assertEqual(self.capturar(eta='manana').status_code, 200)
        self.assertIsNone(WarehouseOperation.objects.latest('id').eta)


class CorreccionDesdeLaPantallaTests(BaseDeAlmacen):
    """
    Corregir un pedimento pasa por las mismas comprobaciones que teclearlo.

    Si no, la pantalla de edicion seria la puerta de atras por la que entra el
    numero mal formado: en la captura el sistema lo caza, y en la correccion
    -- que es justo donde se va a arreglar un numero equivocado -- pasaria sin
    mirarlo.
    """

    # La fecha va escrita y no leida de la operacion: `date` tiene por defecto
    # `timezone.now`, que en memoria es un datetime, y devolverlo tal cual al
    # formulario manda una hora donde se espera una fecha.
    DIA = '2026-09-07'

    def setUp(self):
        self.client.force_login(self.jefa)
        self.op = self.operacion('ED-E1', date=self.DIA, bundle_qty=2,
                                 ped_aduana='24', ped_patente='1780',
                                 ped_consecutivo='6003555')

    def editar(self, **extra):
        datos = {
            'date': self.DIA, 'bundle_qty': '2',
            'description': 'Cajas',
            'ped_aduana': '24', 'ped_patente': '1780',
            'ped_consecutivo': '6003555',
        }
        datos.update(extra)
        return self.client.post(f'/operations/{self.op.pk}/edit/', datos)

    def test_se_corrige_el_numero(self):
        respuesta = self.editar(ped_patente='1781', ped_consecutivo='6000200')
        self.assertEqual(respuesta.status_code, 200)
        self.op.refresh_from_db()
        self.assertEqual(self.op.pedimento, '24-1781-6000200')

    def test_un_numero_a_medias_no_se_guarda(self):
        respuesta = self.editar(ped_consecutivo='600355')
        self.assertEqual(respuesta.status_code, 422)
        self.op.refresh_from_db()
        self.assertEqual(self.op.pedimento, '24-1780-6003555')

    def test_la_casilla_de_series_se_puede_marcar_y_desmarcar(self):
        self.editar(has_serial_numbers_present='1', has_serial_numbers='1')
        self.op.refresh_from_db()
        self.assertTrue(self.op.has_serial_numbers)
        # Sin el checkbox pero con el marcador presente: se desmarca.
        self.editar(has_serial_numbers_present='1')
        self.op.refresh_from_db()
        self.assertFalse(self.op.has_serial_numbers)

    def test_una_pantalla_sin_esas_casillas_no_borra_lo_que_hay(self):
        # Ausencia no es lo mismo que "no tiene". Hay formularios de edicion
        # que no llevan este bloque, y guardar desde ellos no puede vaciar un
        # dato que nadie toco.
        self.op.has_serial_numbers = True
        self.op.save()
        self.client.post(f'/operations/{self.op.pk}/edit/',
                         {'date': self.DIA, 'description': 'Cajas'})
        self.op.refresh_from_db()
        self.assertTrue(self.op.has_serial_numbers)
        self.assertEqual(self.op.pedimento, '24-1780-6003555')


class PantallaDeArmarPedimentosTests(BaseDeAlmacen):
    """
    La pantalla donde se reparte la mercancia en pedimentos.

    Lo que se prueba es lo que la hace util y lo que la hace segura: que el
    reparto cuadra por bultos, que el candado del agente aduanal salta sin
    guardar y devolviendo la pantalla entera -- no un fragmento suelto --, y
    que un pedimento de otra empresa sencillamente no existe.
    """

    URL = '/pedimentos/'

    def setUp(self):
        self.client.force_login(self.jefa)
        self.entrada = self.operacion('ED260901-0001', bundle_qty=10,
                                      weight_lbs=1240)

    def panel(self, cliente=None):
        return self.client.get(self.URL, {'customer': (cliente or self.cliente).pk})

    def nuevo_pedimento(self, orden=1, **extra):
        return Pedimento.objects.create(tenant=self.tenant, customer=self.cliente,
                                        orden=orden, created_by=self.jefa, **extra)

    # -- Lo que se ve --------------------------------------------------------

    def test_sin_cliente_no_se_pinta_ningun_reparto(self):
        # Juntar los pedimentos de todos los clientes en una lista no ayudaria
        # a nadie: un pedimento nunca mezcla dos clientes.
        respuesta = self.client.get(self.URL)
        self.assertEqual(respuesta.status_code, 200)
        self.assertIsNone(respuesta.context['cliente'])

    def test_los_bultos_sin_repartir_salen_contados(self):
        respuesta = self.panel()
        self.assertEqual(respuesta.context['resumen']['sin_repartir'], 10)
        self.assertEqual([op.pk for op in respuesta.context['sin_repartir']],
                         [self.entrada.pk])

    def test_lo_repartido_deja_de_estar_suelto(self):
        ped = self.nuevo_pedimento()
        PedimentoBundle.objects.create(pedimento=ped, operation=self.entrada,
                                       bultos=6)
        respuesta = self.panel()
        self.assertEqual(respuesta.context['resumen']['sin_repartir'], 4)

    def test_repartido_del_todo_desaparece_del_bloque_rojo(self):
        ped = self.nuevo_pedimento()
        PedimentoBundle.objects.create(pedimento=ped, operation=self.entrada,
                                       bultos=10)
        respuesta = self.panel()
        self.assertEqual(respuesta.context['sin_repartir'], [])
        self.assertEqual(respuesta.context['resumen']['sin_repartir'], 0)

    def test_la_aduana_y_el_agente_salen_del_numero(self):
        # No hay selector de aduana ni de agente: los dos viajan dentro del
        # numero del primer pedimento que lo tenga.
        self.nuevo_pedimento(ped_aduana='24', ped_patente='1515',
                             ped_consecutivo='6005000')
        respuesta = self.panel()
        self.assertEqual(respuesta.context['aduana'], '24')
        self.assertEqual(respuesta.context['patente'], '1515')
        self.assertEqual(str(respuesta.context['nombre_de_aduana']), 'Nuevo Laredo')

    # -- El reparto ----------------------------------------------------------

    def test_asignar_bultos_con_su_peso(self):
        ped = self.nuevo_pedimento()
        self.client.post('/pedimentos/%d/assign/' % ped.pk,
                         {'operation': self.entrada.pk, 'bultos': '6',
                          'weight_lbs': '748'})
        renglon = ped.renglones.get()
        self.assertEqual(renglon.bultos, 6)
        # Los kilos los pone el sistema: se teclea en libras porque asi llegan
        # los embarques, y nadie convierte a mano.
        self.assertEqual(str(renglon.weight_lbs), '748.00')
        self.assertEqual(str(renglon.weight_kgs), '339.29')

    def test_el_peso_es_opcional(self):
        # Quien reparte a veces todavia no lo tiene, y no dejar guardar por eso
        # pararia el trabajo por un dato que llega despues.
        ped = self.nuevo_pedimento()
        self.client.post('/pedimentos/%d/assign/' % ped.pk,
                         {'operation': self.entrada.pk, 'bultos': '3'})
        renglon = ped.renglones.get()
        self.assertEqual(renglon.bultos, 3)
        self.assertIsNone(renglon.weight_lbs)
        self.assertIsNone(renglon.weight_kgs)

    def test_no_se_pueden_asignar_mas_bultos_de_los_que_llegaron(self):
        # "19 de 20" es normal y se apoya; "21 de 20" es un error de dedo, y
        # dejarlo pasar solo aplaza el problema hasta el anden.
        ped = self.nuevo_pedimento()
        respuesta = self.client.post('/pedimentos/%d/assign/' % ped.pk,
                                     {'operation': self.entrada.pk, 'bultos': '11'})
        self.assertEqual(respuesta.status_code, 422)
        self.assertEqual(ped.renglones.count(), 0)

    def test_los_19_de_20(self):
        ped = self.nuevo_pedimento()
        self.client.post('/pedimentos/%d/assign/' % ped.pk,
                         {'operation': self.entrada.pk, 'bultos': '9'})
        # El bulto que sobra sigue en el bloque rojo hasta que alguien diga que
        # pasa con el.
        self.assertEqual(self.panel().context['resumen']['sin_repartir'], 1)

    def test_asignar_dos_veces_suma_en_el_mismo_renglon(self):
        ped = self.nuevo_pedimento()
        for cuantos in ('2', '3'):
            self.client.post('/pedimentos/%d/assign/' % ped.pk,
                             {'operation': self.entrada.pk, 'bultos': cuantos})
        self.assertEqual(ped.renglones.count(), 1)
        self.assertEqual(ped.renglones.get().bultos, 5)

    def test_quitar_un_renglon_devuelve_los_bultos(self):
        ped = self.nuevo_pedimento()
        renglon = PedimentoBundle.objects.create(pedimento=ped,
                                                 operation=self.entrada, bultos=4)
        self.client.post('/pedimentos/%d/unassign/' % ped.pk,
                         {'renglon': renglon.pk})
        self.assertEqual(self.panel().context['resumen']['sin_repartir'], 10)

    # -- El candado ----------------------------------------------------------

    def test_el_candado_no_guarda_y_devuelve_la_pantalla_entera(self):
        self.nuevo_pedimento(orden=1, ped_aduana='24', ped_patente='1515',
                             ped_consecutivo='6005000')
        otro = self.nuevo_pedimento(orden=2)

        respuesta = self.client.post('/pedimentos/%d/number/' % otro.pk,
                                     {'ped_aduana': '24', 'ped_patente': '1781',
                                      'ped_consecutivo': '6000200'})
        self.assertEqual(respuesta.status_code, 422)
        otro.refresh_from_db()
        self.assertFalse(otro.tiene_numero)

        # La pantalla vuelve entera, con el aviso dentro. Devolver un fragmento
        # suelto dejaba al usuario en una pagina en blanco con una frase: el
        # mensaje correcto y la pantalla inservible.
        cuerpo = respuesta.content.decode()
        self.assertIn('1781', cuerpo)
        self.assertIn('1515', cuerpo)
        self.assertIn('</html>', cuerpo)
        # Y lo tecleado vuelve a sus casillas, para corregir un digito en vez
        # de escribirlo entero otra vez.
        self.assertEqual(respuesta.context['intento']['patente'], '1781')

    def test_la_misma_patente_se_guarda(self):
        self.nuevo_pedimento(orden=1, ped_aduana='24', ped_patente='1515',
                             ped_consecutivo='6005000')
        otro = self.nuevo_pedimento(orden=2)
        respuesta = self.client.post('/pedimentos/%d/number/' % otro.pk,
                                     {'ped_aduana': '24', 'ped_patente': '1515',
                                      'ped_consecutivo': '6005001'})
        self.assertEqual(respuesta.status_code, 302)
        otro.refresh_from_db()
        self.assertEqual(otro.numero, '24-1515-6005001')

    def test_un_cero_de_mas_tampoco_pasa_aqui(self):
        ped = self.nuevo_pedimento()
        respuesta = self.client.post('/pedimentos/%d/number/' % ped.pk,
                                     {'ped_aduana': '24', 'ped_patente': '1515',
                                      'ped_consecutivo': '60050000'})
        self.assertEqual(respuesta.status_code, 422)
        ped.refresh_from_db()
        self.assertFalse(ped.tiene_numero)

    # -- Quien puede que -----------------------------------------------------

    def test_un_pedimento_vacio_se_puede_borrar(self):
        ped = self.nuevo_pedimento()
        self.client.post('/pedimentos/%d/delete/' % ped.pk)
        self.assertFalse(Pedimento.objects.filter(pk=ped.pk).exists())

    def test_uno_que_ya_salio_a_revision_no_se_borra(self):
        # Es algo que el cliente tiene delante; hacerlo desaparecer de su
        # pantalla no es una correccion, es dejarle mirando un hueco.
        ped = self.nuevo_pedimento(estado=Pedimento.EN_REVISION)
        respuesta = self.client.post('/pedimentos/%d/delete/' % ped.pk)
        self.assertEqual(respuesta.status_code, 422)
        self.assertTrue(Pedimento.objects.filter(pk=ped.pk).exists())

    def test_el_pedimento_de_otra_empresa_no_existe(self):
        otro_tenant = Tenant.objects.create(name='Bodegas del Sur',
                                            type='organization', subdomain='sur')
        cliente_ajeno = Catalog.objects.create(category='CUSTOMER', name='Zeta',
                                               tenant=otro_tenant)
        ajeno = Pedimento.objects.create(tenant=otro_tenant, customer=cliente_ajeno)
        respuesta = self.client.post('/pedimentos/%d/number/' % ajeno.pk,
                                     {'ped_aduana': '24', 'ped_patente': '1515',
                                      'ped_consecutivo': '6005000'})
        self.assertEqual(respuesta.status_code, 404)
