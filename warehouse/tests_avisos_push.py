"""
Avisos push a los operadores: a quien le suena, cuando, y lo que queda en la
bitacora. El servicio de push del fabricante se sustituye por un doble: lo que
se prueba es lo que decide el WMS, no lo que hace Google.
"""
import json
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from . import avisos_push, notifications
from .models import (Catalog, Conversation, LADO_CLIENTE, LADO_TENANT, Message,
                     NotificationLog, PushSubscription, Tenant, UserProfile,
                     WarehouseOperation)

# Un par valido generado solo para las pruebas; no es el de ningun entorno real.
PUBLICA = 'BACv9VfzJMPFJ8onwnVsauH0HZrbSTyEqrwpY_N0F6CY0HmQTzgnNjkrPR9VNGVg1qj5wp4P2rC0vLo8GIZ5n7s'
PRIVADA = 'iqn3ErJ-GBqrX8mgrp8kdIO6vJSumznqktyxOSpfS_w'
CON_LLAVES = override_settings(
    VAPID_PUBLIC_KEY=PUBLICA, VAPID_PRIVATE_KEY=PRIVADA,
    VAPID_SUBJECT='mailto:pruebas@example.com',
)
SIN_LLAVES = override_settings(VAPID_PUBLIC_KEY='', VAPID_PRIVATE_KEY='')


def _respuesta(codigo):
    r = MagicMock()
    r.status_code = codigo
    return r


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.otro_tenant = Tenant.objects.create(
            name='Bodegas del Sur', type='organization', subdomain='sur')
        cls.cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Cliente Uno, SA de CV',
            contact_email='compras@uno.com',
            push_on_message=True, push_on_create=True)

        cls.ana = User.objects.create_user('ana', password='x')
        UserProfile.objects.create(user=cls.ana, tenant=cls.tenant, role='staff')
        cls.beto = User.objects.create_user('beto', password='x')
        UserProfile.objects.create(user=cls.beto, tenant=cls.tenant, role='admin',
                                   language='en')
        cls.del_cliente = User.objects.create_user('cli', password='x')
        UserProfile.objects.create(user=cls.del_cliente, tenant=cls.tenant,
                                   role='customer', customer=cls.cliente)
        cls.ajeno = User.objects.create_user('ajeno', password='x')
        UserProfile.objects.create(user=cls.ajeno, tenant=cls.otro_tenant, role='staff')

    def _operacion(self, cliente=None):
        return WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id='ED261125-0001',
            customer=cliente or self.cliente, description='Prueba')

    def _dispositivo(self, usuario, n=1, movil=True):
        return PushSubscription.objects.create(
            user=usuario, tenant=self.tenant,
            endpoint=f'https://fcm.googleapis.com/fcm/send/{usuario.username}-{n}',
            p256dh='BPk', auth='aut', es_movil=movil)

    def _logs(self):
        return NotificationLog.objects.filter(channel='PUSH')


