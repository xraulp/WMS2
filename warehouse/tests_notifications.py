"""
Avisos al cliente (Tenant nivel 2) y su bitacora.

Lo que se cubre: a quien se le escribe, cuando se le escribe, y que de cada
envio quede constancia — incluidos los que fallan y los que se omiten, que era
justo lo que antes no dejaba rastro.
"""
import tempfile
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from . import notifications
from .models import (Catalog, NotificationLog, OperationDocument, Tenant,
                     UserProfile, WarehouseOperation)


class NotificationTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Warehouse Uno', type='organization', subdomain='uno')

        cls.cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Cliente A',
            contact_email='compras@cliente-a.com', whatsapp='+521999')

        cls.staff = User.objects.create_user('operador', password='x')
        # 'admin' y no 'manager': mantener las preferencias de aviso de un cliente
        # es editar el catalogo de clientes, y eso quedo reservado al
        # administrador de la empresa.
        UserProfile.objects.create(user=cls.staff, tenant=cls.tenant, role='admin')

    def _operacion(self, customer=None, custom_id='ED260816-0001',
                   op_type='ENTRY', manual=''):
        return WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type=op_type, custom_id=custom_id,
            customer=customer, customer_name_manual=manual,
            description='Mercancia de prueba')

    def _logs(self, **filtros):
        return NotificationLog.objects.filter(**filtros)


class DestinatariosTests(NotificationTestBase):
    """A quien se le escribe."""

    def test_incluye_contact_email_y_los_logins_del_cliente(self):
        """
        El alta de cliente nivel 2 crea un User con su propio correo. Antes el
        envio miraba solo contact_email, asi que ese usuario nunca recibia nada.
        """
        user = User.objects.create_user(
            'cliente_a', password='x', email='juan@cliente-a.com')
        UserProfile.objects.create(
            user=user, tenant=self.tenant, role='customer', customer=self.cliente)

        self.assertEqual(
            notifications.email_recipients(self.cliente),
            ['compras@cliente-a.com', 'juan@cliente-a.com'])

    def test_no_repite_una_direccion_que_aparece_en_los_dos_lados(self):
        user = User.objects.create_user(
            'cliente_a', password='x', email='COMPRAS@cliente-a.com')
        UserProfile.objects.create(
            user=user, tenant=self.tenant, role='customer', customer=self.cliente)

        self.assertEqual(
            notifications.email_recipients(self.cliente), ['compras@cliente-a.com'])

    def test_ignora_los_logins_desactivados(self):
        user = User.objects.create_user(
            'ex_empleado', password='x', email='ex@cliente-a.com', is_active=False)
        UserProfile.objects.create(
            user=user, tenant=self.tenant, role='customer', customer=self.cliente)

        self.assertEqual(
            notifications.email_recipients(self.cliente), ['compras@cliente-a.com'])

    def test_el_nombre_capturado_a_mano_se_resuelve_dentro_del_tenant(self):
        """
        get_customer_email_raw() del modelo busca el nombre sin filtrar por
        tenant, asi que un homonimo de otra empresa podia recibir el correo.

        Se comprueba desde el tenant de abajo a proposito: su homonimo se creo
        despues, asi que un .first() sin filtrar por tenant devolveria el de
        arriba y el correo del cliente se le iria a otra empresa.
        """
        otro_tenant = Tenant.objects.create(
            name='Warehouse Dos', type='organization', subdomain='dos')
        homonimo = Catalog.objects.create(
            tenant=otro_tenant, category='CUSTOMER', name='Cliente A',
            contact_email='suyo@otro-tenant.com')

        op_de_alla = WarehouseOperation.objects.create(
            tenant=otro_tenant, operation_type='ENTRY', custom_id='ED-DOS-9',
            customer_name_manual='cliente a')
        resuelto = notifications.resolve_customer(op_de_alla)

        self.assertEqual(resuelto, homonimo)
        self.assertEqual(
            notifications.email_recipients(resuelto), ['suyo@otro-tenant.com'])

        # Y en la direccion contraria: el de aca sigue resolviendo al suyo.
        self.assertEqual(
            notifications.resolve_customer(self._operacion(manual='cliente a')),
            self.cliente)


