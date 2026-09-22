import logging
import os
import re
import uuid
from decimal import Decimal

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

def siguiente_consecutivo(modelo, prefijo, fecha=None, campo='custom_id'):
    """
    El siguiente numero libre de la forma `PREFIJO` + `AAMMDD` + `-NNNN`.

    Se cuenta por el prefijo del propio numero y no por la fecha de alta: el
    lookup `__date` resuelve la fecha en la zona horaria de la aplicacion y
    `datetime.date()` en UTC, y con unas horas de diferencia las dos no son la
    misma -- el contador miraba el dia equivocado y el numero se repetia.

    El bucle no es paranoia: dos altas a la vez cuentan lo mismo y piden el
    mismo numero, y aqui eso es una excepcion de base de datos en la cara de
    quien estaba creando una tarea.
    """
    from django.utils import timezone as _tz
    fecha = fecha or _tz.localdate()
    raiz = f'{prefijo}{fecha.strftime("%y%m%d")}-'
    cuantos = modelo.objects.filter(**{f'{campo}__startswith': raiz}).count()
    while True:
        cuantos += 1
        numero = f'{raiz}{cuantos:04d}'
        if not modelo.objects.filter(**{campo: numero}).exists():
            return numero


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

    # ── La cadena de entrega (solo para category='CUSTOMER') ─────────────────
    # Van en la ficha y no en cada tarea de cruce porque son casi siempre los
    # mismos para un cliente. Un dato que se teclea una vez al ano en vez de
    # una vez por embarque es un dato que casi nunca sale mal, y aqui salir mal
    # significa que la mercancia se le entrega a quien no es. Al emitir la
    # remision se proponen y se pueden cambiar ese dia si toca otra linea.
    rfc = models.CharField(max_length=20, blank=True, default='',
                           verbose_name='RFC')
    linea_de_enlace = models.CharField(
        max_length=200, blank=True, default='',
        verbose_name='Linea transportista de enlace',
        help_text='A quien se le deja la carga en la frontera mexicana')
    domicilio_de_enlace = models.TextField(
        blank=True, default='', verbose_name='Domicilio de entrega en la frontera')
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

    # ── El numero de pedimento, desglosado ───────────────────────────────────
    # `pedimento` se queda donde esta y sigue siendo el numero escrito: es lo
    # que sale en los nombres de archivo, en los reportes y en las busquedas, y
    # es lo unico que tienen las operaciones capturadas antes de esto.
    #
    # Estas tres casillas son ese mismo numero por dentro. No duplican el dato
    # -- `save` compone `pedimento` a partir de ellas -- sino que lo hacen
    # legible para el sistema: con la aduana y la patente separadas, el candado
    # del agente aduanal se comprueba sin preguntarle a nadie y sin un campo
    # nuevo que alguien tenga que acordarse de rellenar. El desglose vive en
    # `warehouse/pedimentos.py`, que es quien sabe del Anexo 22.
    ped_aduana = models.CharField(
        max_length=2, blank=True, default='',
        verbose_name='Aduana de despacho',
        help_text='2 digitos. 24 = Nuevo Laredo, 80 = Colombia')
    ped_patente = models.CharField(
        max_length=4, blank=True, default='',
        verbose_name='Patente del agente aduanal',
        help_text='4 digitos')
    ped_consecutivo = models.CharField(
        max_length=7, blank=True, default='',
        verbose_name='Ano y consecutivo',
        help_text='7 digitos: el ultimo digito del ano mas el consecutivo')

    # Cuando se espera la mercancia. Se teclea al capturar porque el
    # transportista y la guia llegan por correo del cliente antes que la carga,
    # y con eso el reporte de impuestos puede enseñar un ETA en vez de un
    # "no ha llegado", que no dice nada.
    eta = models.DateField(
        null=True, blank=True,
        verbose_name='ETA',
        help_text='Fecha estimada de llegada')

    # Lo marca quien recibe la mercancia, que es el unico momento del proceso
    # en que alguien tiene esa caja delante. Decide solo si el pedimento
    # necesita fotos de numeros de serie para poder mandarse a revision: sin
    # esto, esa comprobacion habria que hacerla de memoria cada vez, y las
    # comprobaciones de memoria son las que fallan el dia que hay prisa.
    #
    # Ojo con no confundirlo con las fotos de la mercancia, que son otra cosa:
    # esas se toman siempre, son del expediente digital y no suben al pedimento.
    has_serial_numbers = models.BooleanField(
        default=False,
        verbose_name='Esta mercancia tiene numeros de serie')

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

    # El estado se guarda, se compara y se filtra por su texto en ingles: es lo
    # que viaja en el formulario de busqueda y lo que esperan las vistas. Por
    # eso traducirlo es cosa de la presentacion y nunca del valor -- quien
    # filtra estando en espanol sigue mandando 'In Warehouse'.
    @property
    def status_label(self):
        """El estado como se enseña, en el idioma que este puesto."""
        if self.status == 'Released Goods':
            return _('Released Goods')
        return _('In Warehouse')

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

    # ── El pedimento, leido por dentro ───────────────────────────────────────
    # Las tres propiedades de abajo son la unica forma en que el resto del
    # sistema deberia preguntar por la aduana y la patente de una operacion.
    # Miran primero las casillas y, si estan vacias, intentan desglosar el
    # numero escrito: asi una operacion capturada antes de que existieran las
    # casillas responde igual, siempre que su numero tenga la forma buena.

    @property
    def pedimento_desglosado(self):
        """`(aduana, patente, consecutivo)`, vengan de donde vengan."""
        from . import pedimentos
        if self.ped_aduana or self.ped_patente or self.ped_consecutivo:
            return (self.ped_aduana or '', self.ped_patente or '',
                    self.ped_consecutivo or '')
        return pedimentos.desglosar(self.pedimento)

    @property
    def patente_aduanal(self):
        """La patente del agente aduanal, o cadena vacia si no se sabe."""
        return self.pedimento_desglosado[1]

    @property
    def aduana_de_despacho(self):
        """La aduana por la que despacha, o cadena vacia si no se sabe."""
        return self.pedimento_desglosado[0]

    def save(self, *args, **kwargs):
        if not self.custom_id:
            self.custom_id = self.generate_custom_id()
        # El numero escrito se compone de las casillas y no al reves: quien
        # teclea llena las tres casillas, y `pedimento` es el resultado. Solo
        # se pisa cuando las tres estan completas, para no borrar el numero de
        # las operaciones viejas -- las que tienen texto libre y ninguna
        # casilla -- ni el de una importacion de Excel.
        from . import pedimentos
        compuesto = pedimentos.numero_corrido(
            self.ped_aduana, self.ped_patente, self.ped_consecutivo)
        if compuesto:
            self.pedimento = compuesto
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
    FILE_TYPE_CHOICES = [('PHOTO', _('Photo')), ('DOCUMENT', _('Document')),
                         ('VIDEO', _('Video')), ('OTHER', _('Other'))]
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

    # Que papel hace este archivo dentro del expediente, cuando hace uno
    # concreto. Casi todos no hacen ninguno -- son fotos de la mercancia, guias,
    # notas -- y por eso el valor por defecto es vacio y no hay que rellenarlo
    # nunca.
    #
    # La unica ranura por ahora es la factura comercial, y no es un capricho de
    # clasificacion: es una de las dos condiciones para poder mandar un
    # pedimento a revision. Sin ella el cliente no tiene contra que comparar el
    # pedimento, asi que el sistema tiene que poder contestar "esta cargada o
    # no" sin que nadie lo mire a ojo.
    RANURA_FACTURA_COMERCIAL = 'FACTURA_COMERCIAL'
    RANURA_CHOICES = [
        ('',                        _('No particular role')),
        (RANURA_FACTURA_COMERCIAL,  _('Commercial invoice')),
    ]
    ranura = models.CharField(max_length=30, blank=True, default='',
                              choices=RANURA_CHOICES,
                              verbose_name='Papel dentro del expediente')

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
    KIND_CHOICES = [('OPERATION', _('Operation')), ('DOCUMENT', _('Document'))]

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

    # A donde contesta un cliente que recibe un aviso de esta empresa.
    #
    # Los correos salen del dominio de la plataforma -- es el único verificado --
    # así que sin esto una respuesta llegaría a un buzón que nadie lee. El
    # Reply-To es lo que hace que el cliente le escriba a su proveedor y no al
    # vacío, y no cuesta ningún trámite de DNS: es una cabecera, no un dominio.
    #
    # No es el de facturación: aquel lo lee contabilidad y es de la plataforma
    # hacia la empresa; este lo lee quien atiende a los clientes de la empresa.
    reply_to_email = models.EmailField(
        blank=True, null=True, verbose_name="Correo de respuesta",
        help_text="A donde contestan los clientes de esta empresa. "
                  "Vacio = las respuestas se pierden.")

    # Como se nombra la empresa en los papeles que lee el cliente: en la hoja
    # de impuestos el status se escribe "ELAB PED · DYSER". Recortar la razon
    # social no sirve -- "DYSER Group LLC" daria "DYSER GROUP" -- porque lo que
    # se dicta por telefono es una palabra elegida, no un recorte.
    short_name = models.CharField(max_length=20, blank=True, default='',
                                  verbose_name="Nombre corto",
                                  help_text="Como aparece en la hoja de impuestos. "
                                            "Vacio = se recorta el nombre.")

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

    # Las formas juridicas no distinguen a ninguna empresa: todas son LLC o
    # SA de CV. Fuera de las siglas.
    FORMAS_JURIDICAS = {'LLC', 'INC', 'LTD', 'CORP', 'CO', 'SA', 'DE', 'CV',
                        'RL', 'SAPI', 'SC', 'S', 'A', 'C', 'V'}

    @property
    def sigla(self):
        """
        Como se nombra la empresa donde no cabe la razon social: la barra de
        arriba del telefono, donde el nombre entero empujaba la salida fuera de
        la pantalla.

        Manda el nombre corto, que es una palabra elegida. Sin el, las
        iniciales de la razon social: "Logistics Laredo LLC" da "LL".
        """
        if (self.short_name or '').strip():
            return self.short_name.strip().upper()
        # Sin puntos: "S.A.P.I." es una forma juridica y no cuatro palabras.
        palabras = [p for p in re.split(r'[\s,]+', (self.name or '').upper().replace('.', ''))
                    if p and p not in self.FORMAS_JURIDICAS]
        return ''.join(p[0] for p in palabras[:4]) or (self.name or '')[:6].upper()

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
    # Las etiquetas se escriben en ingles y el espanol es su traduccion, como el
    # resto: esta bitacora se lee en la pantalla de plataforma, que tiene su
    # propio selector de idioma. El valor guardado no cambia.
    EVENT_CHOICES = [
        ('OPERATION_CREATED', _('Operation registered')),
        ('GOODS_RELEASED',    _('Goods released')),
        ('DOCUMENTS_ADDED',   _('Documents added')),
        ('MANUAL',            _('Manual send')),
        # Este no sale de una operacion sino de la plataforma: es la factura
        # que se le manda a la empresa. Va en la misma bitacora porque la
        # pregunta que se responde es la misma -- "¿le llego o no?".
        ('INVOICE_SENT',      _('Invoice sent')),
        # El aviso de que hay un mensaje nuevo en el hilo de una operacion.
        ('CHAT_MESSAGE',      _('Message in the thread')),
    ]
    STATUS_CHOICES = [
        ('SENT',    _('Sent')),
        ('FAILED',  _('Failed')),
        ('SKIPPED', _('Skipped')),
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


# ═══════════════════════════════════════════════════════════════════════════
#  EL PEDIMENTO
# ═══════════════════════════════════════════════════════════════════════════
#
# Hasta ahora el pedimento era un campo de texto de la operacion, y eso no
# alcanza para representar como se agrupa de verdad. El ejemplo que lo decide:
#
#     ED260901-0001 -- llega por XPO, dos pallets: uno del proveedor ABC LLC
#                      y otro de 123 LLC.
#     ED260901-0002 -- llega por ABF, un pallet de ABC LLC.
#
# El cliente quiere juntar en un pedimento lo de ABC LLC:
#
#     Pedimento 1515-6005000 = 1 pallet de la 0001 + el pallet entero de la 0002
#     Pedimento 1515-6005001 = el pallet que sobra de la 0001
#
# Lo que se asigna a un pedimento no es la operacion: son **bultos**. Una
# operacion se parte entre dos pedimentos y dos operaciones caen en uno. Por
# eso el pedimento es una entidad con un reparto, y por eso el campo
# `pedimento` de la operacion se queda como esta: sirve para las operaciones
# sueltas y para no romper nada de lo que ya funciona.
#
# Y el pedimento **no** cuelga de la tarea de cruce, aunque la tarea sea donde
# mas se mire. Un pedimento se manda a revision con solo la llegada y la
# factura comercial, sin que exista ninguna tarea programada. La regla, escrita
# entera: todo nace del embarque. Llega la mercancia, se captura la entrada,
# entra sola al reporte de impuestos, se pide la factura, se elabora su
# pedimento, se manda a revision, el cliente aprueba. La tarea de cruce aparece
# despues y hace otra cosa: juntar lo que ya esta listo y meterlo en un camion
# un dia concreto.


class Pedimento(models.Model):
    """
    Un pedimento en preparacion, con los bultos que van dentro.

    Cuelga del cliente y no de la tarea de cruce, por lo dicho arriba. Nace sin
    numero -- primero se agrupa la mercancia y despues el agente aduanal da el
    numero -- y por eso se llama "Pedimento 1" hasta que lo tiene.
    """

    # El camino de un pedimento, con dueno en cada tramo. `CORRECCIONES` no es
    # un fracaso ni un paso atras: es el cliente haciendo su trabajo, y por eso
    # vuelve a BORRADOR en cuanto se corrige, sin dejar marca en ningun sitio.
    BORRADOR     = 'BORRADOR'
    EN_REVISION  = 'EN_REVISION'
    CORRECCIONES = 'CORRECCIONES'
    APROBADO     = 'APROBADO'
    VALIDADO     = 'VALIDADO'
    PAGADO       = 'PAGADO'
    ESTADOS = [
        (BORRADOR,     _('Draft')),
        (EN_REVISION,  _('Under customer review')),
        (CORRECCIONES, _('Corrections requested')),
        (APROBADO,     _('Approved by the customer')),
        (VALIDADO,     _('Validated')),
        (PAGADO,       _('Paid')),
    ]

    tenant   = models.ForeignKey('Tenant', on_delete=models.CASCADE,
                                 related_name='pedimentos')
    customer = models.ForeignKey(Catalog, on_delete=models.PROTECT,
                                 related_name='pedimentos',
                                 limit_choices_to={'category': 'CUSTOMER'},
                                 verbose_name='Cliente')

    # Como se le llama mientras no tiene numero. Se asigna al crearlo contando
    # los que ya tiene el cliente y no vuelve a cambiar: un nombre que se mueve
    # al borrar otro pedimento no sirve para hablar por telefono, que es para
    # lo que existe.
    orden = models.PositiveIntegerField(default=1, verbose_name='Numero provisional')

    # Las tres casillas del Anexo 22. Mismo criterio que en la operacion: se
    # guardan separadas porque de ahi salen la aduana y la patente, que son las
    # que sostienen el candado.
    ped_aduana      = models.CharField(max_length=2, blank=True, default='',
                                       verbose_name='Aduana de despacho')
    ped_patente     = models.CharField(max_length=4, blank=True, default='',
                                       verbose_name='Patente del agente aduanal')
    ped_consecutivo = models.CharField(max_length=7, blank=True, default='',
                                       verbose_name='Ano y consecutivo')
    # Los dos digitos que pone el sistema, no quien teclea. Se sellan al validar
    # -- antes de eso el ano de validacion todavia no ha ocurrido.
    anio_validacion = models.CharField(max_length=2, blank=True, default='',
                                       verbose_name='Ano de validacion')

    estado = models.CharField(max_length=15, choices=ESTADOS, default=BORRADOR)

    # Las fechas de cada tramo. Se guardan sueltas y no en una bitacora porque
    # lo que se necesita de ellas es responder "cuanto lleva parado aqui", y
    # eso se contesta con la fecha del tramo en el que esta.
    enviado_a_revision_en = models.DateTimeField(null=True, blank=True)
    aprobado_en           = models.DateTimeField(null=True, blank=True)
    validado_en           = models.DateTimeField(null=True, blank=True)
    pagado_en             = models.DateTimeField(null=True, blank=True)

    # Lo que el cliente escribio al pedir correcciones. Se guarda el ultimo y
    # no el historial: quien lo lee esta arreglando el pedimento de ahora.
    correcciones_pedidas = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='pedimentos_creados')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Pedimento'
        verbose_name_plural = 'Pedimentos'

    def __str__(self):
        return self.etiqueta

    # -- Como se lee y como se llama -----------------------------------------

    @property
    def numero(self):
        """El numero escrito, `24-1780-6003555`, o cadena vacia si aun no tiene."""
        from . import pedimentos as ped
        return ped.numero_corrido(self.ped_aduana, self.ped_patente,
                                  self.ped_consecutivo)

    @property
    def numero_completo(self):
        """Los quince digitos por grupos, `26 24 1780 6003555`."""
        from . import pedimentos as ped
        return ped.numero_completo(self.ped_aduana, self.ped_patente,
                                   self.ped_consecutivo, self.anio_validacion)

    @property
    def tiene_numero(self):
        return bool(self.numero)

    @property
    def se_puede_armar(self):
        """
        Si todavia se le pueden mover los bultos, el numero y los archivos.

        Deja de poderse en cuanto sale a revision. Lo que el cliente tiene
        delante no puede cambiar por debajo mientras lo mira: aprobaria un
        pedimento y quedaria aprobado otro. Cuando el cliente pide correcciones
        vuelve a abrirse, que es justo para lo que pidio las correcciones.
        """
        return self.estado in (self.BORRADOR, self.CORRECCIONES)

    @property
    def etiqueta(self):
        """Como se le nombra en pantalla: su numero si lo tiene, si no su orden."""
        return self.numero or f'Pedimento {self.orden}'

    @property
    def aduana(self):
        return self.ped_aduana or ''

    @property
    def patente(self):
        return self.ped_patente or ''

    # -- El reparto ----------------------------------------------------------

    @property
    def total_bultos(self):
        """Cuantos bultos van dentro, sumando todos los renglones del reparto."""
        return sum(r.bultos for r in self.renglones.all())

    @property
    def total_libras(self):
        """Las libras que van dentro, sumando los renglones que las tengan."""
        pesos = [r.weight_lbs for r in self.renglones.all() if r.weight_lbs]
        return sum(pesos) if pesos else None

    @property
    def total_kilos(self):
        """Los kilos, que salen de las libras y nunca se teclean."""
        pesos = [r.weight_kgs for r in self.renglones.all() if r.weight_kgs]
        return sum(pesos) if pesos else None

    @property
    def necesita_fotos_de_series(self):
        """
        Si a este pedimento hay que subirle fotos de numeros de serie.

        No se decide a mano ni se marca como "no aplica" cada vez: se hereda de
        los bultos. Si alguna de las operaciones que van dentro trae numeros de
        serie, la ranura aparece y es obligatoria; si ninguna, no aparece. Asi
        la comprobacion no depende de que alguien se acuerde.
        """
        return any(r.operation.has_serial_numbers
                   for r in self.renglones.select_related('operation'))

    # ── Enviar a revision ────────────────────────────────────────────────────
    #
    # Que el pedimento no dependa de la tarea de cruce no quiere decir que se
    # pueda mandar a revision en cualquier momento. Una revision es el cliente
    # comparando el pedimento contra su factura: sin la factura no tiene contra
    # que compararlo, y sin numero no esta revisando un pedimento sino un
    # borrador. Mandarle algo incompleto gasta el unico momento de atencion que
    # da, y la segunda vez ya no lo mira igual.
    #
    # Por eso lo que hay aqui no es un booleano sino una lista de lo que falta:
    # en la pantalla el boton se ve atenuado **con el motivo escrito al lado**,
    # y no encendido dando error al pulsarlo.

    def documentos_por_ranura(self):
        """Los documentos que hay, agrupados por ranura."""
        cajones = {}
        for doc in self.documentos.all():
            cajones.setdefault(doc.ranura, []).append(doc)
        return cajones

    @property
    def operaciones_sin_factura(self):
        """
        Los embarques de este pedimento a los que les falta la factura comercial.

        Se mira el expediente y no el campo de texto `invoice`: lo que hace
        falta es el archivo cargado, porque es lo que el cliente compara. Tener
        apuntado el numero de factura no es tenerla.
        """
        faltan = []
        for renglon in self.renglones.select_related('operation'):
            op = renglon.operation
            tiene = op.documents.filter(
                ranura=OperationDocument.RANURA_FACTURA_COMERCIAL).exists()
            if not tiene:
                faltan.append(op)
        return faltan

    @property
    def faltantes_para_revision(self):
        """
        Lo que impide mandar este pedimento a revision, escrito para la pantalla.

        Lista vacia significa que el boton se enciende. El orden es el de la
        importancia: primero lo que no se puede suplir con nada, despues las
        ranuras del expediente.
        """
        faltan = []
        if not self.renglones.exists():
            faltan.append(_('No goods assigned yet'))
        if not self.tiene_numero:
            faltan.append(_('The pedimento number'))

        # La factura comercial **no** esta en esta lista, y no es un olvido.
        # Pedirla aqui era pedir dos pruebas del mismo hecho: el COVE, que si
        # se exige, es la transmision del valor de esa factura a la Ventanilla
        # Unica y no se puede generar sin tenerla. El archivo se sigue pidiendo
        # para el expediente -- lo enseña `operaciones_sin_factura`, que la
        # pantalla usa para avisar --, pero ya no frena la revision.

        cajones = self.documentos_por_ranura()
        etiquetas = dict(PedimentoDocument.RANURAS)
        for ranura in PedimentoDocument.RANURAS_PARA_REVISION:
            if not cajones.get(ranura):
                faltan.append(etiquetas[ranura])
        # Las fotos de series solo se exigen cuando la mercancia las trae. La
        # ranura no esta y no se pide: no es una casilla de "no aplica" que
        # alguien marque cada vez, es que no aparece.
        if self.necesita_fotos_de_series and not cajones.get(
                PedimentoDocument.FOTOS_SERIES):
            faltan.append(etiquetas[PedimentoDocument.FOTOS_SERIES])
        return faltan

    @property
    def puede_enviarse_a_revision(self):
        return self.estado in (self.BORRADOR, self.CORRECCIONES) \
               and not self.faltantes_para_revision

    def enviar_a_revision(self):
        """Marca el pedimento como enviado. Devuelve si se pudo."""
        if not self.puede_enviarse_a_revision:
            return False
        self.estado = self.EN_REVISION
        self.enviado_a_revision_en = timezone.now()
        self.correcciones_pedidas = ''
        self.save(update_fields=['estado', 'enviado_a_revision_en',
                                 'correcciones_pedidas', 'updated_at'])
        return True

    # -- El candado ----------------------------------------------------------

    def choque_con(self, otros):
        """
        Si este pedimento no puede ir con `otros` -- y por que.

        `otros` es un iterable de pedimentos o de numeros. Devuelve `None` si
        cabe. El razonamiento vive en `warehouse.pedimentos.choque`; aqui solo
        se traducen los objetos a numeros.
        """
        from . import pedimentos as ped
        numeros = [o.numero if hasattr(o, 'numero') else str(o or '')
                   for o in otros if o is not None]
        numeros = [n for n in numeros if n and n != self.numero]
        return ped.choque(self.numero, numeros)


