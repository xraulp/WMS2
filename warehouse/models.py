import logging
import os
import re
import uuid

from django.db import connection, models, transaction
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.text import slugify
# `gettext_lazy` y no `gettext`: las etiquetas de un modelo se evaluan al
# importar el modulo, cuando todavia no hay peticion ni idioma que aplicar.
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

# warehouse/models.py (al inicio, después de los imports)
ROLE_CHOICES = [
    ('superadmin', _('Super administrator')),
    ('admin', _('Administrator')),
    ('manager', _('Manager')),
    ('staff', _('Staff')),
    ('customer', _('Customer')),
]

# Los mismos roles, ordenados. Hace falta un orden porque la gestion de usuarios
# recibia el rol desde el formulario y lo guardaba tal cual: un administrador de
# una empresa podia crear -o ascender a alguien a- 'superadmin', que es el nivel
# mas alto que existe dentro del tenant. Con el orden se puede exigir la regla
# que faltaba: nadie reparte un nivel por encima del suyo.
ROLE_RANK = {
    'customer': 0,
    'staff': 1,
    'manager': 2,
    'admin': 3,
    'superadmin': 4,
}

# Categorias del catalogo reservadas al administrador de la empresa. Dar de alta
# un cliente y dar de alta un carrier son hoy la misma operacion con un valor
# distinto en el desplegable, y no deberian serlo: los operativos son trabajo
# diario de cualquiera que capture, mientras que un cliente decide a quien se le
# mandan los avisos y quien puede tener acceso al sistema.
CATALOG_ADMIN_CATEGORIES = {'CUSTOMER'}

# El catalogo esta partido en dos pantallas porque son dos trabajos distintos
# con dos publicos distintos: los clientes los mantiene el administrador de la
# empresa, y el resto -carriers, shippers, tipos de bulto- lo mantiene a diario
# quien captura las operaciones. Tenerlos juntos en una sola tabla obligaba a
# elegir la categoria en un desplegable y hacia imposible separar los permisos.
CATALOG_SCOPES = {
    'customers':   ['CUSTOMER'],
    'operational': ['SHIPPER', 'CARRIER', 'BUNDLE_TYPE', 'TYPE_OP', 'CC_EMAIL'],
}

def catalog_scope_of(category):
    """A que pantalla pertenece una categoria."""
    return 'customers' if category in CATALOG_ADMIN_CATEGORIES else 'operational'

class Catalog(models.Model):
    CATEGORY_CHOICES = [
        ('CUSTOMER',    _('Customer')),
        ('SHIPPER',     _('Shipper')),
        ('CARRIER',     _('Carrier')),
        ('BUNDLE_TYPE', _('Type of Bundle')),
        ('TYPE_OP',     _('Type of Operation')),
        ('CC_EMAIL',    _('CC Email')),
    ]
    tenant        = models.ForeignKey('Tenant', on_delete=models.CASCADE, null=True, blank=True, related_name='catalog_entries')
    category      = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    name          = models.CharField(max_length=200)
    abbreviation = models.CharField(max_length=50, blank=True, null=True,
                                     help_text='Abreviatura del cliente (ej: LBO, ACME)')
    contact_email = models.TextField(blank=True, null=True)
    phone         = models.CharField(max_length=50, blank=True, null=True)
    address       = models.TextField(blank=True, null=True)
    notes         = models.TextField(blank=True, null=True)
    whatsapp      = models.CharField(max_length=30, blank=True, null=True,
                                     help_text='+521XXXXXXXXXX')
    active        = models.BooleanField(default=True)
    created_at    = models.DateTimeField(auto_now_add=True)

    # ── Preferencias de notificacion (solo aplican a category='CUSTOMER') ──────
    # Los defaults reproducen el comportamiento que ya existia: el correo de alta
    # salia siempre y el de WhatsApp solo si el operador marcaba el checkbox. Por
    # eso los eventos nuevos nacen apagados: se activan cliente por cliente.
    notify_email        = models.BooleanField(default=True,
                            verbose_name='Notificar por email')
    notify_whatsapp     = models.BooleanField(default=False,
                            verbose_name='Notificar por WhatsApp')
    notify_on_create    = models.BooleanField(default=True,
                            verbose_name='Avisar al registrar la operacion')
    notify_on_release   = models.BooleanField(default=False,
                            verbose_name='Avisar al liberar la mercancia')
    notify_on_documents = models.BooleanField(default=False,
                            verbose_name='Avisar al agregar documentos')

    # Los dias que la mercancia de este cliente puede estar en bodega antes de
    # que la pantalla avise. Vacio significa "lo que diga la empresa": asi el
    # plazo general se puede cambiar en un sitio sin repasar cliente por cliente.
    alert_days          = models.PositiveIntegerField(null=True, blank=True,
                            verbose_name='Dias en bodega antes de avisar',
                            help_text='Vacio = usar el plazo general de la empresa')

    # En que idioma se le escribe a este cliente. Es suyo y no de quien manda el
    # correo: un documento se lee en el idioma de quien lo recibe, y el operador
    # que captura una entrada a las tres de la manana no tiene por que acordarse
    # de en que idioma habla cada cliente. Vacio = el idioma de la casa.
    LANGUAGE_CHOICES = [('', _('Default')), ('es', 'Español'), ('en', 'English')]
    language            = models.CharField(max_length=5, blank=True, default='',
                            choices=LANGUAGE_CHOICES,
                            verbose_name='Idioma de los correos y documentos')

    class Meta:
        ordering = ['category', 'name']

    def wants_notification(self, channel, event):
        """
        True si este cliente quiere recibir `event` por `channel`.

        Se cruzan dos ejes independientes: el canal (email / WhatsApp) y el
        evento. Apagar el canal silencia todos los eventos de ese canal; apagar
        el evento lo silencia en los dos canales.
        """
        channel_ok = {
            'EMAIL':    self.notify_email,
            'WHATSAPP': self.notify_whatsapp,
        }.get(channel, False)
        event_ok = {
            'OPERATION_CREATED': self.notify_on_create,
            'GOODS_RELEASED':    self.notify_on_release,
            'DOCUMENTS_ADDED':   self.notify_on_documents,
        }.get(event, False)
        return bool(channel_ok and event_ok)

    def __str__(self):
        return f"{self.get_category_display()} - {self.name}"