@CON_LLAVES
class OperacionNuevaTests(Base):

    def test_suena_a_los_operadores_menos_a_quien_la_registro(self):
        self._dispositivo(self.ana)
        self._dispositivo(self.beto)
        op = self._operacion()
        with patch('pywebpush.webpush') as webpush:
            self.assertTrue(avisos_push.avisar_operacion_nueva(op, triggered_by=self.ana))

        self.assertEqual(webpush.call_count, 1)
        enviado = webpush.call_args.kwargs
        self.assertIn('beto-1', enviado['subscription_info']['endpoint'])
        datos = json.loads(enviado['data'])
        self.assertIn('Cliente Uno', datos['title'])
        self.assertEqual(datos['url'], '/mobile/?tab=digital&q=ED261125-0001')
        self.assertEqual(datos['tag'], f'op-{op.pk}')

        log = self._logs().get()
        self.assertEqual((log.status, log.event, log.recipient),
                         ('SENT', 'OPERATION_CREATED', 'beto'))

    def test_el_aviso_va_en_el_idioma_de_cada_operador(self):
        self._dispositivo(self.beto)
        with patch('pywebpush.webpush') as webpush:
            avisos_push.avisar_operacion_nueva(self._operacion())
        self.assertTrue(json.loads(webpush.call_args.kwargs['data'])['title']
                        .startswith('New operation'))

    def test_en_la_computadora_abre_el_tablero(self):
        self._dispositivo(self.beto, movil=False)
        with patch('pywebpush.webpush') as webpush:
            avisos_push.avisar_operacion_nueva(self._operacion())
        self.assertTrue(json.loads(webpush.call_args.kwargs['data'])['url']
                        .startswith('/dashboard/'))

    def test_no_suena_si_el_cliente_lo_tiene_apagado(self):
        apagado = Catalog.objects.create(
            tenant=self.tenant, category='CUSTOMER', name='Cliente Dos')
        self._dispositivo(self.beto)
        with patch('pywebpush.webpush') as webpush:
            self.assertFalse(avisos_push.avisar_operacion_nueva(self._operacion(apagado)))
        webpush.assert_not_called()
        self.assertFalse(self._logs().exists())

    def test_no_le_suena_al_cliente_ni_a_otra_empresa(self):
        self._dispositivo(self.del_cliente)
        self._dispositivo(self.ajeno)
        with patch('pywebpush.webpush') as webpush:
            avisos_push.avisar_operacion_nueva(self._operacion())
        webpush.assert_not_called()
        self.assertEqual(self._logs().get().detail, 'no_devices')

    def test_la_suscripcion_que_ya_no_existe_se_borra(self):
        from pywebpush import WebPushException
        self._dispositivo(self.beto)
        error = WebPushException('gone', response=_respuesta(410))
        with patch('pywebpush.webpush', side_effect=error):
            self.assertFalse(avisos_push.avisar_operacion_nueva(self._operacion()))
        self.assertFalse(PushSubscription.objects.exists())

    def test_un_fallo_queda_anotado_y_no_borra_el_dispositivo(self):
        from pywebpush import WebPushException
        self._dispositivo(self.beto)
        error = WebPushException('boom', response=_respuesta(500))
        with patch('pywebpush.webpush', side_effect=error):
            avisos_push.avisar_operacion_nueva(self._operacion())
        self.assertEqual(self._logs().get().status, 'FAILED')
        self.assertTrue(PushSubscription.objects.exists())

    def test_el_alta_lo_dispara_y_el_correo_sigue_igual(self):
        self._dispositivo(self.beto)
        with patch('pywebpush.webpush') as webpush:
            enviado, _error = notifications.notify_operation_created(
                self._operacion(), triggered_by=self.ana)
        self.assertTrue(enviado)
        self.assertEqual(webpush.call_count, 1)


@CON_LLAVES
class AlClienteTests(Base):
    """El push que si le llega al cliente: su operacion quedo registrada."""

    def setUp(self):
        Catalog.objects.filter(pk=self.cliente.pk).update(push_cliente_on_create=True)
        self.cliente.refresh_from_db()

    def test_le_suena_a_los_usuarios_del_cliente(self):
        self._dispositivo(self.del_cliente)
        op = self._operacion()
        with patch('pywebpush.webpush') as webpush:
            self.assertTrue(avisos_push.avisar_al_cliente_operacion(op, triggered_by=self.ana))
        self.assertEqual(webpush.call_count, 1)
        enviado = webpush.call_args.kwargs
        self.assertIn('cli-1', enviado['subscription_info']['endpoint'])
        datos = json.loads(enviado['data'])
        self.assertIn('Almacenes del Norte', datos['title'])
        self.assertIn('ED261125-0001', datos['body'])
        self.assertEqual(self._logs().get().recipient, 'cli')

    def test_no_le_llega_a_otro_cliente(self):
        otro = Catalog.objects.create(tenant=self.tenant, category='CUSTOMER',
                                      name='Otro', push_cliente_on_create=True)
        de_otro = User.objects.create_user('otro_cli', password='x')
        UserProfile.objects.create(user=de_otro, tenant=self.tenant,
                                   role='customer', customer=otro)
        self._dispositivo(de_otro)
        with patch('pywebpush.webpush') as webpush:
            avisos_push.avisar_al_cliente_operacion(self._operacion())
        webpush.assert_not_called()

    def test_apagado_en_la_ficha_no_suena(self):
        Catalog.objects.filter(pk=self.cliente.pk).update(push_cliente_on_create=False)
        self.cliente.refresh_from_db()
        self._dispositivo(self.del_cliente)
        with patch('pywebpush.webpush') as webpush:
            self.assertFalse(avisos_push.avisar_al_cliente_operacion(self._operacion()))
        webpush.assert_not_called()

    def test_el_alta_le_avisa_al_cliente_y_a_los_operadores(self):
        self._dispositivo(self.del_cliente)
        self._dispositivo(self.beto)
        with patch('pywebpush.webpush') as webpush:
            notifications.notify_operation_created(self._operacion(), triggered_by=self.ana)
        destinos = sorted(c.kwargs['subscription_info']['endpoint'].rsplit('/', 1)[1]
                          for c in webpush.call_args_list)
        self.assertEqual(destinos, ['beto-1', 'cli-1'])

    def test_sin_usuarios_queda_anotado(self):
        sin_usuarios = Catalog.objects.create(tenant=self.tenant, category='CUSTOMER',
                                              name='Sin usuarios',
                                              push_cliente_on_create=True)
        avisos_push.avisar_al_cliente_operacion(self._operacion(sin_usuarios))
        self.assertEqual(self._logs().get().detail, 'no_customer_users')