class PedimentoBundle(models.Model):
    """
    Un renglon del reparto: tantos bultos de tal operacion van a tal pedimento.

    Es el modelo entero de la agrupacion. `bultos` es una cantidad y no una
    lista de bultos concretos porque en bodega no se numeran uno a uno: se
    cuentan. Lo que hace falta saber es cuantos de esta operacion se fueron a
    este pedimento, y eso es un numero.
    """
    pedimento = models.ForeignKey(Pedimento, on_delete=models.CASCADE,
                                  related_name='renglones')
    operation = models.ForeignKey(WarehouseOperation, on_delete=models.CASCADE,
                                  related_name='renglones_de_pedimento')
    bultos    = models.PositiveIntegerField(
                    default=1, verbose_name='Bultos que van a este pedimento')

    # El peso de estos bultos, no el de la operacion entera. Cuando una entrada
    # se parte entre dos pedimentos, el peso tambien se parte, y no por partes
    # iguales: los bultos de un embarque no pesan lo mismo. Asi que se teclea.
    #
    # No se sugiere ni se calcula. Un peso propuesto que nadie comprueba es
    # peor que una casilla vacia: la casilla vacia se ve, y el numero inventado
    # se firma. Se teclea en libras porque asi llegan los embarques, y los
    # kilos -- que son con los que se trabaja de este lado -- los pone el
    # sistema, para que nadie convierta a mano.
    weight_lbs = models.DecimalField(max_digits=10, decimal_places=2,
                                     null=True, blank=True,
                                     verbose_name='Libras de estos bultos')
    weight_kgs = models.DecimalField(max_digits=10, decimal_places=2,
                                     null=True, blank=True,
                                     verbose_name='Kilos de estos bultos')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['operation__custom_id']
        verbose_name = 'Reparto de bultos'
        verbose_name_plural = 'Reparto de bultos'
        # Una operacion aparece una sola vez en cada pedimento. Si van seis
        # bultos, van en un renglon de seis y no en seis renglones de uno: dos
        # renglones de la misma operacion en el mismo pedimento no significan
        # nada distinto y solo hacen que las sumas dependan de como se capturo.
        unique_together = [('pedimento', 'operation')]

    # Una libra son 0.45359237 kilos exactos. La constante vive aqui y no en la
    # vista para que la conversion sea la misma se guarde desde donde se guarde.
    KILOS_POR_LIBRA = Decimal('0.45359237')

    def save(self, *args, **kwargs):
        # Los kilos salen de las libras siempre que haya libras. Si alguien
        # borra las libras, los kilos se van con ellas: un peso en kilos sin su
        # original no se puede comprobar el dia que no cuadre.
        if self.weight_lbs is not None:
            self.weight_kgs = (self.weight_lbs * self.KILOS_POR_LIBRA
                               ).quantize(Decimal('0.01'))
        else:
            self.weight_kgs = None
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.bultos} de {self.operation.custom_id} a {self.pedimento.etiqueta}'