class UserProfile(models.Model):
    """
    Quien es cada persona dentro de una empresa: su rol y, si es cliente, a que
    cliente pertenece.

    Los permisos salen del rol y de nada mas. Durante mucho tiempo cada
    predicado de aqui llevaba pegado un `or self.user.is_superuser`, de modo
    que el superusuario de Django mandaba dentro de cualquier empresa a la que
    perteneciera sin que nadie se lo hubiera dado, y los niveles 1 y 2 acababan
    siendo la misma persona por construccion. Ese atajo se retiro: quien tenga
    que mandar en una empresa lo hace con el rol escrito en su perfil, y para
    administrar el producto esta `PlatformUser`.

    El `is_superuser` sigue existiendo como llave de emergencia -abre el admin
    de Django- y sigue valiendo para el panel de plataforma mientras no haya un
    administrador de plataforma de verdad; `platform_role()` lo explica.
    """
    user            = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    tenant          = models.ForeignKey('Tenant', on_delete=models.CASCADE, null=True, blank=True, related_name='users')
    role            = models.CharField(max_length=20, choices=ROLE_CHOICES, default='staff')
    # Aqui vivio `plain_password`, la contrasena de acceso guardada en claro para
    # que la pantalla de usuarios pudiera volver a mostrarla. Se retiro: una
    # contrasena se asigna, no se consulta, y quien la olvida recibe una nueva.
    customer        = models.ForeignKey(Catalog, on_delete=models.SET_NULL, null=True, blank=True,
                                         limit_choices_to={'category': 'CUSTOMER'})
    delete_password = models.CharField(max_length=128, blank=True, null=True,
                       help_text='Custom password required to delete records')
    # Como quiere ver la pantalla esta persona. Las dos preferencias van en el
    # perfil y no solo en el navegador porque quien las cambia espera
    # encontrarlas puestas al entrar desde otra computadora.
    #
    # Vacio no es un valor por defecto disfrazado: en el tema significa "el que
    # tenga el sistema operativo", y en el idioma, "el de la empresa". Guardar
    # 'light' o 'es' de entrada seria decidir por alguien que no ha decidido.
    THEME_CHOICES = [('', _('System')), ('light', _('Light')), ('dark', _('Dark'))]
    theme           = models.CharField(max_length=10, blank=True, default='',
                                       choices=THEME_CHOICES,
                                       verbose_name='Tema')
    LANGUAGE_CHOICES = [('', _('Automatic')), ('es', 'Español'), ('en', 'English')]
    language        = models.CharField(max_length=5, blank=True, default='',
                                       choices=LANGUAGE_CHOICES,
                                       verbose_name='Idioma')
    created_at      = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    def is_superadmin(self):
        return self.role == 'superadmin'

    def is_admin(self):
        return self.role == 'admin'

    def is_manager(self):
        return self.role == 'manager'

    def is_staff_role(self):
        return self.role == 'staff'

    def is_customer(self):
        return self.role == 'customer'

    def is_home(self):
        return self.role in ('superadmin', 'admin', 'manager')

    def can_delete(self):
        """
        Si puede borrar algo, sea una operacion o un archivo del expediente.

        Incluye al staff desde que el borrado dejo de ser una frontera de rol:
        buscar al administrador para quitar un archivo mal subido paraba el
        trabajo del dia. Lo que sustituye al permiso denegado es el rastro
        -contrasena de borrado, motivo escrito y renglon en la bitacora-, no
        la confianza.
        """
        return self.is_operator()

    def can_delete_operations(self):
        return self.is_operator()

    def can_delete_documents(self):
        return self.is_operator()

    def can_see_deletion_log(self):
        """
        Quien lee la bitacora de borrados y la papelera.

        Es vigilancia sobre el trabajo ajeno, asi que se queda en los roles de
        casa; el staff deja rastro, no lo audita.
        """
        return self.is_home()

    def can_purge_documents(self):
        """
        Quien destruye de verdad un archivo ya archivado.

        Solo el administrador de la empresa: la papelera no sirve de nada si
        cualquiera puede vaciarla.
        """
        return self.is_superadmin() or self.is_admin()

    def set_delete_password(self, raw):
        """
        Guarda la contrasena de borrado cifrada. Una cadena vacia la quita.
        """
        from django.contrib.auth.hashers import make_password
        raw = (raw or '').strip()
        self.delete_password = make_password(raw) if raw else None

    def check_delete_password(self, raw):
        """
        Comprueba la contrasena de borrado.

        El campo estuvo guardado en claro y visible en la pantalla de usuarios,
        de modo que aqui se acepta tambien un valor sin cifrar y se aprovecha
        para cifrarlo en el acto: asi una base que no haya pasado por la
        migracion se arregla sola en el primer borrado.
        """
        from django.contrib.auth.hashers import check_password, identify_hasher

        guardada = self.delete_password
        if not guardada or not raw:
            return False
        try:
            identify_hasher(guardada)
        except ValueError:
            if raw != guardada:
                return False
            self.set_delete_password(raw)
            self.save(update_fields=['delete_password'])
            return True
        return check_password(raw, guardada)

    def is_operator(self):
        """
        Si es personal de la empresa que opera el almacen todos los dias.

        Incluye al `staff`: es un operador completo, no un usuario de solo
        lectura. Captura operaciones, las corrige, reenvia avisos y saca
        reportes. Lo que no hace es dar de alta clientes ni borrar; para eso
        estan `can_edit_catalog()` y `can_delete()`.

        No confundirlo con `is_home()`, que deja fuera al staff y solo debe
        usarse donde la diferencia sea el borrado o la administracion.
        """
        return self.role in ('superadmin', 'admin', 'manager', 'staff')

    def can_create_operations(self):
        return self.is_operator()

    def can_edit_operations(self):
        """
        Si puede corregir una operacion ya capturada.

        Va con `can_create_operations()` a proposito: quien captura se
        equivoca al capturar, y negarle la correccion obligaba a pedirsela a
        un manager por un peso mal tecleado.
        """
        return self.is_operator()

    def can_manage_users(self):
        return self.role in ('superadmin', 'admin')

    def can_access_tenant(self, tenant):
        """Verifica si el usuario puede acceder a los datos de un tenant."""
        if not self.tenant_id:
            return False
        if self.tenant == tenant:
            return True
        # Un usuario de organización puede acceder a sus branches
        if self.tenant.is_organization and tenant.parent_id == self.tenant_id:
            return True
        return False

    def role_rank(self):
        """
        Nivel del usuario en la jerarquia de roles.

        Sale del rol y de nada mas. El `is_superuser` de Django contaba aqui
        como el maximo, con lo que un superusuario mandaba dentro de una
        empresa sin que nadie se lo hubiera dado: ver la nota de clase.
        """
        return ROLE_RANK.get(self.role, 0)

    def can_assign_role(self, role):
        """
        Si puede repartir ese rol al crear o modificar a otro usuario.

        Se permite el propio nivel para que una empresa pueda tener dos
        administradores; lo que no se permite es subir por encima.
        """
        if not self.can_manage_users():
            return False
        if role not in ROLE_RANK:
            return False
        return ROLE_RANK[role] <= self.role_rank()

    def can_manage_user(self, otro):
        """
        Si puede tocar la cuenta de ese otro usuario: cambiarle el rol, la
        contrasena o borrarla.

        Validar solo el rol que se reparte dejaba la puerta entornada: un
        administrador no podia nombrar un superadmin, pero si cambiarle la
        contrasena al que ya hubiera y entrar como el. Un usuario sin perfil
        cuenta como el nivel mas bajo.
        """
        if not self.can_manage_users():
            return False
        rango_otro = otro.role_rank() if otro is not None else 0
        return rango_otro <= self.role_rank()

    def can_edit_catalog(self, category):
        """
        Si puede dar de alta, editar o dar de baja esa parte del catalogo.

        Los clientes son cosa del administrador de la empresa; el resto del
        catalogo -carriers, shippers, tipos de bulto- lo mantiene quien captura.
        La comprobacion vive aqui y se hace en el servidor a proposito: esconder
        la opcion del desplegable no impide mandar el POST a mano.
        """
        if self.is_customer():
            return False
        if category in CATALOG_ADMIN_CATEGORIES:
            return self.is_superadmin() or self.is_admin()
        return True

    def can_see_tab(self, tab):
        # 'customers' es la pestana de clientes, separada del catalogo
        # operativo. Manager y staff la ven, pero en solo lectura: necesitan
        # consultar el contacto de un cliente aunque no puedan darlo de alta.
        if self.role == 'customer':
            return tab in ('database', 'digital', 'reports')
        if self.role in ('staff', 'manager', 'admin'):
            return tab in ('form', 'database', 'catalog', 'customers',
                           'digital', 'reports')
        return True

    def __str__(self):
        return f"{self.user.username} ({self.role})"


class Warehouse(models.Model):
    """
    Una bodega del tenant. Multi-ubicacion: una empresa puede operar varias
    --Laredo, Monterrey, el patio de al lado-- y hasta ahora el sistema daba por
    hecho que solo habia una, de modo que no se podia decir donde entro la
    mercancia.

    El `code` es lo que se teclea y lo que sale en las etiquetas; el `name` es
    para la pantalla.
    """
    tenant     = models.ForeignKey('Tenant', on_delete=models.CASCADE,
                                   related_name='warehouses')
    name       = models.CharField(max_length=120, verbose_name='Nombre')
    code       = models.CharField(max_length=10, verbose_name='Codigo',
                                  help_text='Corto, sale en el codigo de la ubicacion (ej. LRD)')
    address    = models.TextField(blank=True, null=True)
    notes      = models.TextField(blank=True, null=True)
    active     = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        # El codigo identifica la bodega dentro de su empresa, no en todo el
        # sistema: dos empresas distintas pueden tener las dos su bodega "MTY".
        unique_together = [('tenant', 'code')]

    def __str__(self):
        return f'{self.code} - {self.name}'