class LlavesMalPegadasTests(Base):
    """
    Lo que paso en produccion el 26 nov: la privada mal pegada hacia que la
    biblioteca contestara con un error de ASN.1 que no decia que hacer.
    """

    def _problema(self, **llaves):
        with override_settings(**llaves):
            return avisos_push.problema_con_las_llaves()

    def test_el_par_bueno_no_tiene_problema(self):
        self.assertIsNone(self._problema(VAPID_PUBLIC_KEY=PUBLICA, VAPID_PRIVATE_KEY=PRIVADA))

    def test_se_limpia_el_prefijo_las_comillas_y_los_espacios(self):
        self.assertIsNone(self._problema(
            VAPID_PUBLIC_KEY=f' VAPID_PUBLIC_KEY={PUBLICA} ',
            VAPID_PRIVATE_KEY=f'"VAPID_PRIVATE_KEY={PRIVADA}"'))

    def test_la_publica_pegada_como_privada(self):
        problema = self._problema(VAPID_PUBLIC_KEY=PUBLICA, VAPID_PRIVATE_KEY=PUBLICA)
        self.assertIn('public key', problema)

    def test_la_privada_cortada(self):
        problema = self._problema(VAPID_PUBLIC_KEY=PUBLICA, VAPID_PRIVATE_KEY=PRIVADA[:30])
        self.assertIn('43', problema)
        self.assertIn('30', problema)

    def test_llaves_que_no_son_pareja(self):
        # 32 bytes de 0x01: una privada valida inventada, que no es la de PUBLICA.
        otra = 'AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE'
        problema = self._problema(VAPID_PUBLIC_KEY=PUBLICA, VAPID_PRIVATE_KEY=otra)
        self.assertIn('not a pair', problema)

    def test_con_llaves_malas_no_se_intenta_y_queda_el_motivo(self):
        self._dispositivo(self.beto)
        with override_settings(VAPID_PUBLIC_KEY=PUBLICA, VAPID_PRIVATE_KEY=PUBLICA), \
                patch('pywebpush.webpush') as webpush:
            avisos_push.avisar_operacion_nueva(self._operacion())
        webpush.assert_not_called()
        log = self._logs().get()
        self.assertEqual(log.status, 'FAILED')
        self.assertIn('public key', log.detail)

    def test_mi_cuenta_se_lo_dice_al_operador(self):
        self.client.force_login(self.ana)
        with override_settings(VAPID_PUBLIC_KEY=PUBLICA, VAPID_PRIVATE_KEY=PRIVADA[:30]):
            r = self.client.get('/cuenta/')
        self.assertContains(r, 'keys on the server are wrong')

    def test_firma_de_verdad_con_la_llave_limpia(self):
        """Sin doble: la firma real sale con la privada que traia el prefijo."""
        with override_settings(VAPID_PUBLIC_KEY=PUBLICA,
                               VAPID_PRIVATE_KEY='VAPID_PRIVATE_KEY=' + PRIVADA):
            firma = avisos_push._firma()
            encabezados = firma.sign({'sub': 'mailto:pruebas@example.com',
                                      'aud': 'https://fcm.googleapis.com'})
        self.assertIn(PUBLICA, encabezados['Authorization'])