class AltaDeOperacionTests(NotificationTestBase):
    """El aviso de que la operacion quedo registrada."""

    def test_envia_el_correo_y_lo_registra(self):
        op = self._operacion(customer=self.cliente)

        enviado, error = notifications.notify_operation_created(
            op, triggered_by=self.staff)

        self.assertTrue(enviado)
        self.assertIsNone(error)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('compras@cliente-a.com', mail.outbox[0].to)

        log = self._logs(channel='EMAIL', event='OPERATION_CREATED').get()
        self.assertEqual(log.status, 'SENT')
        self.assertEqual(log.tenant, self.tenant)
        self.assertEqual(log.operation_custom_id, op.custom_id)
        self.assertEqual(log.triggered_by, self.staff)

    def test_marca_la_operacion_como_notificada(self):
        op = self._operacion(customer=self.cliente)
        notifications.notify_operation_created(op, triggered_by=self.staff)

        op.refresh_from_db()
        self.assertTrue(op.email_sent)
        self.assertIsNotNone(op.email_sent_at)

    def test_respeta_al_cliente_que_no_quiere_correo(self):
        self.cliente.notify_email = False
        self.cliente.save()
        op = self._operacion(customer=self.cliente)

        enviado, error = notifications.notify_operation_created(
            op, triggered_by=self.staff)

        self.assertFalse(enviado)
        self.assertEqual(error, 'preference_off')
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(
            self._logs(channel='EMAIL').get().status, 'SKIPPED')

    def test_un_fallo_de_envio_queda_registrado(self):
        """Antes el error se devolvia a la vista pero no quedaba en ningun lado."""
        op = self._operacion(customer=self.cliente)

        with patch('warehouse.notifications.EmailMessage.send',
                   side_effect=Exception('smtp caido')):
            enviado, error = notifications.notify_operation_created(
                op, triggered_by=self.staff)

        self.assertFalse(enviado)
        self.assertIn('smtp caido', error)
        log = self._logs(channel='EMAIL').get()
        self.assertEqual(log.status, 'FAILED')
        self.assertIn('smtp caido', log.detail)

    def test_sin_cliente_en_catalogo_no_se_envia_pero_se_anota(self):
        op = self._operacion(manual='Alguien Que No Existe')

        enviado, _ = notifications.notify_operation_created(op, triggered_by=self.staff)

        self.assertFalse(enviado)
        self.assertEqual(len(mail.outbox), 0)
        log = self._logs(channel='EMAIL').get()
        self.assertEqual(log.status, 'SKIPPED')
        self.assertEqual(log.detail, 'customer_not_in_catalog')


class WhatsAppTests(NotificationTestBase):
    """El canal que antes se enviaba a ciegas."""

    def test_sin_credenciales_queda_omitido_en_la_bitacora(self):
        """
        `twilio` falto meses en produccion y el `except: pass` lo escondio. Sin
        credenciales el envio no puede salir, pero ahora al menos se sabe.
        """
        op = self._operacion(customer=self.cliente)

        with self.settings(TWILIO_ACCOUNT_SID='', TWILIO_AUTH_TOKEN='',
                           TWILIO_WHATSAPP_FROM=''):
            notifications.notify_operation_created(
                op, triggered_by=self.staff, force_whatsapp=True)

        log = self._logs(channel='WHATSAPP').get()
        self.assertEqual(log.status, 'SKIPPED')
        self.assertEqual(log.detail, 'twilio_not_configured')

    def test_el_checkbox_del_operador_manda_aunque_el_cliente_no_lo_pida(self):
        """El checkbox es una orden explicita; las preferencias no lo bloquean."""
        self.assertFalse(self.cliente.notify_whatsapp)
        op = self._operacion(customer=self.cliente)

        notifications.notify_operation_created(
            op, triggered_by=self.staff, force_whatsapp=True)

        self.assertEqual(self._logs(channel='WHATSAPP').count(), 1)

    def test_sin_checkbox_y_sin_preferencia_no_se_intenta(self):
        op = self._operacion(customer=self.cliente)
        notifications.notify_operation_created(op, triggered_by=self.staff)
        self.assertEqual(self._logs(channel='WHATSAPP').count(), 0)

    def test_el_cliente_que_lo_pidio_lo_recibe_sin_checkbox(self):
        self.cliente.notify_whatsapp = True
        self.cliente.save()
        op = self._operacion(customer=self.cliente)

        notifications.notify_operation_created(op, triggered_by=self.staff)

        self.assertEqual(self._logs(channel='WHATSAPP').count(), 1)

    def test_un_fallo_de_twilio_se_registra_en_vez_de_perderse(self):
        op = self._operacion(customer=self.cliente)

        with self.settings(TWILIO_ACCOUNT_SID='sid', TWILIO_AUTH_TOKEN='tok',
                           TWILIO_WHATSAPP_FROM='whatsapp:+1'):
            # Reproduce el ModuleNotFoundError que se tragaba el except: pass.
            with patch.dict('sys.modules', {'twilio.rest': None}):
                sent, error = notifications._deliver_whatsapp(
                    op, self.cliente, notifications.EVENT_CREATED,
                    '+521999', 'cuerpo', triggered_by=self.staff)

        self.assertFalse(sent)
        log = self._logs(channel='WHATSAPP').get()
        self.assertEqual(log.status, 'FAILED')
        self.assertTrue(log.detail)