class Location(models.Model):
    """
    Una posicion dentro de una bodega: zona, pasillo, estante, nivel y hueco.

    Los cinco campos son texto libre y ninguno es obligatorio, a proposito. Una
    bodega chica trabaja con "Zona A" y nada mas; una grande numera hasta el
    hueco. Obligar a rellenar los cinco convertiria el alta en un tramite y
    acabaria con ubicaciones llamadas "-" para poder guardar.
    """
    TIPOS = [
        ('STORAGE',    _('Storage')),
        ('RECEIVING',  _('Receiving')),
        ('SHIPPING',   _('Shipping')),
        ('STAGING',    _('Dock / Staging')),
        ('PICKING',    _('Picking')),
        ('QUARANTINE', _('Quarantine')),
        ('DAMAGED',    _('Damaged goods')),
        ('RETURNS',    _('Returns')),
    ]

    tenant    = models.ForeignKey('Tenant', on_delete=models.CASCADE,
                                  related_name='locations')
    warehouse = models.ForeignKey('Warehouse', on_delete=models.CASCADE,
                                  related_name='locations', verbose_name='Bodega')
    zone      = models.CharField(max_length=20, blank=True, null=True, verbose_name='Zona')
    aisle     = models.CharField(max_length=20, blank=True, null=True, verbose_name='Pasillo')
    rack      = models.CharField(max_length=20, blank=True, null=True, verbose_name='Estante')
    level     = models.CharField(max_length=20, blank=True, null=True, verbose_name='Nivel')
    position  = models.CharField(max_length=20, blank=True, null=True, verbose_name='Posicion')
    kind      = models.CharField(max_length=12, choices=TIPOS, default='STORAGE',
                                 verbose_name='Tipo de ubicacion')
    # El codigo que se lee y se dicta. Se arma solo con lo que se haya rellenado
    # y se guarda, en vez de calcularse al vuelo, para poder buscarlo en la base
    # y para que una ubicacion ya usada no cambie de nombre si manana alguien
    # decide numerar los niveles.
    code      = models.CharField(max_length=80, blank=True, verbose_name='Codigo')
    notes     = models.TextField(blank=True, null=True)
    active    = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['warehouse__code', 'zone', 'aisle', 'rack', 'level', 'position']
        unique_together = [('warehouse', 'code')]

    def componer_codigo(self):
        partes = [self.warehouse.code if self.warehouse_id else '']
        partes += [p for p in (self.zone, self.aisle, self.rack, self.level, self.position)
                   if p and p.strip()]
        return '-'.join(p.strip() for p in partes if p and p.strip())

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self.componer_codigo()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code or f'Ubicacion {self.pk}'

    @property
    def descripcion(self):
        """Lo que se lee cuando el codigo no basta: "Zona A / Pasillo 3 / Nivel 2"."""
        etiquetas = [('Zona', self.zone), ('Pasillo', self.aisle), ('Estante', self.rack),
                     ('Nivel', self.level), ('Posicion', self.position)]
        return ' / '.join(f'{nombre} {valor}' for nombre, valor in etiquetas
                          if valor and valor.strip())


class WarehouseOperation(models.Model):
    # Los cuatro tipos, y no hay mas: la entrada (ED) y la salida (SD) de
    # siempre, el trasbordo (TD), cuando la mercancia sale hacia otro
    # transporte sin llegar a almacenarse, y la revision (RD), cuando se
    # inspecciona carga ya guardada sin moverla. El codigo corto es el que se
    # usa hablando, es el prefijo del Custom ID, y por eso va tambien en la
    # etiqueta: en pantalla se lee "Entry (ED)", como lo dictan por radio.
    TYPE_CHOICES = [
        ('ENTRY', _('Entry (ED)')),
        ('EXIT',  _('Exit (SD)')),
        ('TD',    _('Transfer (TD)')),
        ('RD',    _('Revision (RD)')),
    ]

    # El prefijo con el que nace el Custom ID de cada tipo. `SD` importa mas de
    # lo que parece: `status` lee `entry_dispatched` buscando un token que
    # empiece por SD para decidir si una entrada esta liberada.
    PREFIJO_DEL_TIPO = {'ENTRY': 'ED', 'EXIT': 'SD', 'TD': 'TD', 'RD': 'RD'}

    # Los tipos que consumen entradas ya guardadas. Una revision no: la
    # mercancia no se va, solo se mira.
    TIPOS_QUE_DESPACHAN = ('EXIT', 'TD')

    # Los tipos que llevan ubicacion, que son uno solo. La ubicacion dice donde
    # quedo guardada la carga, y solo la entrada la guarda: la salida y el
    # trasbordo la retiran, y la revision la deja donde ya estaba.
    TIPOS_CON_UBICACION = ('ENTRY',)
    tenant               = models.ForeignKey('Tenant', on_delete=models.CASCADE, null=True, blank=True, 
                                             related_name='operations')
    date                 = models.DateField(default=timezone.now)
    operation_type       = models.CharField(max_length=5, choices=TYPE_CHOICES)
    custom_id            = models.CharField(max_length=20, unique=True, blank=True)
    entry_dispatched     = models.CharField(max_length=500, blank=True, null=True)
    # Donde quedo guardada la mercancia. Solo los tipos de
    # `TIPOS_CON_UBICACION` -- hoy la entrada, y solo ella -- los rellenan. Son
    # opcionales porque hay ochenta y seis operaciones ya guardadas sin
    # ubicacion, y porque una empresa que trabaja con una sola nave sin
    # posiciones no tiene por que rellenarlos.
    warehouse            = models.ForeignKey('Warehouse', on_delete=models.SET_NULL,
                                             null=True, blank=True,
                                             related_name='operations',
                                             verbose_name='Bodega')
    location             = models.ForeignKey('Location', on_delete=models.SET_NULL,
                                             null=True, blank=True,
                                             related_name='operations',
                                             verbose_name='Ubicacion')
    customer             = models.ForeignKey(Catalog, on_delete=models.SET_NULL, null=True, blank=True,
                                             related_name='operations_as_customer',
                                             limit_choices_to={'category': 'CUSTOMER'})
    customer_name_manual = models.CharField(max_length=200, blank=True, null=True)
    shipper              = models.ForeignKey(Catalog, on_delete=models.SET_NULL, null=True, blank=True,
                                             related_name='operations_as_shipper',
                                             limit_choices_to={'category': 'SHIPPER'})
    shipper_name_manual  = models.CharField(max_length=200, blank=True, null=True)
    invoice              = models.CharField(max_length=200, blank=True, null=True)
    po_order             = models.CharField(max_length=200, blank=True, null=True)
    seal                 = models.CharField(max_length=200, blank=True, null=True)
    carrier              = models.ForeignKey(Catalog, on_delete=models.SET_NULL, null=True, blank=True,
                                             related_name='operations_as_carrier',
                                             limit_choices_to={'category': 'CARRIER'})
    carrier_name_manual  = models.CharField(max_length=200, blank=True, null=True)
    pro                  = models.CharField(max_length=200, blank=True, null=True)
    trailer              = models.CharField(max_length=200, blank=True, null=True)
    bundle_type          = models.ForeignKey(Catalog, on_delete=models.SET_NULL, null=True, blank=True,
                                             related_name='operations_as_bundle_type',
                                             limit_choices_to={'category': 'BUNDLE_TYPE'})
    bundle_type_manual   = models.CharField(max_length=200, blank=True, null=True)
    bundle_qty           = models.PositiveIntegerField(blank=True, null=True)
    weight_lbs           = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    weight_kgs           = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    description          = models.TextField(blank=True, null=True)
    note                 = models.TextField(blank=True, null=True)
    customer_notes       = models.TextField(blank=True, null=True,
                            help_text='Notes added by the customer')
    damage               = models.BooleanField(default=False)
    damage_description   = models.TextField(blank=True, null=True)
    created_at           = models.DateTimeField(auto_now_add=True)
    updated_at           = models.DateTimeField(auto_now=True)
    email_sent           = models.BooleanField(default=False)
    email_sent_at        = models.DateTimeField(null=True, blank=True)

        # Nuevos campos para nomenclatura de archivos
        # NUEVOS CAMPOS - Nomenclatura de archivos

    ref_aa = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='REF AA',
        help_text='Referencia AA - no obligatorio'
    )
    ref_dys = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='DYS',
        help_text='Referencia DYS - no obligatorio'
    )
    pedimento = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='PED',
        help_text='Número de pedimento - no obligatorio'
    )

    class Meta:
        ordering = ['-date', '-created_at']

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_operations'
    )

    class Meta:
        ordering = ['-date', '-created_at']


    def __str__(self):
        return self.custom_id or f"OP-{self.pk}"

    @property
    def status(self):
        if self.entry_dispatched and self.entry_dispatched.strip():
            for token in self.entry_dispatched.replace(',', ' ').split():
                if token.upper().startswith('SD'):
                    return 'Released Goods'
        return 'In Warehouse'

    # ── Permanencia en bodega ────────────────────────────────────────────────
    # La pregunta que nadie podia contestar sin repasar la tabla a ojo: que
    # llevaba demasiado tiempo guardado. Se calcula al mirar, no se guarda: un
    # campo "dias" habria que recalcularlo todas las noches, y aqui no hay cron.

    @property
    def dias_en_bodega(self):
        """Dias desde la fecha de la operacion. None si ya no cuenta."""
        if self.operation_type != 'ENTRY' or self.status != 'In Warehouse':
            return None
        return (timezone.now().date() - self.date).days

    @property
    def plazo_de_alerta(self):
        """
        Los dias que esta mercancia puede estar guardada sin avisar.

        Manda el plazo del cliente; si no tiene, el de la empresa. Un cliente
        sin ficha --los que se teclean a mano-- se rige por el de la empresa.
        """
        if self.customer and self.customer.alert_days:
            return self.customer.alert_days
        if self.tenant_id and self.tenant.alert_days_default:
            return self.tenant.alert_days_default
        return None

    @property
    def alerta_permanencia(self):
        """
        'vencida', 'urgente' o None.

        Se avisa al cumplirse el plazo y se sube de tono al doble. Dos niveles y
        no cinco: lo que hace falta distinguir es "hay que moverlo" de "esto ya
        se nos paso", y una escala mas fina se convierte en un semaforo que
        nadie mira.
        """
        dias  = self.dias_en_bodega
        plazo = self.plazo_de_alerta
        if dias is None or not plazo:
            return None
        if dias >= plazo * 2:
            return 'urgente'
        if dias >= plazo:
            return 'vencida'
        return None

    def get_customer_display(self):
        return self.customer.name if self.customer else (self.customer_name_manual or '—')

    def get_shipper_display(self):
        return self.shipper.name if self.shipper else (self.shipper_name_manual or '—')

    def get_carrier_display(self):
        return self.carrier.name if self.carrier else (self.carrier_name_manual or '—')

    def get_bundle_type_display_name(self):
        return self.bundle_type.name if self.bundle_type else (self.bundle_type_manual or '—')

    # def get_customer_email(self):
    #     if self.customer and self.customer.contact_email:
    #         return self.customer.contact_email.split(',')[0].strip()
    #     if self.customer_name_manual:
    #         try:
    #             entry = Catalog.objects.filter(
    #                 category='CUSTOMER', name__iexact=self.customer_name_manual.strip(),
    #                 active=True).first()
    #             if entry and entry.contact_email:
    #                 return entry.contact_email.split(',')[0].strip()
    #         except Exception:
    #             pass
    #     return None