class PedimentoDocument(models.Model):
    """
    Un archivo en una de las ranuras del pedimento.

    Las ranuras no son categorias sueltas: son la lista de lo que el cliente
    revisa. Por eso el pedimento sabe decir que le falta sin que nadie lo mire
    a ojo, y por eso el boton de enviar a revision se enciende solo en cuanto
    entra lo ultimo.

    Un renglon guarda **o** un archivo propio **o** un documento del expediente
    de una operacion, nunca los dos. La segunda forma existe por las fotos de
    numeros de serie: se toman al recibir la mercancia y ya viven en el
    expediente, asi que la ranura las **elige**, no pide subirlas otra vez.
    Subirlas dos veces crearia dos verdades sobre la misma caja.
    """

    # Las seis que se mandan al cliente.
    M3           = 'M3'
    PROFORMA     = 'PROFORMA'
    COVE         = 'COVE'
    CARTA_318    = 'CARTA_318'
    FOTOS_SERIES = 'FOTOS_SERIES'
    OTROS        = 'OTROS'
    # Las cuatro que llegan despues, ya con el pedimento fuera.
    MANIFESTACION    = 'MANIFESTACION'
    ACUSE            = 'ACUSE'
    PEDIMENTO_PAGADO = 'PEDIMENTO_PAGADO'
    SHIPPER          = 'SHIPPER'

    RANURAS = [
        (M3,           _('M3 file')),
        (PROFORMA,     _('Pedimento draft')),
        (COVE,         _('COVE')),
        (CARTA_318,    _('318 letter')),
        (FOTOS_SERIES, _('Serial-number photos')),
        (OTROS,        _('Other')),
        (MANIFESTACION,    _('Statement of value')),
        (ACUSE,            _('Statement of value receipt')),
        (PEDIMENTO_PAGADO, _('Paid pedimento')),
        (SHIPPER,          _('Shipper')),
    ]

    # Las que hacen falta para poder mandar a revision. La de fotos de series
    # solo cuenta cuando la mercancia trae ese dato -- lo decide el propio
    # pedimento a partir de sus bultos, no una marca de "no aplica" que alguien
    # pone cada vez.
    RANURAS_PARA_REVISION = (PROFORMA, COVE, CARTA_318)

    # Las que se mandan, en el orden en que se enseñan.
    RANURAS_DE_ENVIO = (M3, PROFORMA, COVE, CARTA_318, FOTOS_SERIES, OTROS)
    # Las que llegan despues.
    RANURAS_POSTERIORES = (MANIFESTACION, ACUSE, PEDIMENTO_PAGADO, SHIPPER)

    pedimento = models.ForeignKey('Pedimento', on_delete=models.CASCADE,
                                  related_name='documentos')
    ranura    = models.CharField(max_length=20, choices=RANURAS)

    # Un archivo propio de esta ranura. Vacio cuando el renglon apunta al
    # expediente de una operacion.
    file          = models.FileField(upload_to=ruta_documento, max_length=255,
                                     blank=True, null=True)
    original_name = models.CharField(max_length=255, blank=True)

    # O un documento que ya vive en el expediente de la operacion. Se usa para
    # las fotos de numeros de serie, que se tomaron al recibir.
    documento_de_operacion = models.ForeignKey(
        OperationDocument, on_delete=models.CASCADE, null=True, blank=True,
        related_name='usos_en_pedimentos')

    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name='documentos_de_pedimento')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['ranura', 'uploaded_at']
        verbose_name = 'Documento del pedimento'
        verbose_name_plural = 'Documentos del pedimento'

    def __str__(self):
        return f'{self.get_ranura_display()} de {self.pedimento.etiqueta}'

    @property
    def nombre(self):
        """Como se llama este archivo en pantalla."""
        if self.documento_de_operacion_id:
            doc = self.documento_de_operacion
            return doc.original_name or os.path.basename(doc.file.name or '')
        return self.original_name or os.path.basename(self.file.name or '')

    @property
    def archivo(self):
        """El `FieldFile` real, venga de esta ranura o del expediente."""
        if self.documento_de_operacion_id:
            return self.documento_de_operacion.file
        return self.file