@SIN_LLAVES
class SinLlavesTests(Base):

    def test_sin_llaves_no_se_manda_ni_se_anota_nada(self):
        self._dispositivo(self.beto)
        with patch('pywebpush.webpush') as webpush:
            enviado, _error = notifications.notify_operation_created(self._operacion())
        self.assertTrue(enviado)
        webpush.assert_not_called()
        self.assertFalse(self._logs().exists())


@CON_LLAVES
class MensajeNuevoTests(Base):

    def _mensaje(self, lado, autor, cuerpo='¿Ya llegó mi carga?'):
        op = self._operacion()
        hilo = Conversation.objects.create(operation=op, tenant=self.tenant)
        mensaje = Message.objects.create(conversation=hilo, author=autor,
                                         author_name=autor.username, side=lado,
                                         body=cuerpo)
        return hilo, mensaje

    def test_lo_que_escribe_el_cliente_suena_a_los_operadores(self):
        self._dispositivo(self.ana)
        hilo, mensaje = self._mensaje(LADO_CLIENTE, self.del_cliente)
        with patch('pywebpush.webpush') as webpush:
            notifications.avisar_mensaje_nuevo(hilo, LADO_CLIENTE, mensaje=mensaje,
                                               triggered_by=self.del_cliente)
        datos = json.loads(webpush.call_args.kwargs['data'])
        self.assertIn('¿Ya llegó mi carga?', datos['body'])
        self.assertEqual(datos['tag'], f'chat-{hilo.operation.pk}')
        self.assertEqual(self._logs().get().event, 'CHAT_MESSAGE')

    def test_no_espera_los_15_minutos_del_correo(self):
        """Cada mensaje suena; el telefono reemplaza el aviso del mismo hilo."""
        self._dispositivo(self.ana)
        hilo, mensaje = self._mensaje(LADO_CLIENTE, self.del_cliente)
        with patch('pywebpush.webpush') as webpush:
            notifications.avisar_mensaje_nuevo(hilo, LADO_CLIENTE, mensaje=mensaje)
            notifications.avisar_mensaje_nuevo(hilo, LADO_CLIENTE, mensaje=mensaje)
        self.assertEqual(webpush.call_count, 2)

    def test_lo_que_escribe_la_empresa_no_suena(self):
        self._dispositivo(self.ana)
        hilo, mensaje = self._mensaje(LADO_TENANT, self.beto)
        with patch('pywebpush.webpush') as webpush:
            notifications.avisar_mensaje_nuevo(hilo, LADO_TENANT, mensaje=mensaje,
                                               triggered_by=self.beto)
        webpush.assert_not_called()

    def test_el_mensaje_largo_se_recorta(self):
        self._dispositivo(self.ana)
        hilo, mensaje = self._mensaje(LADO_CLIENTE, self.del_cliente, 'a' * 500)
        with patch('pywebpush.webpush') as webpush:
            avisos_push.avisar_mensaje_del_cliente(hilo, mensaje)
        cuerpo = json.loads(webpush.call_args.kwargs['data'])['body']
        self.assertLess(len(cuerpo), 200)
        self.assertTrue(cuerpo.endswith('…'))


