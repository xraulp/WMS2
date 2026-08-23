"""
El hilo de mensajes de una operacion, entre la empresa y su cliente.

Lo que se prueba aqui es lo que no se ve mirando la pantalla: que solo alcance
el hilo quien alcanza la operacion, que el lado del mensaje lo ponga el
servidor y no el formulario, que la marca de lectura cuente lo que debe, y que
el aviso por correo salga una vez y se calle mientras la conversacion sigue
viva -- que es lo unico que separa un aviso util de diez correos seguidos.
"""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.utils import timezone

from . import notifications
from .models import (Catalog, Conversation, LADO_CLIENTE, LADO_TENANT, Message,
                     NotificationLog, Tenant, UserProfile, WarehouseOperation)


class HiloBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacen Uno', type='organization', subdomain='uno')
        cls.otro_tenant = Tenant.objects.create(
            name='Almacen Dos', type='organization', subdomain='dos')

        cls.cliente_a = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Cliente A',
            contact_email='clientea@example.com')
        cls.cliente_b = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Cliente B',
            contact_email='clienteb@example.com')

        cls.op = WarehouseOperation.objects.create(
            tenant=cls.tenant, operation_type='ENTRY',
            custom_id='OP-A', customer=cls.cliente_a)
        cls.op_b = WarehouseOperation.objects.create(
            tenant=cls.tenant, operation_type='ENTRY',
            custom_id='OP-B', customer=cls.cliente_b)
        cls.op_ajena = WarehouseOperation.objects.create(
            tenant=cls.otro_tenant, operation_type='ENTRY',
            custom_id='OP-Z', customer=None)

        cls.staff = User.objects.create_user(
            'operador', password='x', email='operador@almacen.com',
            first_name='Ana', last_name='Ruiz')
        UserProfile.objects.create(user=cls.staff, tenant=cls.tenant, role='staff')

        cls.admin = User.objects.create_user(
            'jefa', password='x', email='jefa@almacen.com')
        UserProfile.objects.create(user=cls.admin, tenant=cls.tenant, role='admin')

        cls.usuario_a = User.objects.create_user(
            'cliente_a', password='x', email='ana@clientea.com')
        UserProfile.objects.create(user=cls.usuario_a, tenant=cls.tenant,
                                   role='customer', customer=cls.cliente_a)

        cls.usuario_b = User.objects.create_user(
            'cliente_b', password='x', email='beto@clienteb.com')
        UserProfile.objects.create(user=cls.usuario_b, tenant=cls.tenant,
                                   role='customer', customer=cls.cliente_b)

        # Existe en auth_user y nada mas: sin perfil no tiene empresa.
        cls.sin_perfil = User.objects.create_user('nadie', password='x')

        # Tiene perfil en la empresa pero con el rol en blanco, que es lo que
        # queda cuando alguien se da de alta a medias. Lleva correo a proposito:
        # es lo que hace visible si `correos_del_tenant` filtra de verdad o se
        # limita a excluir a los clientes.
        cls.sin_rol = User.objects.create_user(
            'a_medias', password='x', email='a_medias@almacen.com')
        UserProfile.objects.create(user=cls.sin_rol, tenant=cls.tenant, role='')

    def _escribir(self, user, op, texto):
        self.client.force_login(user)
        return self.client.post(f'/operations/{op.pk}/chat/send/', {'body': texto})