class ParametrosDeImpuestos(models.Model):
    """
    Los cuatro numeros del calculo que no salen de ninguna formula.

    El tipo de cambio de trabajo, la cuota de prevalidacion y las dos tasas del
    DTA. Los pone la casa y cambian con el tiempo, asi que viven juntos en una
    pantalla de ajustes y no escondidos dentro de cada hoja de impuestos.

    Y se guardan con **la fecha desde la que valen**, no como un campo que se
    pisa. La prevalidacion es el ejemplo claro: se cambia una vez al ano, sale
    de la regla general de comercio exterior 1.8.3, y un calculo de marzo tiene
    que seguir enseñando la cuota de marzo aunque en abril sea otra. Sin
    historico, cambiar la cuota reescribiria hacia atras todos los estimados ya
    dados a los clientes.
    """

    tenant        = models.ForeignKey('Tenant', on_delete=models.CASCADE,
                                      related_name='parametros_de_impuestos')
    vigente_desde = models.DateField(default=timezone.localdate,
                                     verbose_name='Vigente desde')

    # Alto a proposito, y nunca del DOF. Es una decision de negocio: mas vale
    # que sobre dinero en la cuenta a que falte y se pierda el dia de cruce.
    # El del DOF se enseña al lado, en gris, solo para saber de cuanto es el
    # colchon -- y eso lo ven el tenant y el agente aduanal, nunca el cliente.
    tipo_de_cambio = models.DecimalField(
        max_digits=10, decimal_places=4, default=Decimal('19.0000'),
        verbose_name='Tipo de cambio de trabajo')

    # Lo cobra la empresa que hace la prevalidacion. Es un cobro, no una
    # formula, asi que no se calcula.
    prevalidacion = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('300.00'),
        verbose_name='Cuota de prevalidacion (MXN)')

    # Ocho al millar sobre el valor en aduana.
    dta_sin_tmec = models.DecimalField(
        max_digits=8, decimal_places=6, default=Decimal('0.008'),
        verbose_name='Tasa del DTA sin T-MEC')

    # Cero: con T-MEC no se paga derecho de tramite. Va como parametro y no
    # clavado en el codigo por mantenimiento -- si algun ano pasa a ser una
    # cuota fija, se cambia aqui y no hay que tocar nada.
    dta_con_tmec = models.DecimalField(
        max_digits=8, decimal_places=6, default=Decimal('0'),
        verbose_name='Tasa del DTA con T-MEC')

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='parametros_creados')

    class Meta:
        ordering = ['-vigente_desde', '-created_at']
        verbose_name = 'Parametros de impuestos'
        verbose_name_plural = 'Parametros de impuestos'

    def __str__(self):
        return f'{self.tenant} desde {self.vigente_desde}'

    @classmethod
    def vigentes(cls, tenant, fecha=None):
        """
        Los parametros que valian en `fecha` -- hoy si no se dice otra cosa.

        Si el tenant no ha tocado nunca sus ajustes devuelve una fila sin
        guardar, con los valores por omision. Asi el calculo funciona desde el
        primer dia sin obligar a nadie a pasar por una pantalla de ajustes
        antes de poder dar un estimado.
        """
        fecha = fecha or timezone.localdate()
        fila = (cls.objects.filter(tenant=tenant, vigente_desde__lte=fecha)
                .order_by('-vigente_desde', '-created_at').first())
        return fila or cls(tenant=tenant, vigente_desde=fecha)


