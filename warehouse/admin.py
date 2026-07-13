from django.contrib import admin
from .models import WarehouseOperation, Catalog, OperationDocument, UserProfile, DeletionLog


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display  = ['user', 'role', 'customer', 'delete_password']
    list_filter   = ['role']
    search_fields = ['user__username']


@admin.register(WarehouseOperation)
class WarehouseOperationAdmin(admin.ModelAdmin):
    list_display    = ['custom_id', 'date', 'operation_type', 'get_customer_display', 'status', 'email_sent', 'created_by']
    list_filter     = ['operation_type', 'date', 'email_sent']
    search_fields   = ['custom_id', 'customer__name', 'customer_name_manual']
    readonly_fields = ['custom_id', 'created_at', 'updated_at']


@admin.register(Catalog)
class CatalogAdmin(admin.ModelAdmin):
    list_display  = ['name', 'category', 'contact_email', 'phone', 'whatsapp', 'active']
    list_filter   = ['category', 'active']
    search_fields = ['name', 'contact_email']


@admin.register(OperationDocument)
class OperationDocumentAdmin(admin.ModelAdmin):
    list_display = ['operation', 'file_type', 'original_name', 'uploaded_by', 'uploaded_at']


@admin.register(DeletionLog)
class DeletionLogAdmin(admin.ModelAdmin):
    list_display  = ['custom_id', 'operation_type', 'customer_name', 'deleted_by', 'deleted_at']
    list_filter   = ['operation_type', 'deleted_at']
    readonly_fields = ['deleted_at']