class EscribirYLeerTests(HiloBase):
    def test_el_primer_mensaje_crea_el_hilo(self):
        """Una operacion sobre la que nadie dijo nada no tiene fila esperando."""
        self.assertFalse(Conversation.objects.filter(operation=self.op).exists())

        resp = self._escribir(self.staff, self.op, 'Su carga llego completa.')

        self.assertEqual(resp.status_code, 200)
        hilo = Conversation.objects.get(operation=self.op)
        self.assertEqual(hilo.tenant, self.tenant)
        self.assertEqual(hilo.messages.count(), 1)
        self.assertIsNotNone(hilo.last_message_at)

    def test_el_lado_lo_pone_el_servidor(self):
        """
        El formulario no manda el lado, y aunque lo mandara no se le hace caso:
        un cliente que escribiera con side=TENANT firmaria como la empresa.
        """
        self.client.force_login(self.usuario_a)
        self.client.post(f'/operations/{self.op.pk}/chat/send/',
                         {'body': 'Ya la recogimos', 'side': LADO_TENANT})

        mensaje = Message.objects.get()
        self.assertEqual(mensaje.side, LADO_CLIENTE)
        self.assertTrue(mensaje.es_del_cliente)

    def test_el_nombre_de_quien_escribe_queda_congelado(self):
        """Si manana se da de baja a quien escribio, el hilo sigue diciendolo."""
        self._escribir(self.staff, self.op, 'Listo.')
        mensaje = Message.objects.get()
        self.assertEqual(mensaje.author_name, 'Ana Ruiz')

        self.staff.delete()
        mensaje.refresh_from_db()
        self.assertIsNone(mensaje.author)
        self.assertEqual(mensaje.author_name, 'Ana Ruiz')

    def test_los_dos_lados_ven_la_misma_conversacion(self):
        self._escribir(self.staff, self.op, 'Su carga llego completa.')
        self._escribir(self.usuario_a, self.op, 'Gracias, mando el pedimento.')

        self.client.force_login(self.admin)
        resp = self.client.get(f'/operations/{self.op.pk}/chat/')
        cuerpo = resp.content.decode()
        self.assertIn('Su carga llego completa.', cuerpo)
        self.assertIn('mando el pedimento', cuerpo)

    def test_un_mensaje_vacio_no_escribe_nada(self):
        resp = self._escribir(self.staff, self.op, '   ')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Message.objects.count(), 0)
        self.assertContains(resp, 'Escribe un mensaje')

    def test_un_mensaje_demasiado_largo_se_rechaza(self):
        from .views import MENSAJE_MAX
        resp = self._escribir(self.staff, self.op, 'x' * (MENSAJE_MAX + 1))
        self.assertEqual(Message.objects.count(), 0)
        self.assertContains(resp, 'caracteres')

    def test_los_mensajes_salen_en_orden(self):
        for texto in ('uno', 'dos', 'tres'):
            self._escribir(self.staff, self.op, texto)
        hilo = Conversation.objects.get(operation=self.op)
        self.assertEqual([m.body for m in hilo.messages.all()], ['uno', 'dos', 'tres'])


class PermisosDelHiloTests(HiloBase):
    def test_un_cliente_no_alcanza_el_hilo_de_otro_cliente(self):
        self.client.force_login(self.usuario_a)
        self.assertEqual(
            self.client.get(f'/operations/{self.op_b.pk}/chat/').status_code, 403)
        self.assertEqual(
            self.client.post(f'/operations/{self.op_b.pk}/chat/send/',
                             {'body': 'hola'}).status_code, 403)
        self.assertEqual(Message.objects.count(), 0)

    def test_quien_tiene_el_rol_en_blanco_no_escribe_ni_lee(self):
        """Un alta a medias no da voz en el hilo: fail-closed, como el resto."""
        self.client.force_login(self.sin_rol)
        self.assertEqual(
            self.client.get(f'/operations/{self.op.pk}/chat/').status_code, 403)
        self.assertEqual(
            self.client.post(f'/operations/{self.op.pk}/chat/send/',
                             {'body': 'hola'}).status_code, 403)
        self.assertEqual(Message.objects.count(), 0)

    def test_quien_no_tiene_perfil_no_llega_ni_a_la_puerta(self):
        """
        Existir en auth_user no es pertenecer a una empresa. Sin perfil no hay
        tenant, y sin tenant `get_tenant_or_404` corta antes: 404 y no 403,
        porque para ese usuario la operacion no existe.
        """
        self.client.force_login(self.sin_perfil)
        self.assertEqual(
            self.client.get(f'/operations/{self.op.pk}/chat/').status_code, 404)
        self.assertEqual(
            self.client.post(f'/operations/{self.op.pk}/chat/send/',
                             {'body': 'hola'}).status_code, 404)

    def test_el_hilo_de_otra_empresa_no_existe(self):
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.get(f'/operations/{self.op_ajena.pk}/chat/').status_code, 404)

    def test_el_hilo_no_se_escribe_con_get(self):
        self.client.force_login(self.staff)
        self.client.get(f'/operations/{self.op.pk}/chat/send/')
        self.assertEqual(Message.objects.count(), 0)

    def test_el_detalle_no_ofrece_hilo_sin_cliente_en_el_catalogo(self):
        """
        Una operacion con el cliente escrito a mano y fuera del catalogo no
        tiene usuarios del otro lado: ofrecer el hilo seria ofrecer un buzon
        que nadie abre.
        """
        suelta = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id='OP-S',
            customer=None, customer_name_manual='Alguien Sin Alta')
        self.client.force_login(self.staff)
        resp = self.client.get(f'/operations/{suelta.pk}/')
        self.assertFalse(resp.context['hay_con_quien'])
        self.assertNotContains(resp, 'chat-mensajes')

        resp = self.client.get(f'/operations/{self.op.pk}/')
        self.assertTrue(resp.context['hay_con_quien'])
        self.assertContains(resp, 'chat-mensajes')