class RenglonDeImpuestos(models.Model):
    """
    Un embarque en la hoja de impuestos del cliente.

    La hoja no se abre "al llegar a un paso": esta siempre, una por cliente,
    viva desde que le llega el primer embarque hasta que cruza el ultimo. Se
    mira tres veces al dia y siempre por lo mismo -- despues de capturar una
    entrada, cuando llega una factura, y cuando el cliente llama preguntando
    cuanto va a pagar, que es la pregunta que mas veces se contesta.

    El renglon existe aparte de la operacion porque a veces nace antes que
    ella: la factura puede llegar antes que la mercancia, y entonces hay algo
    que reportar -- un pedido, un valor, un ETA -- sin que exista todavia
    ninguna entrada. Cuando la mercancia llega, el renglon se enlaza con su
    operacion y deja de estar suelto.

    De toda la tabla, la unica columna que se teclea es la del valor. El
    status, las fechas, la entrada, el pedido y el pedimento ya estan en el
    sistema por haber hecho el trabajo.
    """

    # Los seis status. Ninguno se teclea: todos salen de lo que ya paso. Y los
    # seis contestan la misma pregunta -- de quien es la pelota -- dicha con
    # las palabras del papel que el cliente ya lee.
    NO_HA_LLEGADO = 'NO_HA_LLEGADO'
    ETA           = 'ETA'
    ELAB_PED      = 'ELAB_PED'
    EN_REVISION   = 'EN_REVISION'
    LISTO_PAGO    = 'LISTO_PAGO'
    PAGADO        = 'PAGADO'

    tenant   = models.ForeignKey('Tenant', on_delete=models.CASCADE,
                                 related_name='renglones_de_impuestos')
    customer = models.ForeignKey(Catalog, on_delete=models.PROTECT,
                                 related_name='renglones_de_impuestos',
                                 limit_choices_to={'category': 'CUSTOMER'},
                                 verbose_name='Cliente')

    # El embarque, cuando ya llego. Vacio mientras la factura va por delante de
    # la mercancia.
    operation = models.OneToOneField(WarehouseOperation, on_delete=models.CASCADE,
                                     null=True, blank=True,
                                     related_name='renglon_de_impuestos')

    # Con lo que el cliente identifica su embarque. Cuando hay operacion se lee
    # de ella; esto es para el renglon que todavia no la tiene.
    po_order = models.CharField(max_length=200, blank=True, default='',
                                verbose_name='Pedido')
    carrier  = models.ForeignKey(Catalog, on_delete=models.SET_NULL, null=True,
                                 blank=True, related_name='renglones_como_carrier',
                                 limit_choices_to={'category': 'CARRIER'})
    guia     = models.CharField(max_length=200, blank=True, default='')
    eta      = models.DateField(null=True, blank=True)

    # ── El valor ─────────────────────────────────────────────────────────────
    # Se guarda lo que se tecleo y no solo el resultado. Un embarque puede
    # traer dos o tres facturas, y quien vuelva a este renglon en octubre tiene
    # que ver `500+700` y no un `1,200` huerfano del que ya nadie se acuerda de
    # donde salio.
    valor_expresion  = models.CharField(max_length=60, blank=True, default='',
                                        verbose_name='Valor tal como se tecleo')
    valor_mercancia  = models.DecimalField(max_digits=14, decimal_places=2,
                                           null=True, blank=True,
                                           verbose_name='Valor de mercancia USD')
    fletes           = models.DecimalField(max_digits=14, decimal_places=2,
                                           null=True, blank=True)
    incrementables   = models.DecimalField(max_digits=14, decimal_places=2,
                                           null=True, blank=True)
    tasa_igi         = models.DecimalField(max_digits=6, decimal_places=4,
                                           default=Decimal('0'),
                                           verbose_name='Tasa de IGI')
    aplica_tmec      = models.BooleanField(default=False,
                                           verbose_name='Aplica el T-MEC')

    # Lo que se le mando al cliente, que puede no ser la cifra exacta: el
    # redondeo es de la casa, siempre hacia arriba y a numero cerrado. El
    # sistema calcula y propone; nunca decide ni redondea por su cuenta.
    impuesto_reportado = models.DecimalField(max_digits=14, decimal_places=2,
                                             null=True, blank=True,
                                             verbose_name='Impuesto reportado MXN')

    # Del valor anterior, cuando se corrige al llegar la factura: en tres meses
    # dice cuanto se despegan las proformas de cada proveedor.
    valor_anterior = models.DecimalField(max_digits=14, decimal_places=2,
                                         null=True, blank=True)

    notas       = models.TextField(blank=True, default='')

    # ── Lo que solo se teclea en una linea a mano ────────────────────────────
    # Valen mientras el renglon no tenga operacion. Cuando la mercancia llega y
    # se enlaza con su entrada, el sistema vuelve a mandar: a partir de ahi hay
    # de donde leer el status, la factura y el pedimento.
    status_manual    = models.CharField(max_length=60, blank=True, default='',
                                        verbose_name='Status tecleado')
    factura_manual   = models.BooleanField(null=True, blank=True,
                                           verbose_name='Factura, tecleada')
    pedimento_manual = models.CharField(max_length=30, blank=True, default='',
                                        verbose_name='Pedimento tecleado')

    # Quitar un renglon de la hoja no borra nada. El embarque sigue en bodega,
    # en operaciones y en su pedimento; lo unico que deja de pasar es que
    # aparezca en la hoja del cliente. Se hace asi porque la hoja la arma
    # tambien el cliente, y equivocarse al ordenarla no puede costar una
    # entrada capturada. Volver a ponerlo es un clic.
    en_la_hoja  = models.BooleanField(default=True,
                                      verbose_name='Aparece en la hoja')
    quitado_en  = models.DateTimeField(null=True, blank=True)
    quitado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name='renglones_quitados')

    # Cuando salio solo, por haber cruzado y estar pagado. Va aparte de
    # `quitado_por` para que la pantalla de retirados pueda decir por que se
    # fue, en vez de enseñar un hueco donde deberia ir un nombre.
    cerrado_en  = models.DateTimeField(null=True, blank=True,
                                       verbose_name='Cerrado por el sistema')

    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)
    updated_by  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name='renglones_tocados')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Renglon de la hoja de impuestos'
        verbose_name_plural = 'Renglones de la hoja de impuestos'

    def __str__(self):
        return f'{self.pedido or "sin pedido"} de {self.customer}'

    # ── Lo que se lee del embarque cuando ya existe ──────────────────────────

    @property
    def pedido(self):
        """El numero de pedido, que es con lo que el cliente lo identifica."""
        if self.operation and (self.operation.po_order or '').strip():
            return self.operation.po_order.strip()
        return (self.po_order or '').strip()

    @property
    def entrada(self):
        return self.operation.custom_id if self.operation else ''

    @property
    def transportista(self):
        if self.operation:
            return self.operation.get_carrier_display()
        return self.carrier.name if self.carrier else ''

    @property
    def guia_de_embarque(self):
        if self.operation and (self.operation.pro or '').strip():
            return self.operation.pro.strip()
        return (self.guia or '').strip()

    @property
    def fecha_estimada(self):
        if self.operation and self.operation.eta:
            return self.operation.eta
        return self.eta

    @property
    def fecha_reportado(self):
        """El dia que se capturo la entrada. Antes se tecleaba."""
        return self.operation.date if self.operation else None

    @property
    def fecha_enviado_a_rev_y_mv(self):
        """
        El dia que el pedimento salio a revision del cliente.

        Es la columna que Diego teclea hoy en su Excel, y sale sola: la escribe
        el boton de "enviar a revision" del pedimento, no quien arma la hoja.
        """
        ped = self.pedimento
        return ped.enviado_a_revision_en if ped else None

    @property
    def pedimento(self):
        """El pedimento en el que cayeron los bultos de este embarque."""
        if not self.operation:
            return None
        renglon = self.operation.renglones_de_pedimento.select_related(
            'pedimento').first()
        return renglon.pedimento if renglon else None

    @property
    def pedimento_texto(self):
        """
        El numero de pedimento como sale en la hoja.

        Del pedimento de verdad cuando lo hay, y del que se tecleo cuando la
        linea es a mano. Existe para que la plantilla no tenga que preguntar
        dos veces ni saber cual de los dos mira.
        """
        ped = self.pedimento
        if ped is not None:
            return ped.ped_consecutivo or ped.etiqueta
        return (self.pedimento_manual or '').strip()

    # ── La factura, que decide si el valor es firme ──────────────────────────

    @property
    def factura_comercial(self):
        """El archivo de la factura, si esta cargado."""
        if not self.operation:
            return None
        return self.operation.documents.filter(
            ranura=OperationDocument.RANURA_FACTURA_COMERCIAL).first()

    @property
    def tiene_factura(self):
        """
        Si el cliente ya mando la factura de este embarque.

        Manda el archivo cuando esta: un documento cargado no admite discusion.
        Cuando no esta, vale lo que se marco al capturar -- el check de «ya
        tenemos la factura» --, porque el papel puede estar en la mano de quien
        captura sin que nadie lo haya escaneado todavia.

        Esto contesta «la tenemos», no «esta en el expediente». Las dos
        preguntas son distintas y ahora se responden por separado: para el
        expediente esta `operaciones_sin_factura`, que mira solo el archivo.
        """
        if self.factura_comercial is not None:
            return True
        if self.factura_manual is not None:
            return self.factura_manual
        return False

    @property
    def estado_del_valor(self):
        """
        `SIN_NADA`, `PROFORMA` o `FACTURA`.

        No se adivina: lo decide el archivo. Mientras el renglon no tenga
        colgada una factura comercial el valor es provisional venga de donde
        venga, y en el momento en que se carga el documento deja de serlo. Sin
        clic de confirmacion.
        """
        if self.tiene_factura:
            return 'FACTURA'
        if self.valor_mercancia:
            return 'PROFORMA'
        return 'SIN_NADA'

    @property
    def valor_es_provisional(self):
        return self.estado_del_valor == 'PROFORMA'

    # ── El status, que se escribe solo ───────────────────────────────────────

    @property
    def status(self):
        """La clave del status, deducida de lo que ya paso en el sistema."""
        ped = self.pedimento
        if ped is not None:
            if ped.estado in (Pedimento.VALIDADO, Pedimento.PAGADO):
                return self.PAGADO
            if ped.estado == Pedimento.APROBADO:
                return self.LISTO_PAGO
            if ped.estado == Pedimento.EN_REVISION:
                return self.EN_REVISION
        if self.operation is not None:
            # Llego la mercancia y se esta elaborando el pedimento. Es el hueco
            # largo, y el momento en que mas falta hace reclamar la factura:
            # sin este status esos dias se verian como "no ha llegado", que es
            # falso.
            return self.ELAB_PED
        if self.fecha_estimada and self.transportista and self.guia_de_embarque:
            return self.ETA
        return self.NO_HA_LLEGADO

    @property
    def status_texto(self):
        """
        El status como se escribe en la hoja que ve el cliente.

        Las abreviaciones no se teclean: la del cliente sale del catalogo y la
        del tenant de su nombre corto.
        """
        from .utils import nombre_corto

        # Una linea a mano puede traer el status escrito -- 'NO HA LLEGADO',
        # 'ETA 15/09', lo que haga falta --, porque no hay nada en el sistema
        # de donde deducirlo. Se respeta tal cual y sin abreviacion pegada: lo
        # que se tecleo es lo que se quiso decir.
        if self.operation_id is None and (self.status_manual or '').strip():
            return self.status_manual.strip()

        clave = self.status

        # Las dos abreviaciones que van al final del status. Ninguna se teclea
        # aqui: la del cliente ya existe en su ficha del catalogo y la del
        # tenant en sus ajustes.
        #
        # Y si alguna falta, no se inventa: el nombre completo recortado a doce
        # letras da cosas como "CUSTOMER TES", que en un papel que el cliente
        # lee parece un error del sistema. Sin abreviacion, el status va solo.
        yo   = (self.tenant.short_name or '').strip().upper()
        if not yo:
            corto = nombre_corto(self.tenant.name).upper()
            yo = corto if len(corto) <= 12 else ''
        suyo = (self.customer.abbreviation or '').strip().upper()

        def con(texto, abrev):
            return f'{texto} \u00b7 {abrev}' if abrev else texto

        if clave == self.NO_HA_LLEGADO:
            return 'NO HA LLEGADO'
        if clave == self.ETA:
            return 'ETA %s' % self.fecha_estimada.strftime('%d/%m')
        if clave == self.ELAB_PED:
            return con('ELAB PED', yo)
        if clave == self.EN_REVISION:
            return con('ENVIADO A REVISION Y MV', suyo)
        if clave == self.LISTO_PAGO:
            return con('LISTO PARA VALIDACION Y PAGO', suyo)
        return 'VALIDADOS Y PAGADOS'

    @property
    def ya_termino(self):
        """
        Si este embarque ya no tiene nada que hacer en la hoja.

        Las dos cosas a la vez: el pedimento pagado y la mercancia cruzada. Con
        una sola no basta -- un pedimento pagado cuya mercancia sigue en bodega
        es justo lo que el cliente quiere ver --, y por eso se comprueban las
        dos.
        """
        if self.status != self.PAGADO or self.operation_id is None:
            return False
        return self.operation.cruces.filter(
            task__estado=CrossingTask.CRUZADA).exists()

    @property
    def la_pelota_es_del_cliente(self):
        """De quien se espera el siguiente movimiento."""
        return self.status == self.EN_REVISION

    # ── La cuenta ────────────────────────────────────────────────────────────

    def calcular(self, parametros=None):
        """
        La hoja de impuestos de este renglon, con los parametros que valian.

        Se usan los del dia en que se reporto -- el dia de la entrada -- y no
        los de hoy: un estimado dado en marzo con la cuota de marzo tiene que
        seguir enseñando esa cuota en agosto.
        """
        from . import impuestos as _impuestos
        if parametros is None:
            parametros = ParametrosDeImpuestos.vigentes(
                self.tenant, self.fecha_reportado)
        return _impuestos.calcular(
            valor_mercancia=self.valor_mercancia or 0,
            fletes=self.fletes or 0,
            incrementables=self.incrementables or 0,
            tipo_de_cambio=parametros.tipo_de_cambio,
            aplica_tmec=self.aplica_tmec,
            tasa_igi=self.tasa_igi or 0,
            prevalidacion=parametros.prevalidacion,
            dta_sin_tmec=parametros.dta_sin_tmec,
            dta_con_tmec=parametros.dta_con_tmec)

    @property
    def impuesto_estimado(self):
        """
        Lo que se le va a decir al cliente, en pesos.

        Es lo reportado si alguien lo escribio -- el redondeo es de la casa --
        y si no la cifra exacta que sale de la cuenta. Sin valor todavia,
        `None`: la columna va vacia y el renglon sale en la hoja del dia
        pidiendo el documento, que es para lo que existe.
        """
        if self.impuesto_reportado is not None:
            return self.impuesto_reportado
        if not self.valor_mercancia:
            return None
        return self.calcular()['T_total_pedimento']