###Solución: Reemplaza SOLO la función get_customer_email por esta versión IDÉNTICA (pero con indentación correcta)

    def get_customer_email(self):
        if self.customer and self.customer.contact_email:
            return self.customer.contact_email.split(',')[0].strip()
        if self.customer_name_manual:
            try:
                entry = Catalog.objects.filter(
                    category='CUSTOMER', name__iexact=self.customer_name_manual.strip(),
                    active=True).first()
                if entry and entry.contact_email:
                    return entry.contact_email.split(',')[0].strip()
            except Exception:
                pass
        return None


    def get_customer_whatsapp(self):
        if self.customer and self.customer.whatsapp:
            return self.customer.whatsapp
        return None

    def generate_custom_id(self):
        prefix = self.PREFIJO_DEL_TIPO.get(self.operation_type, 'OP')
        date_str = self.date.strftime('%y%m%d')
        count = WarehouseOperation.objects.filter(
            operation_type=self.operation_type, date=self.date
        ).exclude(pk=self.pk).count()
        return f"{prefix}{date_str}-{str(count+1).zfill(4)}"

    def save(self, *args, **kwargs):
        if not self.custom_id:
            self.custom_id = self.generate_custom_id()
        super().save(*args, **kwargs)

###Agrega esta función en models.py dentro de la clase WarehouseOperation
###Busca la sección donde están los métodos (después de get_customer_email) y agrega esto:052826
    def get_customer_email_raw(self):
        """Retorna el string completo de email(s) sin dividir (puede contener comas)"""
        if self.customer and self.customer.contact_email:
            return self.customer.contact_email
        if self.customer_name_manual:
            try:
                entry = Catalog.objects.filter(
                    category='CUSTOMER', name__iexact=self.customer_name_manual.strip(),
                    active=True).first()
                if entry and entry.contact_email:
                    return entry.contact_email
            except Exception:
                pass
        return None

###Agrega esta función en models.py dentro de la clase WarehouseOperation
###Busca la sección donde están los métodos (después de get_customer_email) y agrega esto:052826

# Donde viven los archivos mientras estan en la papelera. Es un prefijo del
# almacen, no una carpeta del disco: cambiar de sitio el objeto es lo que hace
# que su URL anterior deje de servir a quien ya la tuviera.
PREFIJO_PAPELERA = 'papelera/'

# Cuanto del nombre original se conserva en la ruta. Sirve para reconocer el
# archivo al mirar el bucket; el nombre completo vive en `original_name`, que es
# lo que se le enseña al usuario y lo que viaja en la descarga.
LARGO_NOMBRE_EN_RUTA = 60


def ruta_documento(instance, filename):
    """
    Donde se guarda el archivo de un documento del expediente.

    Antes era `operations/%Y/%m/%d/` mas el nombre original tal cual, y eso
    tenia tres problemas que se dieron los tres en produccion:

    1. **Se perdian archivos.** Dos documentos con el mismo nombre subidos el
       mismo dia daban la misma ruta, y `AWS_S3_FILE_OVERWRITE` -- que vale
       `True` mientras nadie diga lo contrario -- hacia que el segundo pisara al
       primero sin avisar. La base guardaba las dos filas apuntando al mismo
       objeto, asi que la pantalla no mostraba ningun error: mostraba el archivo
       equivocado.
    2. **No aislaba las empresas.** La ruta no llevaba el tenant, de modo que el
       `report.pdf` de una podia pisar el de otra del mismo dia.
    3. **Era adivinable.** Fecha mas nombre corriente es una ruta que se acierta
       probando, y el bucket se sirve por un dominio publico: quien conociera el
       dominio -- cualquier usuario, porque sale en el HTML -- podia sondear
       documentos ajenos sin pasar por el sistema.

    El `uuid` corta los tres: la ruta deja de colisionar y deja de adivinarse.
    El tenant va delante porque hace evidente de quien es cada archivo al mirar
    el bucket, que es donde se diagnostica cuando algo va mal.
    """
    empresa = 'sin-empresa'
    tenant = getattr(instance, 'tenant', None)
    if tenant is None and getattr(instance, 'operation_id', None):
        # El documento puede llegar sin tenant propio; el de su operacion es el
        # mismo, y vale mas que mandarlo todo al cajon de los huerfanos.
        tenant = getattr(instance.operation, 'tenant', None)
    if tenant is not None and tenant.subdomain:
        empresa = slugify(tenant.subdomain)[:40] or 'sin-empresa'

    base, punto, extension = os.path.basename(filename or '').rpartition('.')
    if not punto:
        base, extension = extension, ''

    nombre = slugify(base)[:LARGO_NOMBRE_EN_RUTA] or 'archivo'
    extension = re.sub(r'[^A-Za-z0-9]', '', extension).lower()[:10]
    if extension:
        nombre = f'{nombre}.{extension}'

    return 'operations/{empresa}/{fecha}/{unico}-{nombre}'.format(
        empresa=empresa,
        fecha=timezone.localtime().strftime('%Y/%m/%d'),
        unico=uuid.uuid4().hex[:12],
        nombre=nombre,
    )


def ruta_logo(instance, filename):
    """
    Donde se guarda el logo de una empresa.

    Mismo criterio que `ruta_documento`, y por las mismas razones: la empresa
    delante para que se vea de quien es al mirar el bucket, y un identificador
    aleatorio para que dos empresas que suban `logo.png` no colisionen y para
    que cambiar el logo no deje el anterior sirviendose desde una ruta que
    alguien tuviera cacheada.
    """
    empresa = slugify(getattr(instance, 'subdomain', '') or '')[:40] or 'sin-empresa'

    base, punto, extension = os.path.basename(filename or '').rpartition('.')
    if not punto:
        base, extension = extension, ''
    nombre = slugify(base)[:LARGO_NOMBRE_EN_RUTA] or 'logo'
    extension = re.sub(r'[^A-Za-z0-9]', '', extension).lower()[:10]
    if extension:
        nombre = f'{nombre}.{extension}'

    return f'logos/{empresa}/{uuid.uuid4().hex[:12]}-{nombre}'


