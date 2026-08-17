from django.contrib import admin
from .models import (WarehouseOperation, Catalog, OperationDocument, UserProfile,
                     DeletionLog, NotificationLog)


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