class LiberacionDeMercanciaTests(NotificationTestBase):
    """Evento nuevo: la entrada en almacen salio despachada."""

    def setUp(self):
        self.cliente.notify_on_release = True
        self.cliente.save()

    def test_avisa_al_cliente_de_la_entrada_liberada(self):
        entrada = self._operacion(customer=self.cliente, custom_id='ED260816-0001')
        salida = self._operacion(custom_id='SD260816-0001', op_type='EXIT',
                                 manual='Otro Cliente')

        notifications.notify_goods_released(
            entrada, exit_op=salida, triggered_by=self.staff)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('SD260816-0001', mail.outbox[0].body)
        log = self._logs(event='GOODS_RELEASED', channel='EMAIL').get()
        self.assertEqual(log.status, 'SENT')
        self.assertEqual(log.operation_custom_id, 'ED260816-0001')

    def test_nace_apagado_para_no_estrenar_correos_sin_permiso(self):
        self.cliente.notify_on_release = False
        self.cliente.save()
        entrada = self._operacion(customer=self.cliente)

        notifications.notify_goods_released(entrada, triggered_by=self.staff)

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self._logs(event='GOODS_RELEASED').count(), 0)

    def test_no_le_escribe_dos_veces_a_quien_ya_recibio_el_alta(self):
        entrada = self._operacion(customer=self.cliente)

        notifications.notify_goods_released(
            entrada, triggered_by=self.staff,
            already_notified=['compras@cliente-a.com'])

        self.assertEqual(len(mail.outbox), 0)
        log = self._logs(event='GOODS_RELEASED').get()
        self.assertEqual(log.status, 'SKIPPED')
        self.assertEqual(log.detail, 'already_notified')

    def test_la_salida_dispara_el_aviso_de_las_entradas_que_despacha(self):
        """Prueba de extremo a extremo por la vista de alta."""
        entrada = self._operacion(customer=self.cliente, custom_id='ED260816-0009')
        otro = Catalog.objects.create(
            tenant=self.tenant, category='CUSTOMER', name='Cliente B',
            contact_email='b@cliente-b.com')

        self.client.force_login(self.staff)
        resp = self.client.post('/operations/create/', {
            'operation_type': 'EXIT',
            'date': '2026-08-16',
            'entry_dispatched': 'ED260816-0009',
            'customer_id': str(otro.pk),
            'shipper_text': 'Shipper X',
            'carrier_text': 'Carrier X',
            'bundle_type_text': 'Pallet',
            'bundle_qty': '3',
            'weight_lbs': '100',
            'description': 'Salida de prueba',
        })

        self.assertEqual(resp.status_code, 200)
        entrada.refresh_from_db()
        self.assertIn('SD', entrada.entry_dispatched)

        destinatarios = [addr for m in mail.outbox for addr in m.to]
        self.assertIn('b@cliente-b.com', destinatarios)      # alta de la salida
        self.assertIn('compras@cliente-a.com', destinatarios)  # liberacion

        self.assertEqual(
            self._logs(event='GOODS_RELEASED', status='SENT').count(), 1)


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': tempfile.mkdtemp()}},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class DocumentosNuevosTests(NotificationTestBase):
    """Evento nuevo: se subieron archivos al expediente."""

    def setUp(self):
        self.cliente.notify_on_documents = True
        self.cliente.save()
        self.op = self._operacion(customer=self.cliente)

    def _subir(self, user):
        self.client.force_login(user)
        return self.client.post(
            f'/digital/{self.op.pk}/upload/',
            {'files': SimpleUploadedFile('factura.pdf', b'%PDF-1.4 x',
                                         content_type='application/pdf')})

    def test_avisa_cuando_el_almacen_sube_documentos(self):
        self._subir(self.staff)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('factura.pdf', mail.outbox[0].body)
        self.assertEqual(
            self._logs(event='DOCUMENTS_ADDED', status='SENT').count(), 1)

    def test_no_avisa_si_el_cliente_sube_sus_propios_archivos(self):
        user = User.objects.create_user('cliente_a', password='x',
                                        email='juan@cliente-a.com')
        UserProfile.objects.create(user=user, tenant=self.tenant,
                                   role='customer', customer=self.cliente)

        self._subir(user)

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self._logs(event='DOCUMENTS_ADDED').count(), 0)

    def test_respeta_al_cliente_que_no_quiere_este_aviso(self):
        self.cliente.notify_on_documents = False
        self.cliente.save()

        self._subir(self.staff)

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self._logs(event='DOCUMENTS_ADDED').count(), 0)