@CON_LLAVES
class DispositivosTests(Base):
    SUSCRIPCION = {'endpoint': 'https://fcm.googleapis.com/fcm/send/abc',
                   'keys': {'p256dh': 'BPk', 'auth': 'aut'}, 'es_movil': True}

    def _post(self, url, datos):
        return self.client.post(url, json.dumps(datos), content_type='application/json')

    def test_el_operador_activa_su_dispositivo(self):
        self.client.force_login(self.ana)
        r = self._post('/push/suscribir/', self.SUSCRIPCION)
        self.assertEqual(r.status_code, 200)
        s = PushSubscription.objects.get()
        self.assertEqual((s.user, s.tenant, s.es_movil), (self.ana, self.tenant, True))

    def test_el_mismo_navegador_pasa_a_quien_entra_ahora(self):
        self.client.force_login(self.ana)
        self._post('/push/suscribir/', self.SUSCRIPCION)
        self.client.force_login(self.beto)
        self._post('/push/suscribir/', self.SUSCRIPCION)
        self.assertEqual(PushSubscription.objects.get().user, self.beto)

    def test_el_usuario_del_cliente_tambien_puede_activarlos(self):
        self.client.force_login(self.del_cliente)
        r = self._post('/push/suscribir/', self.SUSCRIPCION)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(PushSubscription.objects.get().user, self.del_cliente)

    def test_quien_no_tiene_empresa_no_puede_activarlos(self):
        sin_empresa = User.objects.create_user('suelto', password='x')
        self.client.force_login(sin_empresa)
        r = self._post('/push/suscribir/', self.SUSCRIPCION)
        self.assertEqual(r.status_code, 403)
        self.assertFalse(PushSubscription.objects.exists())

    def test_una_suscripcion_sin_https_no_se_guarda(self):
        self.client.force_login(self.ana)
        r = self._post('/push/suscribir/', dict(self.SUSCRIPCION, endpoint='http://x'))
        self.assertEqual(r.status_code, 400)

    def test_solo_se_quita_el_propio(self):
        ajeno = self._dispositivo(self.beto)
        self.client.force_login(self.ana)
        self._post('/push/quitar/', {'endpoint': ajeno.endpoint})
        self.assertTrue(PushSubscription.objects.filter(pk=ajeno.pk).exists())
        self.client.force_login(self.beto)
        self._post('/push/quitar/', {'endpoint': ajeno.endpoint})
        self.assertFalse(PushSubscription.objects.exists())

    def test_aviso_de_prueba(self):
        self._dispositivo(self.ana)
        self.client.force_login(self.ana)
        with patch('pywebpush.webpush') as webpush:
            r = self._post('/push/probar/', {})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(webpush.call_count, 1)

    def test_el_service_worker_se_sirve_desde_la_raiz(self):
        r = self.client.get('/sw.js')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/javascript')
        self.assertEqual(r['Service-Worker-Allowed'], '/')
        self.assertIn(b'showNotification', r.content)

    def test_el_manifest(self):
        r = self.client.get('/manifest.webmanifest')
        self.assertEqual(json.loads(r.content)['display'], 'standalone')

    def test_mi_cuenta_ofrece_activarlos_al_operador(self):
        self.client.force_login(self.ana)
        r = self.client.get('/cuenta/')
        self.assertContains(r, 'push-activar')

    def test_mi_cuenta_los_ofrece_al_cliente_con_su_texto(self):
        self.client.force_login(self.del_cliente)
        r = self.client.get('/cuenta/')
        self.assertContains(r, 'push-activar')
        self.assertContains(r, 'registers one of your operations')

    @SIN_LLAVES
    def test_sin_llaves_mi_cuenta_dice_que_falta_configurarlo(self):
        self.client.force_login(self.ana)
        r = self.client.get('/cuenta/')
        self.assertNotContains(r, 'push-activar')
        self.assertContains(r, 'VAPID')


class FichaDelClienteTests(Base):

    def test_la_ficha_guarda_las_dos_casillas(self):
        self.client.force_login(self.beto)
        self.client.post(f'/catalog/{self.cliente.pk}/edit/', {
            'name': self.cliente.name, 'notify_email': 'on',
            'push_on_create': 'on', 'push_cliente_on_create': 'on'})
        self.cliente.refresh_from_db()
        self.assertTrue(self.cliente.push_on_create)
        self.assertTrue(self.cliente.push_cliente_on_create)
        self.assertFalse(self.cliente.push_on_message)

    def test_nacen_apagadas(self):
        nuevo = Catalog.objects.create(tenant=self.tenant, category='CUSTOMER',
                                       name='Nuevo')
        self.assertFalse(nuevo.push_on_create or nuevo.push_on_message)
