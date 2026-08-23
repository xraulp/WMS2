from django.urls import path
from . import views

urlpatterns = [
    path('', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard, name='dashboard'),
    # Operations
    path('operations/create/', views.operation_create, name='operation_create'),
    path('operations/search/', views.operations_search, name='operations_search'),
    path('operations/free-entries/', views.free_entries, name='free_entries'),
    path('operations/exit-totals/', views.exit_entry_totals, name='exit_entry_totals'),
    path('operations/import/', views.operations_import, name='operations_import'),
    path('operations/layout/', views.operations_layout, name='operations_layout'),
    path('operations/<int:pk>/', views.operation_detail, name='operation_detail'),
    path('operations/<int:pk>/edit/', views.operation_edit, name='operation_edit'),
    # `operations/<pk>/delete/` se retiro: borraba con un POST pelado, sin
    # contrasena ni motivo, mientras la pantalla usaba delete-confirm. Con el
    # staff pudiendo borrar, esa puerta volvia decorativo el control.
    path('operations/<int:pk>/delete-confirm/', views.operation_delete_confirm, name='operation_delete_confirm'),
    path('operations/<int:pk>/pdf/', views.operation_pdf, name='operation_pdf'),
    path('operations/<int:pk>/label/', views.operation_label, name='operation_label'),
    path('operations/<int:pk>/email/', views.operation_send_email, name='operation_send_email'),
    path('operations/<int:pk>/whatsapp/', views.operation_send_whatsapp, name='operation_send_whatsapp'),
    path('operations/<int:pk>/download-all/', views.operation_download_all, name='operation_download_all'),
    # El hilo de la operacion. El GET lo pide el panel al abrirse y cada
    # refresco del polling; el POST escribe.
    path('operations/<int:pk>/chat/', views.operation_chat, name='operation_chat'),
    path('operations/<int:pk>/chat/send/', views.operation_chat_send, name='operation_chat_send'),
    # Los archivos del expediente se sirven por aqui y no por el enlace
    # publico del bucket: la vista comprueba quien pide y entrega una URL
    # firmada de vida corta. Ver warehouse/almacen.py.
    path('documents/<int:doc_pk>/file/', views.document_file, name='document_file'),
    # NUEVA URL: Filtrar operaciones por usuario creador
    path('operations/by-user/<int:user_id>/', views.operations_by_user, name='operations_by_user'),
    # Digital
    path('digital/search/', views.digital_search, name='digital_search'),
    path('digital/<int:pk>/upload/', views.digital_upload, name='digital_upload'),
    path('digital/file/<int:doc_pk>/reorder/', views.digital_reorder, name='digital_reorder'),
    path('digital/file/<int:doc_pk>/delete/', views.digital_delete_file, name='digital_delete_file'),
    path('digital/delete-multiple/', views.digital_delete_multiple, name='digital_delete_multiple'),
    # Papelera y bitacora de borrados
    path('deletions/', views.deletion_log, name='deletion_log'),
    path('deletions/<int:doc_pk>/restore/', views.document_restore, name='document_restore'),
    path('deletions/<int:doc_pk>/purge/', views.document_purge, name='document_purge'),
    # Report generator
    path('reports/', views.report_generator, name='report_generator'),
    path('reports/pdf/', views.report_generator_pdf, name='report_generator_pdf'),
    path('reports/email/', views.report_generator_email, name='report_generator_email'),
    path('reports/excel/', views.report_generator_excel, name='report_generator_excel'),
    # Catalog
    path('catalog/create/', views.catalog_create, name='catalog_create'),
    path('catalog/list/', views.catalog_list, name='catalog_list'),
    path('catalog/import/', views.catalog_import, name='catalog_import'),
    path('catalog/layout/', views.catalog_layout, name='catalog_layout'),
    path('catalog/<int:pk>/edit/', views.catalog_edit, name='catalog_edit'),
    path('catalog/<int:pk>/delete/', views.catalog_delete, name='catalog_delete'),
    path('catalog/autocomplete/', views.catalog_autocomplete, name='catalog_autocomplete'),
    # Users
    path('users/', views.user_management, name='user_management'),
    path('platform/', views.platform_dashboard, name='platform_dashboard'),
    path('platform/tenants/', views.platform_tenant_list, name='platform_tenant_list'),
    path('platform/notifications/', views.platform_notifications, name='platform_notifications'),
    path('platform/invoices/', views.platform_invoices, name='platform_invoices'),
    path('platform/invoices/<int:pk>/pdf/', views.platform_invoice_pdf, name='platform_invoice_pdf'),
    path('platform/users/', views.platform_users, name='platform_users'),
    # Debug
    path('debug/catalog/', views.debug_catalog, name='debug_catalog'),
    path('mobile/', views.mobile_dashboard, name='mobile_dashboard'),
]