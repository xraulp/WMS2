"""
El hilo de mensajes de una operacion, entre la empresa y su cliente.

Lo que se prueba aqui es lo que no se ve mirando la pantalla: que solo alcance
el hilo quien alcanza la operacion, que el lado del mensaje lo ponga el
servidor y no el formulario, que la marca de lectura cuente lo que debe, y que
el aviso por correo salga una vez y se calle mientras la conversacion sigue
viva -- que es lo unico que separa un aviso util de diez correos seguidos.
"""
import tempfile
import threading
import time
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from . import notifications
from .models import (Catalog, Conversation, LADO_CLIENTE, LADO_TENANT, Message,
                     NotificationLog, OperationDocument, Tenant, UserProfile,
                     WarehouseOperation)

# Los archivos de prueba van a un directorio temporal, no al bucket.
STORAGE_LOCAL = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': tempfile.mkdtemp()}},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})


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
        self.assertContains(resp, 'Write a message')

    def test_un_mensaje_demasiado_largo_se_rechaza(self):
        from .views import MENSAJE_MAX
        resp = self._escribir(self.staff, self.op, 'x' * (MENSAJE_MAX + 1))
        self.assertEqual(Message.objects.count(), 0)
        self.assertContains(resp, 'characters')

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


class ContadorGlobalTests(HiloBase):
    """
    El contador de mensajes sin leer que va junto a las pestanas, y la tabla
    acotada a la que lleva.

    El aviso ambar de una fila solo sirve si esa fila esta a la vista, y la
    tabla trae doscientas operaciones ordenadas por fecha: un mensaje sobre una
    carga de hace tres meses no lo ve nadie. Esto es lo que hace que se vea.
    """

    def test_cuenta_todo_lo_que_espera_a_esa_persona(self):
        self._escribir(self.usuario_a, self.op, 'Necesito la factura.')
        self._escribir(self.usuario_b, self.op_b, 'La mia cuando llega?')
        self._escribir(self.usuario_b, self.op_b, 'Es urgente.')

        self.client.force_login(self.staff)
        datos = self.client.get('/operations/chat-badges/').json()
        self.assertEqual(datos['mensajes'], 3)
        self.assertEqual(datos['operaciones'], 2)

    def test_los_propios_no_cuentan_en_el_total(self):
        """Un contador que sube cuando uno mismo escribe no avisa de nada."""
        self._escribir(self.staff, self.op, 'Su carga llego completa.')

        self.client.force_login(self.staff)
        datos = self.client.get('/operations/chat-badges/').json()
        self.assertEqual(datos['mensajes'], 0)
        self.assertEqual(datos['operaciones'], 0)

    def test_cada_cliente_solo_cuenta_lo_suyo(self):
        self._escribir(self.staff, self.op, 'Su carga llego completa.')
        self._escribir(self.staff, self.op_b, 'La suya tambien.')

        self.client.force_login(self.usuario_a)
        datos = self.client.get('/operations/chat-badges/').json()
        self.assertEqual(datos['mensajes'], 1)
        self.assertEqual(datos['operaciones'], 1)

    def test_quien_no_tiene_rol_no_tiene_contador(self):
        self._escribir(self.staff, self.op, 'Su carga llego completa.')

        self.client.force_login(self.sin_rol)
        datos = self.client.get('/operations/chat-badges/').json()
        self.assertEqual(datos['mensajes'], 0)
        self.assertEqual(datos['operaciones'], 0)

    def test_los_propios_no_cuentan_ni_sin_marca_de_lectura(self):
        """
        Escribir por la pantalla deja marca de lectura, y esa marca ya tapa los
        mensajes propios aunque el conteo no los excluyera. Aqui el mensaje se
        crea por el ORM, sin marca, que es como entraria por un comando o por
        una API: es la unica forma de comprobar que el conteo los descarta por
        su cuenta.
        """
        hilo = Conversation.objects.create(operation=self.op, tenant=self.tenant)
        Message.objects.create(conversation=hilo, author=self.staff,
                               author_name='operador', side=LADO_TENANT,
                               body='Su carga llego completa.')

        self.client.force_login(self.staff)
        datos = self.client.get('/operations/chat-badges/').json()
        self.assertEqual(datos['mensajes'], 0)
        self.assertEqual(datos['operaciones'], 0)

    def test_el_tablero_no_le_cuenta_nada_a_quien_no_tiene_rol(self):
        """
        `chat_badges` corta antes a quien no tiene lado, pero el tablero llama
        al conteo directamente: sin su propia guarda, un alta a medias veria en
        el contador cuantos mensajes se estan cruzando en la empresa.
        """
        self._escribir(self.usuario_a, self.op, 'Necesito la factura.')

        self.client.force_login(self.sin_rol)
        resp = self.client.get('/dashboard/')
        self.assertEqual(resp.context['sin_leer']['mensajes'], 0)

    def test_el_contador_baja_al_leer(self):
        self._escribir(self.usuario_a, self.op, 'Necesito la factura.')
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.get('/operations/chat-badges/').json()['mensajes'], 1)

        self.client.get(f'/operations/{self.op.pk}/chat/')

        self.assertEqual(
            self.client.get('/operations/chat-badges/').json()['mensajes'], 0)

    def test_el_tablero_trae_el_contador_en_la_primera_carga(self):
        """
        Quien entra y no toca nada durante treinta segundos tiene que ver que
        le escribieron: el refresco por JavaScript llega despues.
        """
        self._escribir(self.usuario_a, self.op, 'Necesito la factura.')

        self.client.force_login(self.staff)
        resp = self.client.get('/dashboard/')
        self.assertEqual(resp.context['sin_leer']['mensajes'], 1)


