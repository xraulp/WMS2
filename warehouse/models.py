import logging
import os
import re
import uuid

from django.db import connection, models, transaction
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.text import slugify

logger = logging.getLogger(__name__)

# warehouse/models.py (al inicio, después de los imports)
ROLE_CHOICES = [
    ('superadmin', 'Super Administrador'),
    ('admin', 'Administrador'),
    ('manager', 'Gerente'),
    ('staff', 'Staff'),
    ('customer', 'Cliente'),
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
        ('CUSTOMER',    'Customer'),
        ('SHIPPER',     'Shipper'),
        ('CARRIER',     'Carrier'),
        ('BUNDLE_TYPE', 'Type of Bundle'),
        ('TYPE_OP',     'Type of Operation'),
        ('CC_EMAIL',    'CC Email'),
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


class WarehouseOperation(models.Model):
    TYPE_CHOICES = [('ENTRY', 'Entry'), ('EXIT', 'Exit')]
    tenant               = models.ForeignKey('Tenant', on_delete=models.CASCADE, null=True, blank=True, 
                                             related_name='operations')
    date                 = models.DateField(default=timezone.now)
    operation_type       = models.CharField(max_length=5, choices=TYPE_CHOICES)
    custom_id            = models.CharField(max_length=20, unique=True, blank=True)
    entry_dispatched     = models.CharField(max_length=500, blank=True, null=True)
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
        prefix = 'ED' if self.operation_type == 'ENTRY' else 'SD'
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
        # `uploaded_at` es el orden en que se subieron, y `pk` desempata la
        # subida multiple, donde varios archivos comparten el instante.
        ordering = ['uploaded_at', 'pk']

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
    ('admin', 'Administrador de plataforma'),
    ('staff', 'Soporte de plataforma'),
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
