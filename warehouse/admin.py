from django.contrib import admin
from .models import (WarehouseOperation, Catalog, OperationDocument, UserProfile,
                     DeletionLog, DocumentSequence, NotificationLog,
                     PlatformUser, Tenant)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'role', 'created_at']  # Ajusta según tus campos  'tenant',
    list_filter = ['role'] #### , 'tenant'
    search_fields = ['user__username', 'user__email']


@admin.register(WarehouseOperation)
class WarehouseOperationAdmin(admin.ModelAdmin):
    list_display    = ['custom_id', 'date', 'operation_type', 'get_customer_display', 'status', 'email_sent', 'created_by'] ##########En warehouse/admin.py, agrega list_filter = ['tenant'] 072526 20:22 , 'tenant'
    list_filter     = ['operation_type', 'date', 'email_sent'] #####En warehouse/admin.py, agrega list_filter = ['tenant'] 072526 20:25 'tenant',
    search_fields   = ['custom_id', 'customer__name', 'customer_name_manual']
    readonly_fields = ['custom_id', 'created_at', 'updated_at']


@admin.register(Catalog)
class CatalogAdmin(admin.ModelAdmin):
    list_display  = ['name', 'category', 'contact_email', 'phone', 'whatsapp', 'active'] #####En warehouse/admin.py, agrega list_filter = ['tenant'] 072526 20:25 , 'tenant'
    list_filter   = ['category', 'active'] #####En warehouse/admin.py, agrega list_filter = ['tenant'] 072526 20:25 'tenant',
    search_fields = ['name', 'contact_email']


@admin.register(OperationDocument)
class OperationDocumentAdmin(admin.ModelAdmin):
    list_display = ['operation', 'file_type', 'original_name', 'uploaded_by', 'uploaded_at'] #####En warehouse/admin.py, agrega list_filter = ['tenant'] 072526 20:25  , 'tenant'


@admin.register(DeletionLog)
class DeletionLogAdmin(admin.ModelAdmin):
    list_display  = ['custom_id', 'operation_type', 'customer_name', 'deleted_by', 'deleted_at']#####En warehouse/admin.py, agrega list_filter = ['tenant'] 072526 20:25 , 'tenant'
    list_filter   = ['operation_type', 'deleted_at']
    readonly_fields = ['deleted_at']


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    """
    Es una bitácora: se consulta, no se edita. Por eso todo va en readonly y no
    se permite agregar renglones a mano.
    """
    list_display  = ['created_at', 'operation_custom_id', 'customer', 'channel',
                     'event', 'status', 'recipient']
    list_filter   = ['channel', 'event', 'status', 'created_at', 'tenant']
    search_fields = ['operation_custom_id', 'recipient', 'subject', 'detail']
    readonly_fields = [f.name for f in NotificationLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    """
    Las empresas no estaban en el admin, asi que lo que vive en `config` -las
    preferencias de marca que no merecen una columna propia- no habia forma de
    tocarlo sin abrir una shell. La primera que lo necesita es
    `email_footer_note`, la leyenda al pie de los correos:

        {"email_footer_note": "Provider for RDL Systems LLC."}
    """
    list_display  = ['name', 'type', 'subdomain', 'plan', 'is_active', 'created_at']
    list_filter   = ['type', 'plan', 'is_active']
    search_fields = ['name', 'subdomain', 'billing_email']
    readonly_fields = ['created_at']


@admin.register(DocumentSequence)
class DocumentSequenceAdmin(admin.ModelAdmin):
    """
    El contador de nombres del expediente. Se mira para entender de donde sale
    un numero; cambiarlo a mano es como cambiar un consecutivo fiscal, asi que
    no se ofrece el alta y el valor va en solo lectura.
    """
    list_display = ['tenant', 'day', 'last_value']
    list_filter  = ['tenant']
    readonly_fields = ['tenant', 'day', 'last_value']

    def has_add_permission(self, request):
        return False


@admin.register(PlatformUser)
class PlatformUserAdmin(admin.ModelAdmin):
    """
    Quien administra el SaaS. Es un nivel distinto del rol que un usuario tenga
    dentro de una empresa, y por eso vive en su propio modelo.
    """
    list_display  = ['user', 'role', 'created_at']
    list_filter   = ['role']
    search_fields = ['user__username']
    readonly_fields = ['created_at']
