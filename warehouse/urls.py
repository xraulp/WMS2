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
    path('operations/<int:pk>/delete/', views.operation_delete, name='operation_delete'),
    path('operations/<int:pk>/delete-confirm/', views.operation_delete_confirm, name='operation_delete_confirm'),
    path('operations/<int:pk>/pdf/', views.operation_pdf, name='operation_pdf'),
    path('operations/<int:pk>/label/', views.operation_label, name='operation_label'),
    path('operations/<int:pk>/email/', views.operation_send_email, name='operation_send_email'),
    path('operations/<int:pk>/whatsapp/', views.operation_send_whatsapp, name='operation_send_whatsapp'),
    path('operations/<int:pk>/download-all/', views.operation_download_all, name='operation_download_all'),
    # NUEVA URL: Filtrar operaciones por usuario creador
    path('operations/by-user/<int:user_id>/', views.operations_by_user, name='operations_by_user'),
    # Digital
    path('digital/search/', views.digital_search, name='digital_search'),
    path('digital/<int:pk>/upload/', views.digital_upload, name='digital_upload'),
    path('digital/file/<int:doc_pk>/delete/', views.digital_delete_file, name='digital_delete_file'),
    path('digital/delete-multiple/', views.digital_delete_multiple, name='digital_delete_multiple'),
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
    # Debug
    path('debug/catalog/', views.debug_catalog, name='debug_catalog'),
    path('mobile/', views.mobile_dashboard, name='mobile_dashboard'),
]