class TablaSoloSinLeerTests(HiloBase):
    """
    La tabla acotada a las operaciones que esperan respuesta, que es a donde
    lleva el contador. Sin ella el contador seria un numero sin destino.
    """

    def test_trae_solo_las_que_tienen_algo_sin_leer(self):
        self._escribir(self.usuario_a, self.op, 'Necesito la factura.')
        self._escribir(self.staff, self.op_b, 'Su carga llego completa.')

        self.client.force_login(self.staff)
        resp = self.client.get('/operations/unread/')
        traidas = [op.pk for op in resp.context['operations']]
        self.assertEqual(traidas, [self.op.pk])

    def test_sigue_apareciendo_aunque_uno_haya_escrito_en_ese_hilo(self):
        """
        El caso normal: la empresa contesta y el cliente vuelve a escribir.
        Descartar los hilos donde uno ha participado dejaria fuera justo las
        conversaciones vivas, que son las unicas que importan.
        """
        self._escribir(self.staff, self.op, 'Su carga llego completa.')
        self._escribir(self.usuario_a, self.op, 'Falta un bulto.')

        self.client.force_login(self.staff)
        resp = self.client.get('/operations/unread/')
        traidas = [op.pk for op in resp.context['operations']]
        self.assertEqual(traidas, [self.op.pk])

    def test_no_trae_una_operacion_por_los_mensajes_de_uno_mismo(self):
        """
        Igual que en el contador: el mensaje se crea por el ORM, sin la marca
        de lectura que deja escribir por la pantalla, para que lo que se
        compruebe sea el conteo y no esa marca.
        """
        hilo = Conversation.objects.create(operation=self.op, tenant=self.tenant)
        Message.objects.create(conversation=hilo, author=self.staff,
                               author_name='operador', side=LADO_TENANT,
                               body='Su carga llego completa.')

        self.client.force_login(self.staff)
        resp = self.client.get('/operations/unread/')
        self.assertEqual(list(resp.context['operations']), [])

    def test_lo_ultimo_que_llego_va_primero(self):
        """
        Se ordena por el ultimo mensaje y no por la fecha de la operacion:
        aqui lo que se busca es la conversacion viva, no la carga reciente.

        Las dos fechas se ponen al reves a proposito -- la operacion reciente
        es la que lleva el mensaje viejo -- porque si coincidieran, ordenar por
        fecha daria el mismo resultado y la prueba no diria nada.
        """
        hoy = timezone.now().date()
        WarehouseOperation.objects.filter(pk=self.op.pk).update(date=hoy)
        WarehouseOperation.objects.filter(pk=self.op_b.pk).update(
            date=hoy - timedelta(days=30))

        self._escribir(self.usuario_a, self.op, 'Necesito la factura.')
        self._escribir(self.usuario_b, self.op_b, 'La mia cuando llega?')

        self.client.force_login(self.staff)
        resp = self.client.get('/operations/unread/')
        traidas = [op.pk for op in resp.context['operations']]
        self.assertEqual(traidas, [self.op_b.pk, self.op.pk])

    def test_cada_fila_llega_con_su_numero(self):
        self._escribir(self.usuario_a, self.op, 'Necesito la factura.')
        self._escribir(self.usuario_a, self.op, 'Y el pedimento.')

        self.client.force_login(self.staff)
        resp = self.client.get('/operations/unread/')
        self.assertEqual(resp.context['operations'][0].sin_leer, 2)

    def test_la_tabla_dice_que_esta_acotada(self):
        """
        Una tabla que de pronto muestra una fila de doscientas, sin decir por
        que, se lee como que se perdieron las demas.
        """
        self._escribir(self.usuario_a, self.op, 'Necesito la factura.')

        self.client.force_login(self.staff)
        resp = self.client.get('/operations/unread/')
        self.assertTrue(resp.context['solo_sin_leer'])
        self.assertContains(resp, 'Showing only operations with unread messages')

    def test_un_cliente_no_alcanza_los_hilos_de_otro(self):
        self._escribir(self.staff, self.op, 'Su carga llego completa.')
        self._escribir(self.staff, self.op_b, 'La suya tambien.')

        self.client.force_login(self.usuario_a)
        resp = self.client.get('/operations/unread/')
        traidas = [op.pk for op in resp.context['operations']]
        self.assertEqual(traidas, [self.op.pk])

    def test_quien_no_tiene_rol_no_alcanza_ninguna(self):
        self._escribir(self.staff, self.op, 'Su carga llego completa.')

        self.client.force_login(self.sin_rol)
        resp = self.client.get('/operations/unread/')
        self.assertEqual(list(resp.context['operations']), [])

    def test_cuando_no_queda_nada_lo_dice_asi(self):
        """
        El vacio de esta tabla no es «no hay operaciones» sino «no hay nada
        esperandote», que es una buena noticia y no un almacen sin registrar.
        """
        self._escribir(self.staff, self.op, 'Su carga llego completa.')

        self.client.force_login(self.staff)
        resp = self.client.get('/operations/unread/')
        self.assertEqual(list(resp.context['operations']), [])
        self.assertContains(resp, 'Nothing waiting for you')