class DocumentosVivosManager(models.Manager):
    """
    Los documentos que siguen en el expediente.

    Es el manager por defecto a proposito: `operation.documents.all()` se
    recorre en las plantillas, en el ZIP y en los adjuntos del correo, y en
    ninguno de esos sitios debe aparecer lo que esta en la papelera. Lo que
    necesite ver tambien lo archivado usa `OperationDocument.todos`.
    """

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class OperationDocument(models.Model):
    FILE_TYPE_CHOICES = [('PHOTO', 'Photo'), ('DOCUMENT', 'Document'), ('VIDEO', 'Video'), ('OTHER', 'Other')]
    tenant        = models.ForeignKey('Tenant', on_delete=models.CASCADE, null=True, blank=True, related_name='documents')
    operation     = models.ForeignKey(WarehouseOperation, on_delete=models.CASCADE, related_name='documents')
    file_type     = models.CharField(max_length=10, choices=FILE_TYPE_CHOICES, default='OTHER')
    # `max_length` sube de los 100 por omision porque la ruta nueva es mas
    # larga: lleva la empresa, el identificador unico y, mientras esta en la
    # papelera, el prefijo por delante. Con 100 una ruta larga se rechazaba.
    file          = models.FileField(upload_to=ruta_documento, max_length=255)
    original_name = models.CharField(max_length=255, blank=True)
    uploaded_by   = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    uploaded_at   = models.DateTimeField(auto_now_add=True)
    digital_name  = models.CharField(max_length=100, blank=True, null=True)

    # Posicion dentro del expediente, para poder arreglar una secuencia que
    # nacio desordenada. El orden de subida sirve cuando las fotos se toman y
    # se suben una a una desde el movil, pero si el operador las selecciona de
    # un tiron en el explorador, el navegador las manda en el orden que le
    # parece -normalmente alfabetico- y la secuencia llega mal desde el
    # principio. Eso importa porque de ese orden depende que la documentacion
    # aduanal salga bien.
    #
    # Cero significa "nunca se reordeno a mano", y ahi manda la fecha de
    # subida: por eso el `ordering` los encadena. Asi los documentos que ya
    # existian siguen exactamente donde estaban, sin migracion de datos.
    orden         = models.PositiveIntegerField(default=0, verbose_name="Orden")

    # De que mensaje del hilo llego este archivo, si es que llego por ahi.
    #
    # Los adjuntos del chat **son** documentos del expediente, no una segunda
    # coleccion: lo que se manda por el hilo es lo mismo que el ZIP y los
    # correos van a buscar despues, y un archivo que viviera solo dentro de una
    # conversacion seria una segunda verdad -- justo lo que el hilo vino a
    # evitar. Por eso el adjunto se guarda como cualquier otro documento y esto
    # es solo la marca de por donde entro, que es lo que permite pintarlo
    # dentro del globo del mensaje.
    #
    # Va aqui y no en `Message` para que un mensaje pueda llevar varios
    # archivos: quien manda tres fotos de la misma tarima esta diciendo una
    # sola cosa, y tres mensajes seguidos con una foto cada uno convierten esa
    # cosa en tres.
    mensaje = models.ForeignKey('Message', on_delete=models.SET_NULL, null=True,
                                blank=True, related_name='adjuntos')

    # Papelera. Un archivo del expediente ya salio impreso y adjunto en un
    # correo, asi que quitarlo de la vista y destruirlo no son la misma
    # decision: lo primero lo hace quien opera, lo segundo el administrador.
    deleted_at     = models.DateTimeField(null=True, blank=True)
    deleted_by     = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                       blank=True, related_name='documentos_borrados')
    delete_reason  = models.TextField(blank=True)

    objects = DocumentosVivosManager()
    # El manager sin filtrar. Va segundo para que el filtrado sea el que usan
    # las relaciones inversas, y se llama distinto para que su uso se vea.
    todos   = models.Manager()

    class Meta:
        # `base_manager_name` decide el manager que Django usa por dentro para
        # seguir claves foraneas. Sin esto, `doc.operation` de un documento ya
        # archivado podria dejar de resolverse.
        base_manager_name = 'todos'

        # El orden de los archivos es informacion, no presentacion. En una
        # entrada se fotografia la misma pieza varias veces -la serie o el
        # lote, el peso, la tabla nutrimental- y la documentacion aduanal se
        # arma siguiendo esa secuencia: si el ZIP los entrega en otro orden, lo
        # que llega al agente aduanal esta mal aunque no falte ningun archivo.
        #
        # Sin esta linea ninguna consulta pedia orden, asi que PostgreSQL
        # devolvia las filas como le convenia. Coincidia con el de insercion
        # por casualidad, y se rompia en cuanto una fila se actualizaba:
        # archivar y restaurar un documento lo mandaba al final de la lista.
        #
        # `orden` es la posicion puesta a mano, y vale cero mientras nadie la
        # toque; `uploaded_at` es el orden en que se subieron, y `pk` desempata
        # la subida multiple, donde varios archivos comparten el instante.
        ordering = ['orden', 'uploaded_at', 'pk']

    @property
    def en_papelera(self):
        return self.deleted_at is not None

    def _mover_archivo(self, nuevo_nombre):
        """
        Cambia el archivo de sitio dentro del almacen y devuelve si se logro.

        El objeto se copia al destino y se borra el origen: en R2 no hay
        "mover". Lo importante es que la URL vieja deje de servir, asi que si el
        borrado del origen falla se avisa pero no se deshace nada; la referencia
        buena ya es la nueva.

        Nunca lanza. Un almacen que no responde no puede impedir que alguien
        saque de la vista un archivo mal subido: el registro y la papelera son
        lo que no puede fallar, y el archivo se queda donde estaba.
        """
        viejo = self.file.name if self.file else ''
        if not viejo or viejo == nuevo_nombre:
            return False

        almacen = self.file.storage
        try:
            with self.file.open('rb') as contenido:
                guardado = almacen.save(nuevo_nombre, contenido)
        except Exception as e:
            logger.warning('No se pudo mover el archivo del documento %s a %s: %s',
                           self.pk, nuevo_nombre, e)
            return False

        self.file.name = guardado
        try:
            almacen.delete(viejo)
        except Exception as e:
            # Queda una copia en la ruta anterior. Es lo unico que este metodo
            # no puede garantizar, y conviene que se vea en el log.
            logger.warning('Copia huerfana en %s tras mover el documento %s: %s',
                           viejo, self.pk, e)
        return True

    def archivar(self, usuario, motivo):
        """
        Lo saca del expediente sin destruir el archivo.

        Ademas lo mueve bajo `papelera/`. Mientras estuvo en su ruta original,
        quien ya tuviera el enlace podia seguir abriendolo aunque el archivo
        hubiera desaparecido de la pantalla — el bucket sirve por URL, sin
        preguntar quien mira. Al cambiarlo de sitio, esa URL deja de servir.

        Lo que no hace: la ruta nueva es tan publica como la anterior, asi que
        esto invalida el enlace que alguien tuviera, no el acceso al archivo.
        Destruirlo es cosa de la purga.
        """
        movido = self._mover_archivo(PREFIJO_PAPELERA + (self.file.name or ''))

        self.deleted_at    = timezone.now()
        self.deleted_by    = usuario
        self.delete_reason = motivo or ''
        campos = ['deleted_at', 'deleted_by', 'delete_reason']
        if movido:
            campos.append('file')
        self.save(update_fields=campos)

    def restaurar(self):
        """
        Lo devuelve al expediente, y el archivo a su ruta de siempre.
        """
        nombre = self.file.name if self.file else ''
        movido = False
        if nombre.startswith(PREFIJO_PAPELERA):
            movido = self._mover_archivo(nombre[len(PREFIJO_PAPELERA):])

        self.deleted_at    = None
        self.deleted_by    = None
        self.delete_reason = ''
        campos = ['deleted_at', 'deleted_by', 'delete_reason']
        if movido:
            campos.append('file')
        self.save(update_fields=campos)

    def __str__(self):
        return f"{self.get_file_type_display()} - {self.operation.custom_id}"


class DeletionLog(models.Model):
    """
    Que se borro, quien lo borro y por que.

    Registra las dos cosas que se pueden destruir: la operacion entera y el
    archivo del expediente. El `kind` distingue una de otra porque la pantalla
    de bitacora las muestra juntas -para el administrador lo que importa es la
    linea de tiempo, no el tipo de objeto-.
    """
    KIND_CHOICES = [('OPERATION', 'Operacion'), ('DOCUMENT', 'Documento')]

    deleted_by     = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    deleted_at     = models.DateTimeField(auto_now_add=True)
    kind           = models.CharField(max_length=10, choices=KIND_CHOICES, default='OPERATION')
    custom_id      = models.CharField(max_length=20)
    operation_type = models.CharField(max_length=5, blank=True)
    operation_date = models.DateField(null=True, blank=True)
    customer_name  = models.CharField(max_length=200, blank=True)
    description    = models.TextField(blank=True)
    # El nombre del archivo cuando `kind` es DOCUMENT: el registro tiene que
    # decir cual de los archivos del expediente se fue.
    document_name  = models.CharField(max_length=255, blank=True)
    reason         = models.TextField(blank=True, null=True)
    tenant         = models.ForeignKey('Tenant', on_delete=models.SET_NULL, null=True, blank=True, related_name='deletion_logs')

    class Meta:
        ordering = ['-deleted_at']

    def __str__(self):
        return f"Deleted {self.custom_id} by {self.deleted_by} at {self.deleted_at}"

    # ============================================================
# MULTI-TENENCIA JERÁRQUICA (NIVEL 1, 2, 3)
# ============================================================