class ArchivoRemoto:
    """
    Se comporta como un archivo guardado en R2: se abre y se lee, pero **no
    tiene ruta local**.

    Es la pieza que faltaba reproducir. El codigo viejo hacia
    `attach_file(doc.file.path)`, y con R2 eso lanza NotImplementedError, que un
    `except: pass` se tragaba: los correos salieron sin documentos desde la
    mudanza y nadie se entero.
    """

    def __init__(self, nombre, contenido):
        self.name = f'operations/2026/08/16/{nombre}'
        self._contenido = contenido
        self.size = len(contenido)

    @property
    def path(self):
        raise NotImplementedError("Este backend no tiene rutas locales.")

    def open(self, mode='rb'):
        return self

    def read(self):
        return self._contenido

    def close(self):
        pass


class DocumentoRemoto:
    def __init__(self, pk, nombre, contenido):
        self.pk = pk
        self.original_name = nombre
        self.file = ArchivoRemoto(nombre, contenido)


class OperacionConDocumentos:
    """Lo minimo que `_attach_documents` le pide a una operacion."""

    def __init__(self, documentos):
        self.documents = type('_Rel', (), {'all': lambda _self: documentos})()


class AdjuntosTests(NotificationTestBase):
    """Los documentos del expediente cuando el almacenamiento es remoto."""

    def _correo(self):
        from django.core.mail import EmailMessage
        return EmailMessage(subject='x', body='y', to=['a@b.com'])

    def test_el_documento_viaja_aunque_no_haya_ruta_local(self):
        email = self._correo()
        op = OperacionConDocumentos([
            DocumentoRemoto(1, 'factura.pdf', b'%PDF-1.4 contenido')])

        notifications._attach_documents(email, op)

        self.assertEqual([n for n, _, _ in email.attachments], ['factura.pdf'])
        self.assertEqual(email.attachments[0][1], b'%PDF-1.4 contenido')

    def test_un_archivo_demasiado_grande_se_omite_sin_romper_el_resto(self):
        email = self._correo()
        op = OperacionConDocumentos([
            DocumentoRemoto(1, 'chico.pdf', b'x' * 10),
            DocumentoRemoto(2, 'enorme.mp4', b'x' * (2 * 1024 * 1024)),
        ])

        with self.settings(EMAIL_MAX_ATTACHMENT_MB=1):
            notifications._attach_documents(email, op)

        adjuntos = [n for n, _, _ in email.attachments]
        self.assertIn('chico.pdf', adjuntos)
        self.assertNotIn('enorme.mp4', adjuntos)

    def test_un_archivo_ilegible_no_impide_los_demas(self):
        email = self._correo()
        roto = DocumentoRemoto(1, 'roto.pdf', b'x')
        roto.file.read = lambda: (_ for _ in ()).throw(OSError('no se pudo leer'))
        op = OperacionConDocumentos([
            roto, DocumentoRemoto(2, 'bueno.pdf', b'contenido')])

        notifications._attach_documents(email, op)

        self.assertEqual([n for n, _, _ in email.attachments], ['bueno.pdf'])

    def test_el_alta_adjunta_los_documentos_del_expediente(self):
        """El camino completo, con el almacenamiento real de las pruebas."""
        op = self._operacion(customer=self.cliente)
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(STORAGES={
                'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage',
                            'OPTIONS': {'location': tmp}},
                'staticfiles': {'BACKEND':
                                'django.contrib.staticfiles.storage.StaticFilesStorage'},
            }):
                OperationDocument.objects.create(
                    tenant=self.tenant, operation=op, file_type='DOCUMENT',
                    file=SimpleUploadedFile('bl.pdf', b'%PDF-1.4 bl'),
                    original_name='bl.pdf')

                notifications.notify_operation_created(op, triggered_by=self.staff)

                self.assertIn('bl.pdf',
                              [n for n, _, _ in mail.outbox[0].attachments])