class MarcaDeLecturaTests(HiloBase):
    def test_lo_que_escribe_el_otro_queda_sin_leer(self):
        self._escribir(self.usuario_a, self.op, 'Necesito la factura.')
        hilo = Conversation.objects.get(operation=self.op)
        self.assertEqual(hilo.sin_leer_para(self.staff), 1)

    def _hilo_con_marca_vieja(self, autor, cuerpo, lado):
        """
        Un mensaje escrito *despues* de la ultima vez que su autor abrio el
        hilo, saltandose la vista.

        Hace falta montarlo a mano porque al escribir por la pantalla el hilo
        se repinta y eso lo da por leido con la hora de ese momento -- la
        marca acaba siendo posterior al mensaje y tapa el caso. Esa segunda red
        esta en `_pintar_hilo`, y es la que hay que rodear para comprobar que
        `sin_leer_para` excluye los propios por si misma.
        """
        hilo = Conversation.objects.create(operation=self.op, tenant=self.tenant)
        hilo.marcar_leida(autor, timezone.now() - timedelta(hours=1))
        mensaje = Message.objects.create(
            conversation=hilo, author=autor, author_name=autor.username,
            side=lado, body=cuerpo)
        return hilo, mensaje

    def test_los_mensajes_propios_nunca_cuentan(self):
        """Uno no tiene mensajes sin leer de si mismo, ni aunque su marca sea vieja."""
        hilo, _ = self._hilo_con_marca_vieja(self.staff, 'Va en camino.', LADO_TENANT)
        self.assertEqual(hilo.sin_leer_para(self.staff), 0)
        # Y para el de al lado, que no lo escribio, si cuenta.
        self.assertEqual(hilo.sin_leer_para(self.admin), 1)

    def test_el_listado_tampoco_cuenta_los_propios(self):
        from .views import anotar_hilos

        self._hilo_con_marca_vieja(self.staff, 'Va en camino.', LADO_TENANT)
        anotadas = {op.pk: op for op in anotar_hilos(self.staff, [self.op])}
        self.assertEqual(anotadas[self.op.pk].sin_leer, 0)
        self.assertTrue(anotadas[self.op.pk].tiene_hilo)

    def test_el_mensaje_leido_justo_hasta_ahi_no_vuelve_a_contar(self):
        """
        Quien leyo hasta el instante exacto de un mensaje ya lo vio. Con el
        limite mal puesto, el ultimo mensaje de cada visita se quedaria
        encendido para siempre.
        """
        hilo = Conversation.objects.create(operation=self.op, tenant=self.tenant)
        mensaje = Message.objects.create(
            conversation=hilo, author=self.usuario_a, author_name='cliente_a',
            side=LADO_CLIENTE, body='Necesito la factura.')
        hilo.marcar_leida(self.staff, mensaje.created_at)

        self.assertEqual(hilo.sin_leer_para(self.staff), 0)

    def test_abrir_el_hilo_lo_da_por_leido(self):
        self._escribir(self.usuario_a, self.op, 'Necesito la factura.')
        hilo = Conversation.objects.get(operation=self.op)

        self.client.force_login(self.staff)
        self.client.get(f'/operations/{self.op.pk}/chat/')
        self.assertEqual(hilo.sin_leer_para(self.staff), 0)

    def test_la_lectura_es_de_cada_persona(self):
        """
        Que lo haya abierto un operador no significa que los demas se
        enteraron: del lado de la empresa hay varias personas mirando el mismo
        hilo.
        """
        self._escribir(self.usuario_a, self.op, 'Necesito la factura.')
        hilo = Conversation.objects.get(operation=self.op)

        self.client.force_login(self.staff)
        self.client.get(f'/operations/{self.op.pk}/chat/')

        self.assertEqual(hilo.sin_leer_para(self.staff), 0)
        self.assertEqual(hilo.sin_leer_para(self.admin), 1)

    def test_lo_que_llega_despues_de_leer_vuelve_a_contar(self):
        self._escribir(self.usuario_a, self.op, 'Necesito la factura.')
        self.client.force_login(self.staff)
        self.client.get(f'/operations/{self.op.pk}/chat/')

        self._escribir(self.usuario_a, self.op, 'Y el pedimento.')
        hilo = Conversation.objects.get(operation=self.op)
        self.assertEqual(hilo.sin_leer_para(self.staff), 1)