class LaPantallaNoEsperaAlCorreoTests(HiloBase):
    """
    El aviso del chat sale fuera de la peticion.

    Se midio con el correo tal como esta hoy: escribir un mensaje costaba diez
    segundos justos -- el `EMAIL_TIMEOUT` -- porque el envio iba dentro de la
    peticion y el hilo no se repintaba hasta que el servidor de correo terminaba
    de no contestar.
    """

    def test_el_mensaje_se_pinta_sin_esperar_al_envio(self):
        """
        El envio se bloquea a proposito hasta que la prueba lo suelta. Si la
        vista lo esperara, este POST no volveria nunca y la prueba caeria por
        timeout en vez de por asercion -- que es exactamente lo que le pasa al
        operador delante de la pantalla.
        """
        arranco = threading.Event()
        suelta  = threading.Event()
        ESPERA  = 15   # lo que tardaria el envio si la vista lo esperara

        def envio_que_no_termina(*args, **kwargs):
            arranco.set()
            suelta.wait(ESPERA)
            return True, ''

        with override_settings(AVISOS_EN_HILO=True):
            with patch.object(notifications, 'avisar_mensaje_nuevo',
                              side_effect=envio_que_no_termina):
                try:
                    empezo = time.monotonic()
                    resp = self._escribir(self.staff, self.op, 'Su carga llego.')
                    tardo = time.monotonic() - empezo

                    self.assertEqual(resp.status_code, 200)
                    self.assertContains(resp, 'Su carga llego.')
                    # El mensaje ya esta escrito aunque el aviso siga colgado.
                    self.assertEqual(Message.objects.count(), 1)
                    # Lo que se comprueba es **que no espero**. Sin medir el
                    # tiempo, un envio en linea de quince segundos tambien
                    # devuelve 200 y la prueba pasaria igual -- que es justo lo
                    # que hacia la version anterior de esta prueba.
                    #
                    # El limite se mide contra `ESPERA` y no contra un numero
                    # suelto. Estaba en tres segundos, que valia cuando la base
                    # de datos era local; con la base lejos, los viajes de ida y
                    # vuelta de la propia peticion pasan de cuatro segundos y la
                    # prueba caia sin que la vista hubiera esperado a nada. La
                    # mitad del bloqueo sigue dejandola caer en cuanto la vista
                    # vuelva a esperar al correo, que es lo unico que vigila.
                    self.assertLess(tardo, ESPERA / 2,
                                    'la vista se quedo esperando al correo')
                    # Y el aviso se intenta de verdad, no se pierde.
                    self.assertTrue(arranco.wait(10), 'el aviso nunca arranco')
                finally:
                    suelta.set()

    def test_en_las_pruebas_el_aviso_va_en_linea(self):
        """
        Con el interruptor apagado -- que es como corre la suite -- el envio
        ocurre dentro de la peticion. Sin esto, cada prueba que mira
        `mail.outbox` justo despues de escribir estaria mirando algo que
        todavia no ha pasado.
        """
        mail.outbox = []
        with override_settings(AVISOS_EN_HILO=False):
            self._escribir(self.usuario_a, self.op, 'Necesito la factura.')
        self.assertEqual(len(mail.outbox), 1)