class BlindajeTests(NotificationTestBase):
    """
    Registrar la operacion es lo importante; avisar es secundario.

    En el refactor `render_to_string` quedo fuera del try, asi que un fallo
    notificando podia devolver un 500 y hacerle perder el alta al operador.
    """

    def test_un_fallo_notificando_no_tumba_el_alta_de_la_operacion(self):
        self.client.force_login(self.staff)

        with patch('warehouse.notifications.render_to_string',
                   side_effect=Exception('template roto')):
            resp = self.client.post('/operations/create/', {
                'operation_type': 'ENTRY',
                'date': '2026-08-16',
                'customer_id': str(self.cliente.pk),
                'shipper_text': 'Shipper X',
                'carrier_text': 'Carrier X',
                'bundle_type_text': 'Pallet',
                'bundle_qty': '2',
                'weight_lbs': '50',
                'description': 'Entrada de prueba',
            })

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            WarehouseOperation.objects.filter(description='Entrada de prueba').exists())

    def test_el_error_se_devuelve_en_vez_de_propagarse(self):
        op = self._operacion(customer=self.cliente)

        with patch('warehouse.notifications.render_to_string',
                   side_effect=Exception('template roto')):
            enviado, error = notifications.notify_operation_created(
                op, triggered_by=self.staff)

        self.assertFalse(enviado)
        self.assertIn('template roto', error)

    def test_un_fallo_notificando_no_tumba_la_subida_de_documentos(self):
        op = self._operacion(customer=self.cliente)

        with patch('warehouse.notifications.resolve_customer',
                   side_effect=Exception('reventado')):
            self.assertFalse(
                notifications.notify_documents_added(op, ['x'], triggered_by=self.staff))


class EnvioManualTests(NotificationTestBase):
    """Los botones del detalle: se mandan siempre, pero ahora dejan rastro."""

    def test_el_reenvio_manual_ignora_las_preferencias_y_queda_anotado(self):
        self.cliente.notify_email = False
        self.cliente.save()
        op = self._operacion(customer=self.cliente)

        self.client.force_login(self.staff)
        resp = self.client.post(f'/operations/{op.pk}/email/',
                                {'recipient_email': 'quien-sea@example.com'})

        self.assertContains(resp, 'Report sent')
        self.assertEqual(len(mail.outbox), 1)
        log = self._logs(event='MANUAL', channel='EMAIL').get()
        self.assertEqual(log.status, 'SENT')
        self.assertEqual(log.recipient, 'quien-sea@example.com')

    def test_el_whatsapp_manual_reporta_el_error_en_vez_de_decir_enviado(self):
        """
        La vista respondia "WhatsApp sent" con solo que el cliente tuviera
        numero, aunque el envio hubiera reventado dentro de _send_whatsapp.
        """
        op = self._operacion(customer=self.cliente)

        self.client.force_login(self.staff)
        with self.settings(TWILIO_ACCOUNT_SID='', TWILIO_AUTH_TOKEN='',
                           TWILIO_WHATSAPP_FROM=''):
            resp = self.client.post(f'/operations/{op.pk}/whatsapp/')

        self.assertNotContains(resp, 'WhatsApp sent')
        self.assertEqual(self._logs(channel='WHATSAPP').get().status, 'SKIPPED')