class IndicadorEnElListadoTests(HiloBase):
    """
    El aviso en la tabla de operaciones. Sin el, un mensaje del cliente solo se
    descubre abriendo la operacion que a nadie se le ocurre abrir.
    """

    def test_el_listado_marca_lo_que_esta_sin_leer(self):
        self._escribir(self.usuario_a, self.op, 'Necesito la factura.')

        self.client.force_login(self.staff)
        resp = self.client.get('/operations/search/')
        marcadas = {op.pk: op.sin_leer for op in resp.context['operations']}
        self.assertEqual(marcadas[self.op.pk], 1)
        self.assertEqual(marcadas[self.op_b.pk], 0)

    def test_una_operacion_sin_hilo_no_lleva_marca(self):
        self.client.force_login(self.staff)
        resp = self.client.get('/operations/search/')
        for op in resp.context['operations']:
            self.assertFalse(op.tiene_hilo)
            self.assertEqual(op.sin_leer, 0)

    def test_el_hilo_al_dia_se_distingue_del_que_tiene_pendientes(self):
        self._escribir(self.staff, self.op, 'Su carga llego completa.')

        self.client.force_login(self.staff)
        resp = self.client.get('/operations/search/')
        anotadas = {op.pk: op for op in resp.context['operations']}
        self.assertTrue(anotadas[self.op.pk].tiene_hilo)
        self.assertEqual(anotadas[self.op.pk].sin_leer, 0)

    def test_el_indicador_no_cuesta_una_consulta_por_fila(self):
        """
        El listado trae hasta doscientas operaciones. Si el conteo creciera con
        las filas, la tabla dejaria de cargar en cuanto la empresa lleve unos
        meses trabajando.
        """
        from .views import anotar_hilos

        for i in range(6):
            op = WarehouseOperation.objects.create(
                tenant=self.tenant, operation_type='ENTRY',
                custom_id=f'OP-N{i}', customer=self.cliente_a)
            self._escribir(self.usuario_a, op, f'Mensaje {i}')

        muchas = list(WarehouseOperation.objects.filter(tenant=self.tenant))
        with self.assertNumQueries(2):
            anotadas = anotar_hilos(self.staff, muchas)

        self.assertEqual(sum(op.sin_leer for op in anotadas), 6)


class AvisosAlDiaSinRecargarTests(HiloBase):
    """
    La tabla se pinta una vez y se queda quieta. Estos son los numeros que la
    ponen al dia sin recargarla: sin ellos, un mensaje que llega no se ve hasta
    que a alguien se le ocurre recargar, y el aviso de un hilo ya leido sigue
    encendido mintiendo.
    """

    def test_devuelve_lo_que_falta_por_leer(self):
        self._escribir(self.usuario_a, self.op, 'Necesito la factura.')

        self.client.force_login(self.staff)
        datos = self.client.get('/operations/chat-badges/').json()
        self.assertEqual(datos['hilos'][str(self.op.pk)], 1)

    def test_no_menciona_las_operaciones_sin_hilo(self):
        """Una operacion sobre la que nadie dijo nada no tiene nada que encender."""
        self._escribir(self.staff, self.op, 'Su carga llego completa.')

        self.client.force_login(self.staff)
        datos = self.client.get('/operations/chat-badges/').json()
        self.assertIn(str(self.op.pk), datos['hilos'])
        self.assertNotIn(str(self.op_b.pk), datos['hilos'])

    def test_un_hilo_leido_va_en_cero_y_no_desaparece(self):
        """
        Tiene que seguir apareciendo con cero: es lo que distingue «hay
        conversacion y esta al dia» de «no hay conversacion», y lo que apaga el
        ambar sin borrar el boton.
        """
        self._escribir(self.staff, self.op, 'Su carga llego completa.')

        self.client.force_login(self.staff)
        datos = self.client.get('/operations/chat-badges/').json()
        self.assertEqual(datos['hilos'][str(self.op.pk)], 0)

    def test_un_cliente_no_se_entera_de_los_hilos_de_otro(self):
        self._escribir(self.staff, self.op, 'Su carga llego completa.')
        self._escribir(self.staff, self.op_b, 'La suya tambien.')

        self.client.force_login(self.usuario_a)
        datos = self.client.get('/operations/chat-badges/').json()
        self.assertIn(str(self.op.pk), datos['hilos'])
        self.assertNotIn(str(self.op_b.pk), datos['hilos'])

    def test_quien_no_tiene_rol_no_recibe_nada(self):
        self._escribir(self.staff, self.op, 'Su carga llego completa.')

        self.client.force_login(self.sin_rol)
        datos = self.client.get('/operations/chat-badges/').json()
        self.assertEqual(datos['hilos'], {})


