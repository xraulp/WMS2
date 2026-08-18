from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

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
    user            = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    tenant          = models.ForeignKey('Tenant', on_delete=models.CASCADE, null=True, blank=True, related_name='users')
    role            = models.CharField(max_length=20, choices=ROLE_CHOICES, default='staff')
    plain_password  = models.CharField(max_length=128, blank=True, null=True)
    customer        = models.ForeignKey(Catalog, on_delete=models.SET_NULL, null=True, blank=True,
                                         limit_choices_to={'category': 'CUSTOMER'})
    delete_password = models.CharField(max_length=128, blank=True, null=True,
                       help_text='Custom password required to delete records')
    created_at      = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    def is_superadmin(self):
        return self.role == 'superadmin' or self.user.is_superuser

    def is_admin(self):
        return self.role == 'admin'

    def is_manager(self):
        return self.role == 'manager'

    def is_staff_role(self):
        return self.role == 'staff'

    def is_customer(self):
        return self.role == 'customer'

    def is_home(self):
        return self.role in ('superadmin', 'admin', 'manager') or self.user.is_superuser

    def can_delete(self):
        return self.role in ('superadmin', 'admin', 'manager') or self.user.is_superuser

    def can_create_operations(self):
        return self.role in ('superadmin', 'admin', 'manager', 'staff')

    def can_manage_users(self):
        return self.role in ('superadmin', 'admin') or self.user.is_superuser

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

        El superusuario de Django cuenta como el maximo, igual que en
        `is_superadmin()`, porque hoy los dos niveles comparten llave.
        """
        if self.user.is_superuser:
            return ROLE_RANK['superadmin']
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

class OperationDocument(models.Model):
    FILE_TYPE_CHOICES = [('PHOTO', 'Photo'), ('DOCUMENT', 'Document'), ('VIDEO', 'Video'), ('OTHER', 'Other')]
    tenant        = models.ForeignKey('Tenant', on_delete=models.CASCADE, null=True, blank=True, related_name='documents')
    operation     = models.ForeignKey(WarehouseOperation, on_delete=models.CASCADE, related_name='documents')
    file_type     = models.CharField(max_length=10, choices=FILE_TYPE_CHOICES, default='OTHER')
    file          = models.FileField(upload_to='operations/%Y/%m/%d/')
    original_name = models.CharField(max_length=255, blank=True)
    uploaded_by   = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    uploaded_at   = models.DateTimeField(auto_now_add=True)
    digital_name  = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return f"{self.get_file_type_display()} - {self.operation.custom_id}"


class DeletionLog(models.Model):
    deleted_by     = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    deleted_at     = models.DateTimeField(auto_now_add=True)
    custom_id      = models.CharField(max_length=20)
    operation_type = models.CharField(max_length=5)
    operation_date = models.DateField(null=True, blank=True)
    customer_name  = models.CharField(max_length=200, blank=True)
    description    = models.TextField(blank=True)
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
    
    # Facturación
    invoice_number = models.CharField(max_length=50, blank=True, null=True, verbose_name="Número de Factura")
    invoice_date = models.DateField(null=True, blank=True, verbose_name="Fecha de Factura")
    amount_usd = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Monto (USD)")

    class Meta:
        verbose_name = "Suscripción"
        verbose_name_plural = "Suscripciones"
        ordering = ['tenant__name']

    def __str__(self):
        return f"{self.tenant.name} - {self.plan}"


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