# ═══════════════════════════════════════════════════════════════════════════
#  LA TAREA DE CRUCE
# ═══════════════════════════════════════════════════════════════════════════
#
# El cliente ve su mercancia guardada, marca lo que quiere cruzar, elige el dia
# y crea la tarea. Es el numero con el que se habla del embarque de ahi en
# adelante.
#
# La tarea **no** es donde nacen los pedimentos ni la estimacion de impuestos:
# esos existen antes y a veces sin ella. La tarea aparece despues y hace otra
# cosa -- juntar lo que ya esta listo y meterlo en un camion un dia concreto --,
# y por eso se puede armar incompleta: con embarques que ya tienen pedimento y
# con embarques que no, y con la factura comercial todavia sin llegar.
#
# El prefijo es `TC` y no `TD` porque `TD` ya esta ocupado: es el trasbordo, uno
# de los cuatro tipos de operacion.


class CrossingTask(models.Model):
    """Un camion, un dia, y la mercancia que va dentro."""

    PREFIJO = 'TC'

    # Quien la creo. Lo correcto es que la cree el cliente, pero no falta quien
    # llama por telefono, asi que crean los dos -- y la tarea lo enseña
    # siempre. El dia que alguien reclame que se pidio cruzar, esa linea es la
    # respuesta.
    LA_CREO_EL_CLIENTE = 'CLIENTE'
    LA_CREO_LA_CASA    = 'TENANT'
    ORIGENES = [
        (LA_CREO_EL_CLIENTE, _('Created by the customer')),
        (LA_CREO_LA_CASA,    _('Created by the warehouse on their behalf')),
    ]

    ABIERTA   = 'ABIERTA'
    CON_ORDEN = 'CON_ORDEN'
    CARGADA   = 'CARGADA'
    CRUZADA   = 'CRUZADA'
    CANCELADA = 'CANCELADA'
    ESTADOS = [
        (ABIERTA,   _('Open')),
        (CON_ORDEN, _('Load order issued')),
        (CARGADA,   _('Loaded')),
        (CRUZADA,   _('Crossed')),
        (CANCELADA, _('Cancelled')),
    ]

    tenant    = models.ForeignKey('Tenant', on_delete=models.CASCADE,
                                  related_name='cruces')
    customer  = models.ForeignKey(Catalog, on_delete=models.PROTECT,
                                  related_name='cruces',
                                  limit_choices_to={'category': 'CUSTOMER'},
                                  verbose_name='Cliente')
    custom_id = models.CharField(max_length=20, unique=True, blank=True)

    # El dia se elige libremente en un calendario, no entre dias fijos, y se
    # puede cambiar: por tipo de cambio, porque quedo mal el transfer, porque
    # no hay sistema en la aduana. Cada cambio queda en `CambioDeDiaDeCruce`.
    fecha_de_cruce = models.DateField(verbose_name='Dia de cruce')

    estado = models.CharField(max_length=12, choices=ESTADOS, default=ABIERTA)
    origen = models.CharField(max_length=10, choices=ORIGENES,
                              default=LA_CREO_EL_CLIENTE)

    # Cuando la crea la casa por telefono, el cliente recibe un aviso de "esto
    # es lo que entendimos, confirmalo". Un toque, no un tramite: es la
    # instruccion telefonica puesta por escrito.
    confirmada_por_el_cliente = models.BooleanField(default=False)
    confirmada_en             = models.DateTimeField(null=True, blank=True)

    notas      = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='cruces_creados')

    class Meta:
        ordering = ['fecha_de_cruce', 'custom_id']
        verbose_name = 'Tarea de cruce'
        verbose_name_plural = 'Tareas de cruce'

    def __str__(self):
        return self.custom_id or f'TC-{self.pk}'

    def generar_custom_id(self):
        fecha = (timezone.localdate(self.created_at) if self.created_at
                 else timezone.localdate())
        return siguiente_consecutivo(CrossingTask, self.PREFIJO, fecha)

    def save(self, *args, **kwargs):
        if not self.custom_id:
            # El consecutivo necesita saber el dia, y el dia lo pone
            # `auto_now_add` al guardar. Asi que se guarda primero sin numero y
            # se numera despues: es feo, y es lo que hace que dos tareas del
            # mismo dia no puedan repetir numero.
            super().save(*args, **kwargs)
            self.custom_id = self.generar_custom_id()
            return super().save(update_fields=['custom_id'])
        return super().save(*args, **kwargs)

    # ── Quien la creo ────────────────────────────────────────────────────────

    @property
    def creada_por(self):
        """La linea que se enseña siempre, en las palabras del diseno."""
        quien = self.created_by.get_full_name() or self.created_by.username \
            if self.created_by else '?'
        if self.origen == self.LA_CREO_EL_CLIENTE:
            return _('created by %(cliente)s') % {'cliente': self.customer.name}
        return _('created by %(quien)s on behalf of %(cliente)s') % {
            'quien': quien, 'cliente': self.customer.name}

    @property
    def espera_confirmacion(self):
        """Si la creo la casa y el cliente todavia no ha dicho que si."""
        return (self.origen == self.LA_CREO_LA_CASA
                and not self.confirmada_por_el_cliente
                and self.estado == self.ABIERTA)

    # ── Lo que lleva dentro ──────────────────────────────────────────────────

    @property
    def operaciones(self):
        return [r.operation for r in
                self.renglones.select_related('operation').all()]

    @property
    def pedimentos(self):
        """Los pedimentos en los que cayeron los bultos de esta tarea."""
        vistos, salida = set(), []
        for renglon in self.renglones.select_related('operation'):
            for enlace in renglon.operation.renglones_de_pedimento.select_related(
                    'pedimento'):
                if enlace.pedimento_id not in vistos:
                    vistos.add(enlace.pedimento_id)
                    salida.append(enlace.pedimento)
        return salida

    @property
    def numeros_de_pedimento(self):
        return [p.numero for p in self.pedimentos if p.numero]

    @property
    def aduana(self):
        """
        La aduana del cruce, leida del primer pedimento que la traiga dentro.

        No se elige en ningun sitio: viaja dentro del numero de pedimento, que
        ya lo teclea alguien con cuidado porque es el numero que ampara la
        mercancia. Un selector aparte seria un dato mas que puede contradecir
        al pedimento.
        """
        for numero in self.numeros_de_pedimento:
            from . import pedimentos as _ped
            aduana = _ped.aduana_de(numero)
            if aduana:
                return aduana
        return ''

    @property
    def patente(self):
        """La patente del agente aduanal, leida igual que la aduana."""
        for numero in self.numeros_de_pedimento:
            from . import pedimentos as _ped
            patente = _ped.patente_de(numero)
            if patente:
                return patente
        return ''

    @property
    def operaciones_sin_factura(self):
        """
        Los embarques de la tarea a los que les falta la factura comercial.

        Con tarea, la factura que falta deja de ser un renglon incomodo y pasa
        a ser una cuenta atras: hay un camion un dia concreto.
        """
        faltan = []
        for renglon in self.renglones.select_related('operation'):
            op = renglon.operation
            if not op.documents.filter(
                    ranura=OperationDocument.RANURA_FACTURA_COMERCIAL).exists():
                faltan.append(op)
        return faltan

    @property
    def dias_para_el_cruce(self):
        """Cuantos dias faltan. Negativo si ya paso."""
        return (self.fecha_de_cruce - timezone.localdate()).days

    # ── El candado ───────────────────────────────────────────────────────────

    def choque_al_meter(self, op):
        """
        Si `op` no puede entrar en esta tarea -- y por que.

        Un DODA solo puede llevar pedimentos de un mismo agente aduanal. Se
        comprueba ya al meter el embarque en la tarea y no solo al armar el
        camion, porque una tarea con dos patentes no podria subirse entera a
        ningun camion y habria que partirla: mas vale avisar temprano que
        cuando ya esta todo cargado.
        """
        from . import pedimentos as _ped
        dentro = self.numeros_de_pedimento
        suyos = [e.pedimento.numero for e in
                 op.renglones_de_pedimento.select_related('pedimento')
                 if e.pedimento.numero]
        # El pedimento suelto de la operacion, para las que no pasaron por el
        # reparto por bultos.
        if not suyos and (op.pedimento or '').strip():
            suyos = [op.pedimento.strip()]
        for numero in suyos:
            choca = _ped.choque(numero, dentro)
            if choca:
                return choca
        return None