class AislamientoDeLaBitacoraTests(NotificationTestBase):
    """La bitacora es multi-tenant como todo lo demas."""

    def test_cada_registro_queda_bajo_el_tenant_de_su_operacion(self):
        otro_tenant = Tenant.objects.create(
            name='Warehouse Dos', type='organization', subdomain='dos')
        cliente_dos = Catalog.objects.create(
            tenant=otro_tenant, category='CUSTOMER', name='Cliente Z',
            contact_email='z@dos.com')
        op_dos = WarehouseOperation.objects.create(
            tenant=otro_tenant, operation_type='ENTRY', custom_id='ED-DOS-1',
            customer=cliente_dos)

        notifications.notify_operation_created(
            self._operacion(customer=self.cliente), triggered_by=self.staff)
        notifications.notify_operation_created(op_dos)

        self.assertEqual(self._logs(tenant=self.tenant).count(), 1)
        self.assertEqual(self._logs(tenant=otro_tenant).count(), 1)
        self.assertEqual(
            self._logs(tenant=self.tenant).get().customer, self.cliente)


class PreferenciasDelCatalogoTests(NotificationTestBase):
    """La matriz canal x evento y su edicion desde la pestana Catalog."""

    def test_apagar_el_canal_silencia_todos_sus_eventos(self):
        self.cliente.notify_email = False
        self.cliente.notify_on_release = True
        self.cliente.save()

        self.assertFalse(self.cliente.wants_notification('EMAIL', 'GOODS_RELEASED'))
        self.assertFalse(self.cliente.wants_notification('EMAIL', 'OPERATION_CREATED'))

    def test_apagar_el_evento_lo_silencia_en_los_dos_canales(self):
        self.cliente.notify_whatsapp = True
        self.cliente.notify_on_create = False
        self.cliente.save()

        self.assertFalse(self.cliente.wants_notification('EMAIL', 'OPERATION_CREATED'))
        self.assertFalse(self.cliente.wants_notification('WHATSAPP', 'OPERATION_CREATED'))

    def test_los_valores_por_omision_reproducen_lo_que_ya_hacia_el_sistema(self):
        nuevo = Catalog.objects.create(
            tenant=self.tenant, category='CUSTOMER', name='Cliente Nuevo')

        # El correo del alta salia siempre; el WhatsApp solo por checkbox.
        self.assertTrue(nuevo.wants_notification('EMAIL', 'OPERATION_CREATED'))
        self.assertFalse(nuevo.wants_notification('WHATSAPP', 'OPERATION_CREATED'))
        # Los eventos nuevos nacen apagados.
        self.assertFalse(nuevo.wants_notification('EMAIL', 'GOODS_RELEASED'))
        self.assertFalse(nuevo.wants_notification('EMAIL', 'DOCUMENTS_ADDED'))

    def test_se_editan_desde_el_formulario_del_catalogo(self):
        self.client.force_login(self.staff)
        resp = self.client.post(f'/catalog/{self.cliente.pk}/edit/', {
            'name': self.cliente.name,
            'contact_email': self.cliente.contact_email,
            'notify_email': 'on',
            'notify_on_create': 'on',
            'notify_on_release': 'on',
        })

        self.assertEqual(resp.status_code, 200)
        self.cliente.refresh_from_db()
        self.assertTrue(self.cliente.notify_on_release)
        self.assertFalse(self.cliente.notify_whatsapp)
        self.assertFalse(self.cliente.notify_on_documents)

    def test_editar_otra_categoria_no_toca_las_preferencias(self):
        """
        El formulario solo pinta los checkboxes para clientes; leerlos en las
        demas categorias apagaria valores que nadie quiso cambiar.
        """
        shipper = Catalog.objects.create(
            tenant=self.tenant, category='SHIPPER', name='Shipper X')

        self.client.force_login(self.staff)
        self.client.post(f'/catalog/{shipper.pk}/edit/', {'name': 'Shipper X'})

        shipper.refresh_from_db()
        self.assertTrue(shipper.notify_email)
        self.assertTrue(shipper.notify_on_create)