class Tenant(models.Model):
    """
    Nivel 2: Corporativo / Empresa Matriz (type='organization')
    Nivel 3: Sucursal / Franquicia (type='branch')
    """
    TYPES = (
        ('organization', 'Corporativo / Empresa Matriz'),
        ('branch', 'Sucursal / Franquicia'),
    )

    name = models.CharField(max_length=200, verbose_name="Nombre")
    type = models.CharField(max_length=20, choices=TYPES, verbose_name="Tipo")
    subdomain = models.CharField(max_length=50, unique=True, verbose_name="Subdominio")
    parent = models.ForeignKey(
        'self', 
        on_delete=models.CASCADE, 
        null=True, 
        blank=True, 
        related_name='children',
        verbose_name="Tenant Padre"
    )
    is_active = models.BooleanField(default=True, verbose_name="Activo")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Creado el")
    
    # Configuraciones específicas
    config = models.JSONField(default=dict, blank=True, verbose_name="Configuración")
    
    # Facturación (solo para organizations)
    billing_email = models.EmailField(blank=True, null=True, verbose_name="Email de Facturación")

    # A los cuantos dias en bodega una entrada empieza a avisar. Es el plazo de
    # la casa; cada cliente puede tener el suyo (`Catalog.alert_days`), porque
    # una semana para uno es lo normal y para otro ya es una factura de
    # almacenaje. Siete dias es lo que pidio la operacion como punto de partida.
    alert_days_default = models.PositiveIntegerField(
        default=7, verbose_name='Dias en bodega antes de avisar')

    # El logo que sale en los documentos que la empresa manda a sus clientes:
    # reportes, etiquetas y lo que va adjunto en los correos.
    #
    # Estaba escrito en el codigo -- un unico archivo del repositorio, el de la
    # primera empresa -- asi que cualquier otra empresa firmaba sus reportes con
    # el logo ajeno. Es el mismo vicio que el nombre escrito a mano en la
    # pantalla de entrada, y mas visible, porque una imagen no se lee por
    # encima: se ve.
    #
    # Sin logo, los documentos caen al nombre de la empresa en texto, que es lo
    # que ya hacian cuando el archivo no estaba.
    logo = models.ImageField(upload_to=ruta_logo, max_length=255, blank=True,
                             null=True, verbose_name="Logo")
    plan = models.CharField(
        max_length=50, 
        default='starter',
        choices=[('starter', 'Starter'), ('pro', 'Pro'), ('enterprise', 'Enterprise')],
        verbose_name="Plan"
    )

    class Meta:
        verbose_name = "Tenant"
        verbose_name_plural = "Tenants"
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"

    @property
    def is_organization(self):
        return self.type == 'organization'

    @property
    def is_branch(self):
        return self.type == 'branch'

    @property
    def root_tenant(self):
        """Obtiene el tenant raíz (organización) de esta jerarquía."""
        if self.is_organization:
            return self
        return self.parent

    @property
    def email_footer_note(self):
        """
        Leyenda extra al pie de los correos, si la empresa quiere una.

        El pie de `report_email.html` traía escrito "DYSER Group LLC" y
        "Provider for RDL Systems LLC", así que el correo de cualquier otra
        empresa iba firmado con el nombre de una ajena. El nombre sale ahora de
        `Tenant.name`; esta leyenda, que es propia de cada quien, se guarda en
        `config` para no pedir una migración por cada dato de marca.

        Se pone desde el admin, en el JSON de Configuración:

            {"email_footer_note": "Provider for RDL Systems LLC."}

        Sin ella el pie simplemente no la pinta.
        """
        return (self.config or {}).get('email_footer_note', '').strip()

    def get_all_branches(self):
        """Obtiene todas las sucursales de esta organización (solo si es organización)."""
        if self.is_organization:
            return self.children.filter(type='branch', is_active=True)
        return Tenant.objects.none()

    def get_all_children(self):
        """Obtiene todos los tenants hijos (directos e indirectos)."""
        children = list(self.children.all())
        for child in children:
            children.extend(child.get_all_children())
        return children


class Role(models.Model):
    """
    Roles jerárquicos con herencia de permisos.
    """
    name = models.CharField(max_length=50, verbose_name="Nombre del Rol")
    tenant = models.ForeignKey(
        Tenant, 
        on_delete=models.CASCADE, 
        related_name='roles',
        verbose_name="Tenant"
    )
    permissions = models.ManyToManyField(
        'auth.Permission', 
        blank=True,
        verbose_name="Permisos"
    )
    parent = models.ForeignKey(
        'self', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='children_roles',
        verbose_name="Rol Padre"
    )
    description = models.TextField(blank=True, null=True, verbose_name="Descripción")

    class Meta:
        verbose_name = "Rol"
        verbose_name_plural = "Roles"
        unique_together = [['name', 'tenant']]

    def __str__(self):
        return f"{self.name} ({self.tenant.name})"

    def get_all_permissions(self):
        """Obtiene todos los permisos heredados (incluyendo los del padre)."""
        perms = set(self.permissions.all())
        if self.parent:
            perms.update(self.parent.get_all_permissions())
        return perms

    def has_permission(self, perm_codename):
        """Verifica si el rol tiene un permiso específico (incluyendo herencia)."""
        return perm_codename in [p.codename for p in self.get_all_permissions()]


class Subscription(models.Model):
    """
    Suscripción por tenant principal (Nivel 2).
    """
    tenant = models.OneToOneField(
        Tenant, 
        on_delete=models.CASCADE, 
        related_name='subscription',
        verbose_name="Tenant"
    )
    plan = models.CharField(
        max_length=50, 
        default='starter',
        choices=[('starter', 'Starter'), ('pro', 'Pro'), ('enterprise', 'Enterprise')],
        verbose_name="Plan"
    )
    start_date = models.DateField(auto_now_add=True, verbose_name="Fecha de Inicio")
    end_date = models.DateField(null=True, blank=True, verbose_name="Fecha de Fin")
    is_active = models.BooleanField(default=True, verbose_name="Activa")
    
    # Métricas de uso
    storage_used_gb = models.FloatField(default=0, verbose_name="Almacenamiento usado (GB)")
    operations_count = models.IntegerField(default=0, verbose_name="Número de Operaciones")
    
    # Facturación — OBSOLETO. Lo sustituye el modelo `Invoice`.
    #
    # Estos tres campos viven en una fila por empresa, así que solo cabía una
    # factura por cliente: emitir la de septiembre pisaba la de agosto. Sin
    # historial, sin estado de pago y sin forma de saber quién debía.
    #
    # Se conservan para no perder lo que hubiera capturado antes de `Invoice`.
    # Nada los escribe ya, y ninguna pantalla los lee.
    invoice_number = models.CharField(max_length=50, blank=True, null=True, verbose_name="Número de Factura")
    invoice_date = models.DateField(null=True, blank=True, verbose_name="Fecha de Factura")
    amount_usd = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Monto (USD)")

    class Meta:
        verbose_name = "Suscripción"
        verbose_name_plural = "Suscripciones"
        ordering = ['tenant__name']

    def __str__(self):
        return f"{self.tenant.name} - {self.plan}"


class InvoiceSequence(models.Model):
    """
    Contador de facturas, uno por año.

    Una numeración de facturas no puede tener huecos ni repeticiones: es el
    identificador con el que un cliente reclama y con el que se cuadra el
    cobro. Contar las que existen no sirve -cancelar una liberaría su número,
    que ya salió al cliente-, así que el contador se guarda y solo sube, igual
    que el de los documentos del expediente.

    Va por año porque el número lleva el año dentro: `INV-2026-0001`. El primer
    día de enero la serie vuelve a empezar en 1 sin que nadie tenga que
    acordarse.
    """
    year       = models.PositiveIntegerField(unique=True)
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Consecutivo de facturas"
        verbose_name_plural = "Consecutivos de facturas"

    def __str__(self):
        return f"{self.year} → {self.last_value}"


