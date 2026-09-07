from django.urls import path
from . import views

urlpatterns = [
    path('', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard, name='dashboard'),
    # Las cifras de la empresa, el primer panel de las dos pantallas.
    path('inicio/', views.inicio_panel, name='inicio_panel'),
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
    # Los numeros de los avisos de la tabla, para no recargarla entera.
    path('operations/chat-badges/', views.chat_badges, name='chat_badges'),
    path('operations/aging-count/', views.resumen_de_vencidas, name='resumen_de_vencidas'),
    # La tabla acotada a lo que espera respuesta, que es a donde lleva el
    # contador global de mensajes sin leer.
    path('operations/unread/', views.operations_unread, name='operations_unread'),
    path('operations/aging/', views.operations_aging, name='operations_aging'),
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
    # Quien entra al sistema por parte de un cliente. Cuelga de la ficha del
    # cliente porque es ahi donde se nota que no tiene a nadie.
    path('catalog/<int:pk>/access/', views.customer_access, name='customer_access'),
    path('catalog/autocomplete/', views.catalog_autocomplete, name='catalog_autocomplete'),
    # Users
    # Bodegas y posiciones
    # Como quiere ver la pantalla cada quien.
    path('preferencias/tema/', views.cambiar_tema, name='cambiar_tema'),
    path('preferencias/idioma/', views.cambiar_idioma, name='cambiar_idioma'),
    path('locations/', views.locations_panel, name='locations_panel'),
    path('locations/warehouse/create/', views.warehouse_create, name='warehouse_create'),
    path('locations/generate/', views.locations_generate, name='locations_generate'),
    path('locations/<int:pk>/toggle/', views.location_toggle, name='location_toggle'),

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

    # Armar pedimentos. Cuelga del cliente y no de una tarea de cruce: un
    # pedimento se elabora y se manda a revision con solo la llegada y la
    # factura comercial, sin que exista ninguna tarea programada.
    path('pedimentos/', views.pedimentos_panel, name='pedimentos_panel'),
    path('pedimentos/new/', views.pedimento_create, name='pedimento_create'),
    path('pedimentos/<int:pk>/number/', views.pedimento_numero, name='pedimento_numero'),
    path('pedimentos/<int:pk>/assign/', views.pedimento_asignar, name='pedimento_asignar'),
    path('pedimentos/<int:pk>/unassign/', views.pedimento_quitar, name='pedimento_quitar'),
    path('pedimentos/<int:pk>/delete/', views.pedimento_borrar, name='pedimento_borrar'),

    # Las ranuras del expediente del pedimento, que son las que deciden si el
    # boton de enviar a revision se enciende.
    path('pedimentos/<int:pk>/upload/', views.pedimento_subir, name='pedimento_subir'),
    path('pedimentos/<int:pk>/upload/remove/', views.pedimento_quitar_documento,
         name='pedimento_quitar_documento'),
    path('pedimentos/<int:pk>/serials/', views.pedimento_fotos_de_series,
         name='pedimento_fotos_de_series'),
    path('pedimentos/file/<int:pk>/', views.pedimento_archivo, name='pedimento_archivo'),
    path('pedimentos/<int:pk>/zip/', views.pedimento_zip, name='pedimento_zip'),
    path('pedimentos/<int:pk>/review/', views.pedimento_enviar_a_revision,
         name='pedimento_enviar_a_revision'),
    # La factura comercial es del embarque, no del pedimento, pero se marca y
    # se sube desde esta pantalla porque es aqui donde estorba que falte.
    path('operations/<int:pk>/invoice/mark/', views.operacion_marcar_factura,
         name='operacion_marcar_factura'),
    path('operations/<int:pk>/invoice/upload/', views.operacion_subir_factura,
         name='operacion_subir_factura'),

    # La hoja de impuestos. Una por cliente, viva siempre: no se abre al llegar
    # a un paso, se mira tres veces al dia.
    path('impuestos/', views.impuestos_panel, name='impuestos_panel'),
    path('impuestos/new/', views.renglon_crear, name='renglon_crear'),
    path('impuestos/<int:pk>/save/', views.renglon_guardar, name='renglon_guardar'),

    # Las tareas de cruce: un camion, un dia, y la mercancia que va dentro.
    path('cruces/', views.cruces_panel, name='cruces_panel'),
    path('cruces/new/', views.cruce_crear, name='cruce_crear'),
    path('cruces/<int:pk>/confirm/', views.cruce_confirmar, name='cruce_confirmar'),
    path('cruces/<int:pk>/add/', views.cruce_meter, name='cruce_meter'),
    path('cruces/<int:pk>/remove/', views.cruce_sacar, name='cruce_sacar'),
    path('cruces/<int:pk>/day/', views.cruce_cambiar_dia, name='cruce_cambiar_dia'),
    path('cruces/<int:pk>/cancel/', views.cruce_cancelar, name='cruce_cancelar'),

    # La orden de carga, el escaneo y la remision.
    path('cruces/<int:pk>/order/', views.orden_emitir, name='orden_emitir'),
    path('cruces/<int:pk>/verify/', views.carga_verificar, name='carga_verificar'),
    path('cruces/<int:pk>/verify/scan/', views.carga_escanear, name='carga_escanear'),
    path('cruces/<int:pk>/verify/undo/', views.carga_borrar_escaneo,
         name='carga_borrar_escaneo'),
    path('cruces/<int:pk>/remision/', views.remision_emitir, name='remision_emitir'),
    # Lo que contesta el QR del pie de una hoja impresa.
    path('orden/<int:pk>/v<int:version>/', views.orden_vigencia,
         name='orden_vigencia'),
]