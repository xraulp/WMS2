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

    class Meta:
        ordering = ['category', 'name']

    def __str__(self):
        return f"{self.get_category_display()} - {self.name}"


class UserProfile(models.Model):
      user = models.OneToOneField(User, on_delete=models.CASCADE)
      tenant = models.ForeignKey('Tenant', on_delete=models.CASCADE, null=True, blank=True, related_name='users')
      #role = models.ForeignKey('Role', on_delete=models.SET_NULL, null=True, blank=True, related_name='users')
      role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='staff')  # <--- CAMBIA ESTO
      #created_at = models.DateTimeField(auto_now_add=True)   # <--- AGREGA ESTA LÍNEA
      created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True) ### 072626 9:47

ROLE_CHOICES = [
    ('superadmin', 'Super Administrator'),
    ('manager',    'Manager'),
    ('staff',      'Staff'),
    ('customer',   'Customer'),
    ]
user            = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
role            = models.CharField(max_length=20, choices=ROLE_CHOICES, default='manager')
plain_password  = models.CharField(max_length=128, blank=True, null=True)
ustomer        = models.ForeignKey(Catalog, on_delete=models.SET_NULL, null=True, blank=True,
                                        limit_choices_to={'category': 'CUSTOMER'})
delete_password = models.CharField(max_length=128, blank=True, null=True,
                       help_text='Custom password required to delete records')

def is_superadmin(self):
        return self.role == 'superadmin' or self.user.is_superuser

def is_manager(self):

 def is_staff_role(self):
        return self.role == 'staff'

def is_customer(self):
        return self.role == 'customer'

def is_home(self):
        return self.role in ('superadmin', 'manager') or self.user.is_superuser

def can_delete(self):
        return self.role in ('superadmin', 'manager') or self.user.is_superuser

def can_create_operations(self):
        return self.role in ('superadmin', 'manager', 'staff')

def can_manage_users(self):
        return self.role == 'superadmin' or self.user.is_superuser

def can_see_tab(self, tab):
        if self.role == 'customer':
            return tab in ('database', 'digital', 'reports')
        if self.role == 'staff':
            return tab in ('form', 'database', 'catalog', 'digital', 'reports')
        if self.role == 'manager':
            return tab in ('form', 'database', 'catalog', 'digital', 'reports')
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

    def __str__(self):
        return f"{self.tenant.name} - {self.plan}"

# Métodos de utilidad
    def can_access_tenant(self, tenant):
        """Verifica si el usuario puede acceder a los datos de un tenant."""
        if not self.tenant:
            return False
        if self.tenant == tenant:
            return True
        # Un usuario de organización puede acceder a sus branches
        if self.tenant.is_organization and tenant.parent == self.tenant:
            return True
        return False
    
    def has_tenant_permission(self, perm_codename):
        """Verifica si el usuario tiene un permiso específico (con herencia)."""
        if self.role:
            return self.role.has_permission(perm_codename)
        return False

    
    is_active = models.BooleanField(default=True, verbose_name="Activo")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Creado el")
    
    config = models.JSONField(default=dict, blank=True, verbose_name="Configuración")
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
        ordering = ['tenant__name']

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"

    @property
    def is_organization(self):
        return self.type == 'organization'

    @property
    def is_branch(self):
        return self.type == 'branch'