class AvisoPorCorreoTests(HiloBase):
    def setUp(self):
        mail.outbox = []

    def test_lo_que_escribe_el_cliente_le_llega_a_los_operadores(self):
        self._escribir(self.usuario_a, self.op, 'Necesito la factura.')

        self.assertEqual(len(mail.outbox), 1)
        destinos = set(mail.outbox[0].to)
        self.assertEqual(destinos, {'operador@almacen.com', 'jefa@almacen.com'})
        self.assertNotIn('ana@clientea.com', destinos)

    def test_lo_que_escribe_la_empresa_le_llega_al_cliente(self):
        self._escribir(self.staff, self.op, 'Su carga llego completa.')

        self.assertEqual(len(mail.outbox), 1)
        destinos = set(mail.outbox[0].to)
        self.assertIn('clientea@example.com', destinos)
        self.assertIn('ana@clientea.com', destinos)
        self.assertNotIn('operador@almacen.com', destinos)

    def test_el_correo_lleva_el_mensaje_y_a_donde_responder(self):
        self._escribir(self.staff, self.op, 'Falta la etiqueta del lote 7.')
        cuerpo = mail.outbox[0].body
        self.assertIn('Falta la etiqueta del lote 7.', cuerpo)
        self.assertIn('OP-A', cuerpo)

    def test_el_segundo_mensaje_seguido_no_manda_otro_correo(self):
        """
        Una conversacion de diez lineas no puede ser diez correos: a la tercera
        vez el destinatario deja de abrirlos.
        """
        self._escribir(self.staff, self.op, 'Uno.')
        self._escribir(self.staff, self.op, 'Dos.')
        self._escribir(self.staff, self.op, 'Tres.')

        self.assertEqual(len(mail.outbox), 1)

    def test_el_silencio_queda_registrado_con_su_motivo(self):
        """La bitacora tiene que poder responder por que no se aviso."""
        self._escribir(self.staff, self.op, 'Uno.')
        self._escribir(self.staff, self.op, 'Dos.')

        callado = NotificationLog.objects.filter(
            event=notifications.EVENT_MESSAGE, status=notifications.SKIPPED).first()
        self.assertIsNotNone(callado)
        self.assertEqual(callado.detail, 'aviso_reciente')
        self.assertEqual(callado.operation, self.op)

    def test_pasada_la_espera_vuelve_a_avisar(self):
        self._escribir(self.staff, self.op, 'Uno.')
        hilo = Conversation.objects.get(operation=self.op)
        hilo.avisado_al_cliente_at = (
            timezone.now() - notifications.AVISO_ESPERA - timedelta(minutes=1))
        hilo.save(update_fields=['avisado_al_cliente_at'])

        self._escribir(self.staff, self.op, 'Dos.')
        self.assertEqual(len(mail.outbox), 2)

    def test_cada_lado_lleva_su_propia_espera(self):
        """
        Que se le haya avisado al cliente no puede callar el aviso a la
        empresa: son dos buzones distintos y dos conversaciones a medias.
        """
        self._escribir(self.staff, self.op, 'Su carga llego completa.')
        self.assertEqual(len(mail.outbox), 1)

        self._escribir(self.usuario_a, self.op, 'Gracias.')
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn('operador@almacen.com', mail.outbox[1].to)

    def test_un_fallo_avisando_no_impide_que_el_mensaje_se_escriba(self):
        """
        Lo importante es lo que se dijo; el correo es secundario. Si el envio
        revienta, el mensaje ya esta en el hilo.
        """
        with patch.object(notifications, 'correos_del_tenant',
                          side_effect=RuntimeError('sin correo')):
            resp = self._escribir(self.usuario_a, self.op, 'Necesito la factura.')

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Message.objects.count(), 1)