class CrossingTaskItem(models.Model):
    """
    Un embarque dentro de una tarea de cruce.

    Se guarda quien lo metio, cuando y desde donde. Los tres sitios desde los
    que se puede meter -- la lista de operaciones, el renglon de la hoja de
    impuestos y la propia tarea -- hacen lo mismo y dejan la misma linea
    escrita: obligar a ir a un sitio concreto es lo que hace que se acabe
    pidiendo por telefono, y entonces el cambio no queda escrito.
    """

    DESDE_OPERACIONES = 'OPERACIONES'
    DESDE_IMPUESTOS   = 'IMPUESTOS'
    DESDE_LA_TAREA    = 'TAREA'
    ORIGENES = [
        (DESDE_OPERACIONES, _('From the operations list')),
        (DESDE_IMPUESTOS,   _('From the duty sheet')),
        (DESDE_LA_TAREA,    _('From the crossing task')),
    ]

    task      = models.ForeignKey(CrossingTask, on_delete=models.CASCADE,
                                  related_name='renglones')
    operation = models.ForeignKey(WarehouseOperation, on_delete=models.CASCADE,
                                  related_name='cruces')
    desde     = models.CharField(max_length=12, choices=ORIGENES,
                                 default=DESDE_LA_TAREA)

    # Cuantos bultos de este embarque van en **este** cruce. Vacio significa
    # todos, que es el caso normal y el que no hay que teclear.
    #
    # Existe por el caso de los 19 de 20: el cliente pide importar solo 19
    # pallets y el numero 20 se queda en bodega para el siguiente cruce. Sin
    # esto, la unica salida seria sacar la operacion entera de la tarea, que es
    # falso -- 19 de esos pallets si van --, y la orden de carga no podria
    # decir "3 de 5", que es como se ve en el papel.
    bultos    = models.PositiveIntegerField(
                    null=True, blank=True,
                    verbose_name='Bultos que van en este cruce',
                    help_text='Vacio = todos')
    # A partir de que hay una orden de carga emitida, meter o sacar pide
    # motivo. Antes no: la tarea todavia se esta armando.
    motivo    = models.TextField(blank=True, default='')
    added_at  = models.DateTimeField(auto_now_add=True)
    added_by  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                  blank=True, related_name='embarques_metidos')

    class Meta:
        ordering = ['added_at']
        verbose_name = 'Embarque de la tarea'
        verbose_name_plural = 'Embarques de la tarea'
        # Un embarque no puede ir en dos cruces a la vez ni dos veces en el
        # mismo. Lo primero lo comprueba la vista; esto cierra lo segundo.
        unique_together = [('task', 'operation')]

    @property
    def bultos_que_van(self):
        """Los bultos que cruzan, con el numero completo si van todos."""
        return self.bultos if self.bultos is not None else (
            self.operation.bundle_qty or 0)

    @property
    def es_parcial(self):
        return self.bultos_que_van < (self.operation.bundle_qty or 0)

    def __str__(self):
        return f'{self.operation.custom_id} en {self.task.custom_id}'


class CambioDeDiaDeCruce(models.Model):
    """
    Cada vez que se mueve el dia de un cruce, con su motivo.

    La lista corta de motivos no es burocracia: en tres meses deja contestar
    "por que se nos mueven tanto los cruces" con numeros en vez de con
    recuerdos.
    """

    TIPO_DE_CAMBIO = 'TIPO_DE_CAMBIO'
    TRANSPORTE     = 'TRANSPORTE'
    ADUANA         = 'ADUANA'
    CLIENTE        = 'CLIENTE'
    OTRO           = 'OTRO'
    MOTIVOS = [
        (TIPO_DE_CAMBIO, _('Exchange rate')),
        (TRANSPORTE,     _('Transport')),
        (ADUANA,         _('Customs system down')),
        (CLIENTE,        _('The customer asked')),
        (OTRO,           _('Other')),
    ]

    task           = models.ForeignKey(CrossingTask, on_delete=models.CASCADE,
                                       related_name='cambios_de_dia')
    fecha_anterior = models.DateField()
    fecha_nueva    = models.DateField()
    motivo         = models.CharField(max_length=20, choices=MOTIVOS)
    detalle        = models.TextField(blank=True, default='')
    created_at     = models.DateTimeField(auto_now_add=True)
    created_by     = models.ForeignKey(User, on_delete=models.SET_NULL,
                                       null=True, blank=True,
                                       related_name='cambios_de_dia')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Cambio de dia de cruce'
        verbose_name_plural = 'Cambios de dia de cruce'

    def __str__(self):
        return f'{self.task_id}: {self.fecha_anterior} -> {self.fecha_nueva}'


# ═══════════════════════════════════════════════════════════════════════════
#  LOS DOS PAPELES: LA LISTA DE PREPARACION Y LA ORDEN DE CARGA
# ═══════════════════════════════════════════════════════════════════════════
#
# La historia que hay que romper tiene cinco eslabones: oficina emite la orden
# en cuanto el cliente da la primera instruccion, el papel sale de la impresora
# y se va a la bodega, el cliente cambia la instruccion, nadie se lo dice a
# bodega, y el transfer se va con mercancia de mas o de menos -- y el problema
# aparece en la aduana, que es el peor sitio posible para descubrirlo.
#
# El eslabon que se rompe no es el tercero. El cliente va a seguir cambiando de
# opinion: eso es el negocio. Es el segundo: **existe un papel definitivo
# circulando desde antes de que la informacion sea definitiva**.
#
# Por eso hay dos papeles y no uno. La lista de preparacion sale desde el
# minuto uno, no ampara nada y lo dice impreso en grande; la orden de carga no
# se puede emitir hasta que los pedimentos estan pagados -- en ese punto la
# instruccion ya costo dinero y ya no cambia casi nunca --. Bodega no pierde
# nada: sigue pudiendo adelantar trabajo el dia uno. Lo que pierde el papel
# adelantado es la autoridad que hoy tiene y no deberia tener.
#
# La lista de preparacion no es un modelo: es un PDF que se saca cuando hace
# falta. Lo que se guarda es la orden, porque es la que ampara.


class LoadOrder(models.Model):
    """
    La orden de carga de una tarea, en una version concreta.

    No hay orden suelta que se pueda escribir a mano: sale de un boton dentro
    de la tarea y su contenido es, siempre, lo que la tarea decia en ese
    momento. Por eso cada version guarda su propia copia de los renglones --
    si mirara la tarea en vivo, una orden impresa hace tres dias diria hoy otra
    cosa, que es exactamente el problema que se esta resolviendo.

    Cualquier cambio en la tarea despues de emitida sube la orden a v2 y marca
    la anterior como obsoleta. El QR del pie codifica la orden y su version, y
    quien va a surtir lo pistolea antes de empezar: el papel sigue sin saber
    nada, pero ahora se le puede preguntar al sistema en cinco segundos sin
    llamar a oficina.
    """

    PREFIJO = 'OC'

    task    = models.ForeignKey(CrossingTask, on_delete=models.CASCADE,
                                related_name='ordenes')
    # El numero es el mismo en todas las versiones de la misma orden: lo que
    # cambia es la version. En el papel se lee "OC260902-0007 v2".
    custom_id = models.CharField(max_length=20, blank=True)
    version   = models.PositiveIntegerField(default=1)

    # La version anterior no se borra: alguien la tiene impresa en la mano, y
    # el sistema tiene que poder contestarle que ya no vale.
    obsoleta   = models.BooleanField(default=False)
    emitida_en = models.DateTimeField(auto_now_add=True)
    emitida_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name='ordenes_emitidas')

    class Meta:
        ordering = ['-version']
        verbose_name = 'Orden de carga'
        verbose_name_plural = 'Ordenes de carga'
        unique_together = [('custom_id', 'version')]

    def __str__(self):
        return f'{self.custom_id} v{self.version}'

    @property
    def etiqueta(self):
        return f'{self.custom_id} v{self.version}'

    @property
    def total_bultos(self):
        return sum(r.bultos for r in self.renglones.all())

    @property
    def total_kilos(self):
        pesos = [r.weight_kgs for r in self.renglones.all() if r.weight_kgs]
        return sum(pesos) if pesos else None

    # ── Cuando se puede emitir ───────────────────────────────────────────────

    @staticmethod
    def faltantes_para_emitir(task):
        """
        Lo que impide emitir la orden de carga de `task`, para la pantalla.

        Se habilita despues de "pedimentos pagados". Entre que la tarea se crea
        y que los pedimentos se pagan pueden pasar dias, y en todo ese tiempo
        no existe ninguna orden de carga. Es a proposito: si alguien de oficina
        pide "sacame la orden ya" -- y va a pasar --, la respuesta del sistema
        es la lista de preparacion, que es lo que esa persona necesitaba de
        verdad.
        """
        faltan = []
        if not task.renglones.exists():
            faltan.append(_('No shipments in this crossing yet'))
            return faltan

        pedimentos = task.pedimentos
        if not pedimentos:
            faltan.append(_('No pedimento for this merchandise yet'))
        else:
            sin_pagar = [p for p in pedimentos if p.estado != Pedimento.PAGADO]
            if sin_pagar:
                faltan.append(
                    _('These pedimentos are not paid yet: %(cuales)s')
                    % {'cuales': ', '.join(p.etiqueta for p in sin_pagar)})

        # Y no puede quedar mercancia sin pedimento. Esta comprobacion existia
        # antes como puerta de la revision; su sitio es este, que es donde de
        # verdad importa que no falte nada.
        sueltos = []
        for renglon in task.renglones.select_related('operation'):
            op = renglon.operation
            dentro = sum(e.bultos for e in op.renglones_de_pedimento.all())
            # Se comparan los que **cruzan**, no los que llegaron: en el caso
            # de los 19 de 20, el pallet que se queda en bodega no necesita
            # pedimento para este cruce.
            if renglon.bultos_que_van - dentro > 0:
                sueltos.append(op.custom_id)
        if sueltos:
            faltan.append(_('These shipments still have bundles without a '
                            'pedimento: %(cuales)s')
                          % {'cuales': ', '.join(sueltos)})
        return faltan


    # ── El cuadre ────────────────────────────────────────────────────────────

    def cuadre(self, fase=None):
        """
        Que dice el escaneo contra lo que dice esta orden.

        Se compara **por operacion y por cuenta**, no bulto a bulto, porque un
        embarque puede ir parcial: cuando van 3 de 5, la orden dice cuantos van
        pero no cuales, y el pallet que se queda es sencillamente el que no se
        pistoleo. Ese dato lo tiene la bodega en la mano sin apuntarlo en
        ningun lado.

        Lo que si se rechaza es un bulto que no pertenece a la orden -- de otra
        operacion, o un numero que esa operacion no tiene --, que es el caso
        grave: mercancia que se iba a ir sin amparar y que aparece en la
        aduana.

        Devuelve las tres cosas que pueden salir mal y lo que va bien.
        """
        from . import bultos as _bultos

        fase = fase or EscaneoDeBulto.CARGA
        # Lo que espera la orden: cuantos bultos de cada operacion, y cuantos
        # tiene esa operacion en total -- para saber que numeros son suyos.
        esperados, total_de = {}, {}
        for renglon in self.renglones.select_related('operation'):
            clave = renglon.operation.custom_id
            esperados[clave] = esperados.get(clave, 0) + (renglon.bultos or 0)
            total_de[clave] = renglon.operation.bundle_qty or renglon.bultos or 0

        escaneados, repetidos = {}, []
        for e in (self.task.escaneos.filter(fase=fase)
                  .select_related('operation').order_by('created_at')):
            clave = e.operation.custom_id
            vistos = escaneados.setdefault(clave, set())
            if e.numero_de_bulto in vistos:
                repetidos.append(_bultos.codigo(clave, e.numero_de_bulto))
            else:
                vistos.add(e.numero_de_bulto)

        faltan, sobran, verificados = [], [], 0
        for clave, cuantos in esperados.items():
            vistos = escaneados.get(clave, set())
            # Los que no son de esta operacion: un numero que no existe.
            buenos = {n for n in vistos if 1 <= n <= (total_de[clave] or 0)}
            sobran += [_bultos.codigo(clave, n) for n in sorted(vistos - buenos)]
            verificados += min(len(buenos), cuantos)
            if len(buenos) < cuantos:
                faltan.append(_('%(op)s: %(n)d missing')
                              % {'op': clave, 'n': cuantos - len(buenos)})
            elif len(buenos) > cuantos:
                sobran.append(_('%(op)s: %(n)d more than the order says')
                              % {'op': clave, 'n': len(buenos) - cuantos})

        # Y lo pistoleado de operaciones que no van en esta orden.
        for clave, vistos in escaneados.items():
            if clave not in esperados:
                sobran += [_bultos.codigo(clave, n) for n in sorted(vistos)]

        return {
            'esperados':   sum(esperados.values()),
            'verificados': verificados,
            'faltan':      [str(f) for f in faltan],
            'sobran':      [str(x) for x in sobran],
            'repetidos':   repetidos,
            'cuadra':      (not faltan and not sobran and bool(esperados)),
        }

    @property
    def puede_emitir_remision(self):
        """
        Si el escaneo de carga cuadra contra esta orden y esta orden vale.

        Una remision pertenece a la version con la que se emitio: si la tarea
        cambia despues, la orden sube de version, esta queda obsoleta y la
        remision se vuelve a bloquear hasta verificar lo que cambio.
        """
        return not self.obsoleta and self.cuadre()['cuadra']