class FirmaDeLosMensajesTests(HiloBase):
    """
    Con que nombre sale firmado cada mensaje.

    Un mensaje firmado solo con el nombre de usuario -`custtes1`- no le dice
    nada a quien lo lee del otro lado: lo primero que necesita saber es si le
    escribe su almacen o su cliente.
    """

    def _firmas(self, quien_mira, op=None):
        self.client.force_login(quien_mira)
        resp = self.client.get('/operations/%s/chat/' % (op or self.op).pk)
        return [m.firma for m in resp.context['mensajes']]

    def test_la_empresa_firma_con_su_nombre_y_la_persona(self):
        self._escribir(self.staff, self.op, 'Su carga llego completa.')
        self.assertEqual(self._firmas(self.staff), [u'Almacen Uno · Ana Ruiz'])

    def test_el_cliente_firma_con_el_nombre_del_cliente(self):
        self._escribir(self.usuario_a, self.op, 'Necesito la factura.')
        self.assertEqual(self._firmas(self.staff), [u'Cliente A · cliente_a'])

    def test_los_dos_lados_ven_la_misma_firma(self):
        """
        La firma sale del lado del mensaje, no de quien mira: el cliente tiene
        que leer el nombre del almacen igual que el almacen lee el suyo.
        """
        self._escribir(self.staff, self.op, 'Su carga llego completa.')
        self._escribir(self.usuario_a, self.op, 'Gracias.')

        self.assertEqual(self._firmas(self.staff), self._firmas(self.usuario_a))

    def test_se_le_quita_la_forma_societaria_al_nombre(self):
        """
        «Customer Test, SA. de CV» no es una firma, es un dato del acta: ocupa
        la mitad del globo y no aporta nada frente a «Customer Test».
        """
        self.cliente_a.name = u'Customer Test, SA. de CV'
        self.cliente_a.save(update_fields=['name'])
        self._escribir(self.usuario_a, self.op, u'Necesito la factura.')

        self.assertEqual(self._firmas(self.staff), [u'Customer Test · cliente_a'])

    def test_la_firma_sobrevive_a_la_baja_de_quien_escribio(self):
        """
        El nombre de la persona esta congelado en el mensaje; el de la empresa
        se vuelve a calcular. Ninguno de los dos depende de que la cuenta siga
        existiendo.
        """
        self._escribir(self.staff, self.op, 'Su carga llego completa.')
        self.staff.delete()

        self.assertEqual(self._firmas(self.admin), [u'Almacen Uno · Ana Ruiz'])

    def test_si_la_empresa_se_renombra_los_mensajes_viejos_la_siguen(self):
        """
        Es la misma empresa: un mensaje de hace un ano no puede seguir firmado
        con un nombre que ya no existe.
        """
        self._escribir(self.staff, self.op, 'Su carga llego completa.')
        self.tenant.name = 'Almacenes del Norte'
        self.tenant.save(update_fields=['name'])

        self.assertEqual(self._firmas(self.staff),
                         [u'Almacenes del Norte · Ana Ruiz'])

    def test_el_correo_firma_con_el_nombre_corto(self):
        """
        En la firma y en el «fulano escribio» va el nombre corto, que es donde
        estorba el del acta. En la ficha de datos de la operacion, en cambio, el
        renglon «Cliente» conserva el nombre completo a proposito: ahi no es una
        firma, es el dato del expediente.
        """
        self.cliente_a.name = u'Customer Test, SA. de CV'
        self.cliente_a.save(update_fields=['name'])
        mail.outbox = []
        self._escribir(self.usuario_a, self.op, u'Necesito la factura.')

        cuerpo = mail.outbox[0].body
        self.assertIn('<b>Customer Test</b> wrote in the file', cuerpo)
        self.assertIn(u'Customer Test · cliente_a', cuerpo)
        self.assertNotIn(u'Customer Test, SA. de CV ·', cuerpo)
        # Y el dato de la operacion sigue completo.
        self.assertIn(u'<b>Customer Test, SA. de CV</b>', cuerpo)