class Invoice(models.Model):
    """
    Una factura emitida a una empresa de la plataforma.

    Antes esto vivía dentro de `Subscription`, en tres campos sueltos
    -`invoice_number`, `invoice_date`, `amount_usd`- y `Subscription` es una
    fila por empresa. Es decir: cabía **una sola factura por cliente**, y
    emitir la de septiembre borraba la de agosto. No había historial, no había
    estado de pago y no había forma de saber quién debía. En la práctica no se
    podía facturar.

    Decisiones que conviene no deshacer sin pensarlo:

    * **El monto se congela aquí.** Se captura al emitir y se queda. Si mañana
      sube el precio del plan, las facturas ya emitidas no cambian: dicen lo
      que se cobró.
    * **El plan también se copia**, por lo mismo. La empresa puede cambiar de
      plan después, y la factura tiene que seguir diciendo qué se le facturó.
    * **«Vencida» no es un estado guardado**, se deduce de la fecha. Guardarlo
      obligaría a un proceso diario que fuera marcándolas, y ese proceso es
      justo lo que no tenemos: el día que no corriera, la pantalla mentiría.
    * **Una factura emitida no se borra**, se cancela con su motivo. El número
      ya salió al cliente y no vuelve a usarse.
    """
    PENDIENTE = 'pendiente'
    PAGADA    = 'pagada'
    CANCELADA = 'cancelada'
    ESTADOS = [
        (PENDIENTE, 'Pendiente'),
        (PAGADA,    'Pagada'),
        (CANCELADA, 'Cancelada'),
    ]

    # PROTECT y no CASCADE: una factura es un registro de cobro y no puede
    # desaparecer porque alguien dé de baja la empresa en el admin. Dar de baja
    # se hace con `is_active`, que no borra nada.
    tenant   = models.ForeignKey('Tenant', on_delete=models.PROTECT,
                                 related_name='invoices', verbose_name="Empresa")
    numero   = models.CharField(max_length=20, unique=True, verbose_name="Número")

    # Qué periodo cubre. Se guarda entero y no solo el mes porque un ajuste o
    # una primera factura a mitad de mes no empiezan el día 1.
    periodo_inicio = models.DateField(verbose_name="Periodo desde")
    periodo_fin    = models.DateField(verbose_name="Periodo hasta")

    emitida_el = models.DateField(verbose_name="Emitida el")
    vence_el   = models.DateField(verbose_name="Vence el")

    plan      = models.CharField(max_length=50, blank=True, verbose_name="Plan facturado")
    monto_usd = models.DecimalField(max_digits=10, decimal_places=2,
                                    verbose_name="Monto (USD)")

    estado = models.CharField(max_length=12, choices=ESTADOS, default=PENDIENTE,
                              verbose_name="Estado")

    pagada_el          = models.DateField(null=True, blank=True, verbose_name="Pagada el")
    referencia_de_pago = models.CharField(max_length=120, blank=True,
                                          verbose_name="Referencia de pago")

    cancelada_el     = models.DateField(null=True, blank=True, verbose_name="Cancelada el")
    motivo_de_cancelacion = models.TextField(blank=True, verbose_name="Motivo de cancelación")

    # Cuando se le mando al cliente, si es que se le mando. No es lo mismo que
    # emitida: una factura puede existir en el sistema y no haber salido nunca,
    # y esa diferencia es justo la que hay que ver antes de reclamar un pago.
    enviada_el = models.DateTimeField(null=True, blank=True, verbose_name="Enviada el")

    notas      = models.TextField(blank=True, verbose_name="Notas")
    emitida_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name='facturas_emitidas')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Factura"
        verbose_name_plural = "Facturas"
        # De la más reciente a la más vieja, que es como se lee un estado de
        # cuenta. `numero` desempata las emitidas el mismo día.
        ordering = ['-emitida_el', '-numero']

    def __str__(self):
        return f"{self.numero} · {self.tenant.name} · {self.monto_usd} USD"

    @property
    def esta_vencida(self):
        """Pendiente y con la fecha pasada. No se guarda: se mira al preguntar."""
        return (self.estado == self.PENDIENTE
                and self.vence_el < timezone.localdate())

    @property
    def dias_de_atraso(self):
        if not self.esta_vencida:
            return 0
        return (timezone.localdate() - self.vence_el).days

    def marcar_pagada(self, cuando=None, referencia=''):
        """
        Registra el cobro.

        Solo desde pendiente: una factura cancelada no se cobra, y volver a
        cobrar una pagada seria pisar la fecha del cobro real.
        """
        if self.estado != self.PENDIENTE:
            raise ValueError('Solo una factura pendiente puede marcarse pagada.')
        self.estado = self.PAGADA
        self.pagada_el = cuando or timezone.localdate()
        self.referencia_de_pago = (referencia or '').strip()
        self.save(update_fields=['estado', 'pagada_el', 'referencia_de_pago'])

    def cancelar(self, motivo):
        """
        La deja sin efecto, conservando el numero.

        Una factura pagada no se cancela: lo que hubo fue un cobro, y borrarlo
        de esta manera dejaria el dinero sin explicacion. Para ese caso se
        emite una nota de credito, que hoy no existe y por eso esto se niega en
        vez de improvisar.
        """
        if self.estado == self.PAGADA:
            raise ValueError('Una factura pagada no se cancela.')
        if self.estado == self.CANCELADA:
            return
        self.estado = self.CANCELADA
        self.cancelada_el = timezone.localdate()
        self.motivo_de_cancelacion = (motivo or '').strip()
        self.save(update_fields=['estado', 'cancelada_el', 'motivo_de_cancelacion'])

    @classmethod
    def siguiente_numero(cls, anio=None):
        """
        Aparta el siguiente numero de la serie del año y lo devuelve.

        Con la fila bloqueada donde el motor lo permite: sin eso, dos personas
        emitiendo a la vez leen el mismo valor y se llevan el mismo numero, que
        en una serie de facturas es el peor de los errores posibles.
        """
        anio = anio or timezone.localdate().year
        with transaction.atomic():
            fila, _ = InvoiceSequence.objects.get_or_create(year=anio)
            if connection.features.has_select_for_update:
                fila = InvoiceSequence.objects.select_for_update().get(pk=fila.pk)
            fila.last_value += 1
            fila.save(update_fields=['last_value'])
            return f'INV-{anio}-{fila.last_value:04d}'


class NotificationLog(models.Model):
    """
    Bitacora de cada aviso enviado al cliente (Tenant nivel 2).

    Antes solo quedaba el flag `email_sent` en la operacion y de WhatsApp no
    quedaba ningun rastro, asi que un envio fallido era invisible. Aqui se
    registra un renglon por destinatario y canal, incluidos los fallos y los
    que se omitieron por preferencia del cliente.
    """
    CHANNEL_CHOICES = [
        ('EMAIL',    'Email'),
        ('WHATSAPP', 'WhatsApp'),
    ]
    EVENT_CHOICES = [
        ('OPERATION_CREATED', 'Operación registrada'),
        ('GOODS_RELEASED',    'Mercancía liberada'),
        ('DOCUMENTS_ADDED',   'Documentos agregados'),
        ('MANUAL',            'Envío manual'),
        # Este no sale de una operacion sino de la plataforma: es la factura
        # que se le manda a la empresa. Va en la misma bitacora porque la
        # pregunta que se responde es la misma -- "¿le llego o no?".
        ('INVOICE_SENT',      'Factura enviada'),
        # El aviso de que hay un mensaje nuevo en el hilo de una operacion.
        ('CHAT_MESSAGE',      'Mensaje en el hilo'),
    ]
    STATUS_CHOICES = [
        ('SENT',    'Enviada'),
        ('FAILED',  'Fallida'),
        ('SKIPPED', 'Omitida'),
    ]

    tenant     = models.ForeignKey('Tenant', on_delete=models.CASCADE, null=True, blank=True,
                                   related_name='notification_logs')
    operation  = models.ForeignKey(WarehouseOperation, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='notifications')
    # Se copia el custom_id porque la operacion se puede borrar y la bitacora
    # tiene que seguir diciendo de que envio se trataba, igual que DeletionLog.
    operation_custom_id = models.CharField(max_length=30, blank=True)
    customer   = models.ForeignKey(Catalog, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='notifications',
                                   limit_choices_to={'category': 'CUSTOMER'})
    channel    = models.CharField(max_length=10, choices=CHANNEL_CHOICES)
    event      = models.CharField(max_length=20, choices=EVENT_CHOICES)
    status     = models.CharField(max_length=10, choices=STATUS_CHOICES)
    recipient  = models.CharField(max_length=500, blank=True)
    subject    = models.CharField(max_length=300, blank=True)
    # Para SKIPPED guarda el motivo ('no_recipient', 'preference_off', ...) y
    # para FAILED el texto de la excepcion.
    detail     = models.TextField(blank=True)
    triggered_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='triggered_notifications')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Notificación"
        verbose_name_plural = "Notificaciones"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', '-created_at']),
            models.Index(fields=['operation']),
        ]

    def __str__(self):
        return f"{self.get_channel_display()} {self.status} → {self.recipient or '—'}"