class LoadOrderItem(models.Model):
    """
    Un renglon de la orden, copiado tal como estaba al emitirla.

    Se copia y no se mira en vivo por la misma razon por la que existe la
    version: la orden que alguien lleva impresa tiene que poder compararse
    contra lo que decia cuando se imprimio, no contra lo que dice la tarea
    ahora.
    """
    order     = models.ForeignKey(LoadOrder, on_delete=models.CASCADE,
                                  related_name='renglones')
    operation = models.ForeignKey(WarehouseOperation, on_delete=models.CASCADE,
                                  related_name='renglones_de_orden')

    # La copia. Los nombres largos van en texto y no por clave foranea a
    # proposito: si manana alguien corrige el nombre de un proveedor en el
    # catalogo, la orden emitida no puede cambiar sola.
    po_order    = models.CharField(max_length=200, blank=True, default='')
    bultos      = models.PositiveIntegerField(default=0)
    bundle_type = models.CharField(max_length=200, blank=True, default='')
    weight_lbs  = models.DecimalField(max_digits=10, decimal_places=2,
                                      null=True, blank=True)
    weight_kgs  = models.DecimalField(max_digits=10, decimal_places=2,
                                      null=True, blank=True)
    ubicacion   = models.CharField(max_length=100, blank=True, default='')
    pedimento   = models.CharField(max_length=100, blank=True, default='')
    shipper     = models.CharField(max_length=200, blank=True, default='')
    invoice     = models.CharField(max_length=200, blank=True, default='')
    descripcion = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['operation__custom_id']
        verbose_name = 'Renglon de la orden de carga'
        verbose_name_plural = 'Renglones de la orden de carga'

    def __str__(self):
        return f'{self.operation.custom_id} x{self.bultos}'


class EscaneoDeBulto(models.Model):
    """
    Un bulto pistoleado.

    El escaneo se hace dos veces: una al preparar y otra al cargar. La de
    preparar no habilita nada -- adelanta trabajo y deja marcado lo que ya esta
    en el anden --; la de cargar es la que cuenta. Si entre una y otra no se
    movio nada, la segunda es un repaso rapido en vez de empezar de cero.
    """

    PREPARACION = 'PREPARACION'
    CARGA       = 'CARGA'
    FASES = [
        (PREPARACION, _('Picking')),
        (CARGA,       _('Loading')),
    ]

    task      = models.ForeignKey(CrossingTask, on_delete=models.CASCADE,
                                  related_name='escaneos')
    # Contra que version se pistoleo. La de preparacion puede no tener orden
    # todavia: la lista de preparacion existe desde el minuto uno.
    order     = models.ForeignKey(LoadOrder, on_delete=models.SET_NULL,
                                  null=True, blank=True, related_name='escaneos')
    operation = models.ForeignKey(WarehouseOperation, on_delete=models.CASCADE,
                                  related_name='escaneos')
    numero_de_bulto = models.PositiveIntegerField()
    fase      = models.CharField(max_length=12, choices=FASES, default=CARGA)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='escaneos')

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Bulto escaneado'
        verbose_name_plural = 'Bultos escaneados'
        # Un bulto se pistolea una vez por fase. El segundo disparo sobre el
        # mismo bulto no es otro bulto: es el mismo otra vez, y la pantalla lo
        # tiene que decir en vez de contarlo dos veces.
        unique_together = [('task', 'operation', 'numero_de_bulto', 'fase')]

    def __str__(self):
        from . import bultos as _bultos
        return _bultos.codigo(self.operation.custom_id, self.numero_de_bulto)


class Remision(models.Model):
    """
    El papel que se le entrega al transfer.

    Lleva lo mismo que la orden de carga sobre la mercancia, y encima toda la
    cadena de entrega: quien cruza, a quien le deja la carga en la frontera
    mexicana, a donde va despues y a quien se le factura el servicio. En la
    practica son tres empresas distintas, y hoy esa informacion viaja de boca
    en boca.

    Solo se emite cuando lo escaneado es exactamente lo que dice la orden
    vigente -- ni falta ni sobra --, y pertenece a la version con la que se
    emitio, igual que una aprobacion pertenece a la proforma que se aprobo.
    """

    PREFIJO = 'RM'

    task  = models.ForeignKey(CrossingTask, on_delete=models.CASCADE,
                              related_name='remisiones')
    order = models.ForeignKey(LoadOrder, on_delete=models.PROTECT,
                              related_name='remisiones')
    custom_id = models.CharField(max_length=20, blank=True, unique=True)

    # ── Quien cruza ──────────────────────────────────────────────────────────
    transfer_empresa = models.CharField(max_length=200, blank=True, default='')
    transfer_chofer  = models.CharField(max_length=200, blank=True, default='')
    transfer_unidad  = models.CharField(max_length=100, blank=True, default='')
    sello            = models.CharField(max_length=100, blank=True, default='')

    # ── A quien se le entrega en la frontera ────────────────────────────────
    # Se propone de la ficha del cliente, porque es casi siempre la misma: un
    # dato que se teclea una vez al ano en vez de una vez por embarque es un
    # dato que casi nunca sale mal, y aqui salir mal significa que la mercancia
    # se le entrega a quien no es.
    linea_de_enlace     = models.CharField(max_length=200, blank=True, default='')
    domicilio_de_enlace = models.TextField(blank=True, default='')

    # ── El dueno de la mercancia ─────────────────────────────────────────────
    destinatario           = models.CharField(max_length=200, blank=True, default='')
    destinatario_rfc       = models.CharField(max_length=20, blank=True, default='')
    destinatario_domicilio = models.TextField(blank=True, default='')

    # ── La salida de emergencia ──────────────────────────────────────────────
    # Cuando el escaneo no cuadra y el camion tiene que salir igual. Solo un
    # manager o superior, y con motivo escrito. Prefiero un embarque irregular
    # que quede registrado con nombre y hora a uno que se vaya por fuera del
    # sistema -- que es lo que pasa siempre que un candado no tiene salida.
    con_discrepancia    = models.BooleanField(default=False)
    motivo_discrepancia = models.TextField(blank=True, default='')
    diferencia          = models.TextField(blank=True, default='',
                                           verbose_name='La diferencia exacta')
    autorizada_por = models.ForeignKey(User, on_delete=models.SET_NULL,
                                       null=True, blank=True,
                                       related_name='remisiones_autorizadas')

    bultos_verificados = models.PositiveIntegerField(default=0)
    bultos_de_la_orden = models.PositiveIntegerField(default=0)
    verificada_por = models.ForeignKey(User, on_delete=models.SET_NULL,
                                       null=True, blank=True,
                                       related_name='remisiones_verificadas')
    verificada_en  = models.DateTimeField(null=True, blank=True)

    emitida_en  = models.DateTimeField(auto_now_add=True)
    emitida_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name='remisiones_emitidas')

    class Meta:
        ordering = ['-emitida_en']
        verbose_name = 'Remision'
        verbose_name_plural = 'Remisiones'

    def __str__(self):
        return self.custom_id or f'RM-{self.pk}'