class NombreCortoTests(TestCase):
    """
    El nombre de una empresa, recortado para caber en una firma. No pretende
    ser exacto -- una empresa puede llamarse «Ltd» de verdad -- sino legible.
    """

    def test_quita_la_forma_societaria(self):
        from .utils import nombre_corto
        casos = {
            u'Customer Test, SA. de CV':  u'Customer Test',
            # Este es el que obliga a cortar por la coma: lo que va detras no
            # es una forma societaria, asi que quitar sufijos no lo alcanza.
            u'Transportes del Valle, Division Norte': u'Transportes del Valle',
            u'DYSER GROUP SA DE CV':      u'DYSER GROUP',
            u'RDL Systems LLC':           u'RDL Systems',
            u'Nadie Entra SA':            u'Nadie Entra',
            u'Zapatos Zeta':              u'Zapatos Zeta',
        }
        for entra, sale in casos.items():
            self.assertEqual(nombre_corto(entra), sale, entra)

    def test_nunca_devuelve_una_firma_vacia(self):
        """
        Si al recortar no quedara nada, es preferible una firma larga a una
        firma en blanco.
        """
        from .utils import nombre_corto
        self.assertEqual(nombre_corto(u'Ltd'), u'Ltd')
        self.assertEqual(nombre_corto(u'  '), u'')
        self.assertEqual(nombre_corto(None), u'')

    def test_un_nombre_larguisimo_se_recorta(self):
        """
        El globo del mensaje no crece: sin esto la firma partiria el renglon y
        el mensaje empezaria mas abajo en unas filas que en otras.
        """
        from .utils import nombre_corto
        largo = nombre_corto(u'Comercializadora Internacional del Noroeste, S.A. de C.V.')
        self.assertLessEqual(len(largo), 22)
        self.assertTrue(largo.endswith(u'…'))