class DocumentSequence(models.Model):
    """
    Contador de documentos del expediente, por empresa y por día.

    El nombre digital de un documento es `DDMMAA-N`, y ese N salía de contar los
    documentos que la empresa tenía subidos ese día. Contar no es lo mismo que
    continuar: al borrar uno el contador retrocedía y la siguiente subida
    repetía un nombre ya entregado. En el peor caso -borrar el primero de dos-
    los dos documentos vivos acababan llamándose igual, y el operador que busca
    `170826-2` en el expediente no sabe cuál de los dos le están dando.

    Deducirlo del máximo que sigue existiendo tampoco basta: al borrar el
    último, su número vuelve a quedar libre, y el nombre ya salió impreso y
    adjunto en un correo. Por eso el contador se guarda, y solo sube.

    La fila se siembra la primera vez con el número más alto que ya hubiera en
    la base para ese día, así que los expedientes que existen desde antes de
    este cambio siguen numerándose donde se quedaron, sin migración de datos.
    """
    tenant     = models.ForeignKey('Tenant', on_delete=models.CASCADE,
                                   related_name='document_sequences')
    # La fecha en el mismo formato en que va dentro del nombre (DDMMAA), para
    # que la correspondencia con `digital_name` sea directa y no haya que
    # convertirla en cada consulta.
    day        = models.CharField(max_length=6)
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Consecutivo de documentos"
        verbose_name_plural = "Consecutivos de documentos"
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'day'],
                                    name='unique_document_sequence_per_day'),
        ]

    def __str__(self):
        return f"{self.tenant_id}/{self.day} → {self.last_value}"


PLATFORM_ROLE_CHOICES = [
    ('admin', _('Platform administrator')),
    ('staff', _('Platform support')),
]


class PlatformUser(models.Model):
    """
    Quien administra el SaaS, que no es lo mismo que quien administra una
    empresa.

    Hasta ahora el nivel de plataforma no existia como tal: tenia una sola
    llave, el `is_superuser` de Django. Eso obligaba a elegir entre dar acceso
    total -el admin de Django y los datos de todas las empresas- o no dar
    ninguno, asi que un equipo de soporte era imposible. Y en el otro sentido,
    `UserProfile.is_superadmin()` cuenta a cualquier superusuario como el rol
    mas alto dentro de su empresa, con lo que los dos niveles acababan siendo la
    misma persona por construccion.

    Este modelo vive aparte de `UserProfile` a proposito. Meter el nivel de
    plataforma como un rol mas del perfil habria reproducido justamente la
    mezcla que se quiere deshacer: un usuario de plataforma no pertenece a
    ninguna empresa, y no tener tenant es lo que hace que `get_tenant_or_404` lo
    deje fuera de todas las pantallas del tenant.

    Los dos niveles:

    - `admin`: lo critico. Dar de alta una empresa y nombrar a su
      administrador, activarla o desactivarla, cambiarle el plan, y repartir
      este mismo acceso.
    - `staff`: el dia a dia. Consultar el estado de las empresas y la bitacora
      de envios para atender un "a este cliente no le llegan los correos". Mira,
      no toca.
    """
    user       = models.OneToOneField(User, on_delete=models.CASCADE,
                                      related_name='platform_access')
    role       = models.CharField(max_length=20, choices=PLATFORM_ROLE_CHOICES,
                                  default='staff')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Usuario de plataforma"
        verbose_name_plural = "Usuarios de plataforma"
        ordering = ['user__username']

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"

    def is_platform_admin(self):
        return self.role == 'admin'


# ── EL HILO DE LA OPERACION ──────────────────────────────────────────────────
#
# De que lado escribe cada quien. No es "quien es el usuario" sino "en nombre de
# quien habla": del lado del tenant contesta el del turno, no siempre la misma
# persona, y del lado del cliente puede escribir cualquiera de las personas que
# ese cliente tenga dadas de alta. Por eso el hilo cuelga de la operacion y no
# de una pareja de usuarios.
LADO_TENANT   = 'TENANT'
LADO_CLIENTE  = 'CUSTOMER'
LADO_CHOICES  = [
    (LADO_TENANT,  'Empresa'),
    (LADO_CLIENTE, 'Cliente'),
]


class Conversation(models.Model):
    """
    El hilo de mensajes de una operacion, entre la empresa y su cliente.

    Existe uno por operacion y se crea la primera vez que alguien escribe: una
    operacion sin conversacion es lo normal, no una fila que falte.

    Lo que se conversa sobre una operacion -"manden la foto de la etiqueta",
    "el pedimento va con este numero", "ya llego mi carga"- es hoy informacion
    que vive en el WhatsApp de alguien y que el turno siguiente no encuentra.
    Colgar el hilo de la operacion es lo que la convierte en parte del
    expediente: queda junto a las fotos y los documentos, y la lee quien tome
    el caso manana.

    No hay hilos internos. Todo lo que se escribe aqui lo ve el cliente, y esa
    regla tiene que seguir siendo evidente para quien escribe. El dia que hagan
    falta notas internas seran mensajes marcados y pintados aparte, no un
    silencio que haya que recordar.
    """
    tenant     = models.ForeignKey('Tenant', on_delete=models.CASCADE,
                                   related_name='conversations')
    operation  = models.OneToOneField(WarehouseOperation, on_delete=models.CASCADE,
                                      related_name='conversation')
    created_at = models.DateTimeField(auto_now_add=True)

    # Cuando entro el ultimo mensaje. Se guarda en vez de deducirse porque lo
    # que lo pide es ordenar la lista de hilos y marcar los que tienen algo
    # nuevo, y eso se consulta mucho mas de lo que se escribe.
    last_message_at = models.DateTimeField(null=True, blank=True)

    # Cuando se aviso por ultima vez a cada lado. El aviso por correo es lo que
    # hace que el chat exista -nadie se queda mirando la pantalla- pero un
    # correo por mensaje convierte una conversacion de diez lineas en diez
    # correos, y a la tercera vez el destinatario deja de abrirlos. Con estas
    # dos fechas se avisa del primer mensaje y se callan los siguientes
    # mientras la conversacion sigue viva; ver AVISO_ESPERA en notifications.
    avisado_al_tenant_at  = models.DateTimeField(null=True, blank=True)
    avisado_al_cliente_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Conversacion"
        verbose_name_plural = "Conversaciones"
        ordering = ['-last_message_at', '-created_at']

    def __str__(self):
        return f"Hilo de {self.operation.custom_id}"

    def sin_leer_para(self, user):
        """
        Cuantos mensajes tiene esta conversacion que ese usuario no ha visto.

        Los propios nunca cuentan: uno no tiene mensajes sin leer de si mismo.
        Quien nunca abrio el hilo los tiene todos sin leer, que es lo que hace
        que el primer mensaje de un cliente nuevo se vea.
        """
        pendientes = self.messages.exclude(author_id=user.pk)
        marca = self.reads.filter(user=user).first()
        if marca and marca.last_read_at:
            pendientes = pendientes.filter(created_at__gt=marca.last_read_at)
        return pendientes.count()

    def marcar_leida(self, user, cuando=None):
        """Deja constancia de que ese usuario vio el hilo hasta este momento."""
        ConversationRead.objects.update_or_create(
            conversation=self, user=user,
            defaults={'last_read_at': cuando or timezone.now()},
        )


class Message(models.Model):
    """
    Un mensaje del hilo. No se edita y no se borra.

    Esa es la diferencia entre un chat y una nota: lo que se dijo sobre una
    operacion forma parte de su historia, y de el pueden colgar decisiones -un
    numero de pedimento, una instruccion de despacho- que despues alguien tiene
    que poder consultar tal como se escribieron.
    """
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE,
                                     related_name='messages')
    author       = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                     blank=True, related_name='mensajes_enviados')

    # El nombre queda congelado en el mensaje, igual que el monto en la
    # factura: si manana se da de baja a quien escribio, el hilo tiene que
    # seguir diciendo quien dijo cada cosa.
    author_name  = models.CharField(max_length=150, blank=True)

    side         = models.CharField(max_length=10, choices=LADO_CHOICES)
    body         = models.TextField()
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Mensaje"
        verbose_name_plural = "Mensajes"
        # El orden de una conversacion es su contenido. `pk` desempata los
        # mensajes que caen en el mismo instante.
        ordering = ['created_at', 'pk']
        indexes = [models.Index(fields=['conversation', 'created_at'])]

    def __str__(self):
        return f"{self.author_name}: {self.body[:40]}"

    @property
    def es_del_cliente(self):
        return self.side == LADO_CLIENTE


class ConversationRead(models.Model):
    """
    Hasta donde ha leido cada persona.

    Es por usuario y no por lado: del lado de la empresa hay varias personas
    que ven el mismo hilo, y que lo haya abierto una no significa que las demas
    ya se enteraron.
    """
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE,
                                     related_name='reads')
    user         = models.ForeignKey(User, on_delete=models.CASCADE,
                                     related_name='hilos_leidos')
    last_read_at = models.DateTimeField()

    class Meta:
        verbose_name = "Marca de lectura"
        verbose_name_plural = "Marcas de lectura"
        unique_together = [('conversation', 'user')]

    def __str__(self):
        return f"{self.user.username} leyo {self.conversation_id} hasta {self.last_read_at}"