@STORAGE_LOCAL
class AdjuntosEnElHiloTests(HiloBase):
    """
    Lo que se adjunta en el hilo.

    La regla que gobierna todo lo demas: **un adjunto del chat es un documento
    del expediente**, no una coleccion aparte. Lo que se manda por aqui es lo
    mismo que el ZIP y los correos van a buscar despues, y un archivo que
    viviera solo dentro de una conversacion seria una segunda verdad -- justo
    lo que el hilo vino a evitar.
    """

    def _mandar(self, user, op, texto='', archivos=()):
        self.client.force_login(user)
        datos = {'body': texto}
        if archivos:
            datos['adjuntos'] = list(archivos)
        return self.client.post('/operations/%s/chat/send/' % op.pk, datos)

    def _foto(self, nombre='etiqueta.jpg', contenido=b'una foto'):
        return SimpleUploadedFile(nombre, contenido, content_type='image/jpeg')

    def test_lo_adjuntado_entra_al_expediente(self):
        self._mandar(self.staff, self.op, 'Ahi va la etiqueta.', [self._foto()])

        doc = OperationDocument.objects.get(operation=self.op)
        self.assertEqual(doc.original_name, 'etiqueta.jpg')
        self.assertEqual(doc.file_type, 'PHOTO')
        self.assertEqual(doc.tenant, self.tenant)
        self.assertEqual(doc.uploaded_by, self.staff)
        # Y lleva nombre digital, como cualquier documento del expediente.
        self.assertTrue(doc.digital_name)

    def test_el_documento_sabe_de_que_mensaje_vino(self):
        self._mandar(self.staff, self.op, 'Ahi va.', [self._foto()])

        mensaje = Message.objects.get()
        doc = OperationDocument.objects.get(operation=self.op)
        self.assertEqual(doc.mensaje, mensaje)
        self.assertEqual(list(mensaje.adjuntos.all()), [doc])

    def test_un_mensaje_puede_llevar_varios_archivos(self):
        """
        Quien manda tres fotos de la misma tarima esta diciendo una sola cosa;
        tres mensajes seguidos con una foto cada uno la convierten en tres.
        """
        self._mandar(self.staff, self.op, 'La tarima completa.',
                     [self._foto('a.jpg'), self._foto('b.jpg'), self._foto('c.jpg')])

        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(Message.objects.get().adjuntos.count(), 3)

    def test_un_archivo_solo_es_un_mensaje_completo(self):
        """Mandar la foto de la etiqueta sin escribir nada es una respuesta."""
        resp = self._mandar(self.staff, self.op, '', [self._foto()])

        self.assertEqual(resp.status_code, 200)
        mensaje = Message.objects.get()
        self.assertEqual(mensaje.body, '')
        self.assertEqual(mensaje.adjuntos.count(), 1)

    def test_sin_texto_y_sin_archivo_no_hay_mensaje(self):
        resp = self._mandar(self.staff, self.op, '   ')

        self.assertEqual(Message.objects.count(), 0)
        self.assertContains(resp, 'Write a message or attach a file.')

    def test_el_cliente_tambien_puede_adjuntar(self):
        """
        Es la mitad del valor: «mandenos la foto del pedimento» se contesta con
        la foto, no con una explicacion de donde subirla.
        """
        self._mandar(self.usuario_a, self.op, 'Aqui esta.', [self._foto('ped.jpg')])

        doc = OperationDocument.objects.get(operation=self.op)
        self.assertEqual(doc.uploaded_by, self.usuario_a)
        self.assertEqual(Message.objects.get().side, LADO_CLIENTE)

    def test_los_adjuntos_van_al_final_del_expediente(self):
        """Lo que se sube despues va despues: el orden del expediente es dato."""
        ya_estaba = OperationDocument.objects.create(
            tenant=self.tenant, operation=self.op, file_type='PHOTO',
            original_name='vieja.jpg', file=SimpleUploadedFile('vieja.jpg', b'x'),
            orden=1)

        self._mandar(self.staff, self.op, 'Y esta.', [self._foto('nueva.jpg')])

        nueva = OperationDocument.objects.get(original_name='nueva.jpg')
        self.assertGreater(nueva.orden, ya_estaba.orden)

    def test_no_se_aceptan_mas_de_cinco_archivos(self):
        """
        El tope no es tecnico: es para que el hilo siga siendo una
        conversacion. Quien sube veinte fotos esta armando un expediente, y
        para eso esta el panel Digital, que ademas deja ordenarlas.
        """
        resp = self._mandar(self.staff, self.op, 'Todas.',
                            [self._foto('%s.jpg' % i) for i in range(6)])

        self.assertEqual(Message.objects.count(), 0)
        self.assertEqual(OperationDocument.objects.count(), 0)
        self.assertContains(resp, 'No more than 5 files per message.')

    def test_no_se_acepta_un_archivo_enorme(self):
        from .views import ADJUNTO_MAX_MB

        gordo = SimpleUploadedFile(
            'video.mp4', b'x' * (ADJUNTO_MAX_MB * 1024 * 1024 + 1),
            content_type='video/mp4')
        resp = self._mandar(self.staff, self.op, 'Mira esto.', [gordo])

        self.assertEqual(Message.objects.count(), 0)
        self.assertEqual(OperationDocument.objects.count(), 0)
        self.assertContains(resp, 'is over %s MB' % ADJUNTO_MAX_MB)

    def test_lo_rechazado_no_deja_el_mensaje_escrito(self):
        """
        La comprobacion va antes de crear nada: un mensaje que quedara escrito
        sin su archivo diria «ahi va la etiqueta» sin etiqueta ninguna.
        """
        gordo = SimpleUploadedFile('video.mp4', b'x' * (26 * 1024 * 1024))
        self._mandar(self.staff, self.op, 'Ahi va la etiqueta.', [gordo])

        self.assertFalse(Message.objects.exists())
        self.assertFalse(Conversation.objects.filter(operation=self.op).exists())

    def test_el_hilo_ensena_lo_adjuntado(self):
        self._mandar(self.staff, self.op, 'Ahi va.', [self._foto()])

        self.client.force_login(self.usuario_a)
        resp = self.client.get('/operations/%s/chat/' % self.op.pk)
        doc = OperationDocument.objects.get()
        self.assertContains(resp, '/documents/%s/file/' % doc.pk)

    def test_un_archivo_retirado_del_expediente_desaparece_del_hilo(self):
        """
        El hilo no puede seguir ofreciendo un archivo que ya se mando a la
        papelera. Lo que si se queda es lo que se dijo: los mensajes no se
        borran.
        """
        self._mandar(self.staff, self.op, 'Ahi va.', [self._foto()])
        doc = OperationDocument.objects.get()
        doc.deleted_at = timezone.now()
        doc.save(update_fields=['deleted_at'])

        self.client.force_login(self.staff)
        resp = self.client.get('/operations/%s/chat/' % self.op.pk)
        self.assertNotContains(resp, '/documents/%s/file/' % doc.pk)
        self.assertContains(resp, 'Ahi va.')

    def test_el_aviso_por_correo_dice_que_hay_archivo(self):
        """
        Un mensaje que es solo un archivo dejaba el aviso sin nada que ensenar:
        decia que alguien escribio y no mostraba ni una linea.
        """
        mail.outbox = []
        self._mandar(self.usuario_a, self.op, '', [self._foto('pedimento.jpg')])

        cuerpo = mail.outbox[0].body
        self.assertIn('1 archivo en el expediente', cuerpo)
        doc = OperationDocument.objects.get()
        self.assertIn(doc.digital_name, cuerpo)

    def test_una_subida_que_falla_no_tumba_la_pantalla(self):
        """
        El archivo viaja al bucket, que esta al otro lado de la red: un corte o
        un rechazo del almacenamiento son cosas que pasan. Se descubrio en un
        navegador -- un POST devolvio 500 y el operador perdio lo que acababa de
        escribir sin saber por que.

        Lo que se dijo se queda escrito; lo que falta es el archivo, y eso el
        aviso lo dice.
        """
        with patch('warehouse.views.guardar_en_expediente',
                   side_effect=OSError('el bucket no responde')):
            resp = self._mandar(self.staff, self.op, 'Ahi va la etiqueta.',
                                [self._foto()])

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Message.objects.get().body, 'Ahi va la etiqueta.')
        self.assertContains(resp, 'the file could not be uploaded')
        self.assertEqual(OperationDocument.objects.count(), 0)

    def test_si_falla_la_subida_el_aviso_sale_igual(self):
        """
        El del otro lado tiene que enterarse de que le escribieron aunque el
        archivo se haya quedado en el camino: el texto ya dice algo.
        """
        mail.outbox = []
        with patch('warehouse.views.guardar_en_expediente',
                   side_effect=OSError('el bucket no responde')):
            self._mandar(self.usuario_a, self.op, 'Aqui esta el pedimento.',
                         [self._foto()])

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Aqui esta el pedimento.', mail.outbox[0].body)
