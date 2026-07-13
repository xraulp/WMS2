from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_POST, require_GET
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.utils import timezone
from django.db.models import Q
from io import BytesIO
import os, json, zipfile

from .models import WarehouseOperation, Catalog, OperationDocument, UserProfile, DeletionLog
from .utils import generate_pdf_report, generate_label_pdf

now_local = timezone.localtime(timezone.now())
generated_at = now_local.strftime('%Y-%m-%d %H:%M')


# ── PERMISSION HELPERS ────────────────────────────────────────────────────────

def get_profile(user):
    try:
        return user.profile
    except UserProfile.DoesNotExist:
        role = 'superadmin' if user.is_superuser else 'manager'
        profile = UserProfile.objects.create(user=user, role=role)
        return profile

def is_home(user):
    return get_profile(user).is_home()

def is_customer_user(user):
    return get_profile(user).is_customer()

def customer_ops_filter(user, qs):
    profile = get_profile(user)
    if not profile.is_customer():
        return qs
    if profile.customer:
        return qs.filter(
            Q(customer=profile.customer) |
            Q(customer_name_manual__iexact=profile.customer.name)
        )
    return qs.none()


# ── AUTH ──────────────────────────────────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        user = authenticate(request,
                            username=request.POST.get('username'),
                            password=request.POST.get('password'))
        if user:
            login(request, user)
            if request.headers.get('HX-Request'):
                return HttpResponse(headers={'HX-Redirect': '/dashboard/'})
            return redirect('dashboard')
        if request.headers.get('HX-Request'):
            return render(request, 'warehouse/partials/login_error.html')
        return render(request, 'warehouse/login.html', {'error': 'Invalid credentials.'})
    return render(request, 'warehouse/login.html')


def logout_view(request):
    logout(request)
    return redirect('login')


# ── DASHBOARD ─────────────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    profile = get_profile(request.user)

    # Obtener lista de usuarios para el filtro (solo para superadmin/home/manager)
    users = []
    if profile.is_superadmin() or profile.is_home() or profile.is_manager():
        users = User.objects.filter(is_active=True).order_by('username')
    else:
        users = User.objects.filter(pk=request.user.pk)

    def cat_json(category):
        return json.dumps([
            {'id': e.pk, 'name': e.name}
            for e in Catalog.objects.filter(category=category, active=True).order_by('name')
        ])

    ops = WarehouseOperation.objects.select_related(
        'customer', 'shipper', 'carrier', 'bundle_type', 'created_by').all()
    ops = customer_ops_filter(request.user, ops)[:200]

    context = {
        'operations': ops,
        'catalog_entries': Catalog.objects.filter(active=True).order_by('category', 'name'),
        'customers_json':    cat_json('CUSTOMER'),
        'shippers_json':     cat_json('SHIPPER'),
        'carriers_json':     cat_json('CARRIER'),
        'bundle_types_json': cat_json('BUNDLE_TYPE'),
        'active_tab': request.GET.get('tab', 'form'),
        'profile': profile,
        'is_home': profile.is_home(),
        'users': users,
    }
    return render(request, 'warehouse/dashboard.html', context)


# ── OPERATIONS BY USER ─────────────────────────────────────────────────────────

@login_required
@require_GET
def operations_by_user(request, user_id):
    """Filtrar operaciones creadas por un usuario específico"""
    profile = get_profile(request.user)
    if not profile.is_superadmin() and not profile.is_home() and not profile.is_manager():
        return HttpResponse('Permission denied.', status=403)

    target_user = get_object_or_404(User, pk=user_id)
    ops = WarehouseOperation.objects.filter(created_by=target_user).select_related(
        'customer', 'shipper', 'carrier', 'bundle_type', 'created_by')[:200]

    return render(request, 'warehouse/partials/operations_table.html', {
        'operations': ops,
        'is_home': profile.is_home(),
        'profile': profile,
        'filter_user': target_user.username,
    })


# ── EMAIL HELPERS ─────────────────────────────────────────────────────────────

def _build_subject(operation):
    parts = []
    customer = operation.get_customer_display()
    if customer and customer != '—':
        parts.append(customer)
    if operation.po_order:
        parts.append(f"PO: {operation.po_order}")
    parts.append('DYSER Group LLC')
    op_type = 'Recepcion de Mercancias' if operation.operation_type == 'ENTRY' else 'Salida de Mercancias'
    parts.append(op_type)
    if operation.custom_id:
        parts.append(str(operation.custom_id))
    return ' | '.join(parts)

def _get_cc_emails():
    cc = []
    for e in Catalog.objects.filter(category='CC_EMAIL', active=True).exclude(
            contact_email__isnull=True).exclude(contact_email=''):
        for addr in e.contact_email.split(','):
            addr = addr.strip()
            if addr: cc.append(addr)
    return cc

def _send_operation_email(operation, message_body=''):
    # Obtener el string completo de emails (puede ser "a@mail.com, b@mail.com")
    email_raw = operation.get_customer_email_raw()
    if not email_raw:
        return False, 'no_email'

    # Dividir por comas y limpiar espacios
    emails = [email.strip() for email in email_raw.split(',') if email.strip()]

    if not emails:
        return False, 'no_email'

    try:
        pdf_buffer = generate_pdf_report(operation)
        html_body = render_to_string('warehouse/email/report_email.html',
                                      {'operation': operation, 'message_body': message_body})
        email = EmailMessage(
            subject=_build_subject(operation),
            body=html_body,
            to=emails,
            cc=_get_cc_emails(),
        )
        email.content_subtype = 'html'
        email.attach(f'{operation.custom_id}.pdf', pdf_buffer, 'application/pdf')
        for doc in operation.documents.all():
            try: email.attach_file(doc.file.path)
            except: pass
        email.send()
        operation.email_sent = True
        operation.email_sent_at = timezone.now()
        operation.save(update_fields=['email_sent', 'email_sent_at'])
        return True, None
    except Exception as e:
        return False, str(e)

def _send_whatsapp(operation):
    from django.conf import settings
    wa_number = operation.get_customer_whatsapp()
    if not wa_number:
        return
    sid   = getattr(settings, 'TWILIO_ACCOUNT_SID', '')
    token = getattr(settings, 'TWILIO_AUTH_TOKEN', '')
    from_  = getattr(settings, 'TWILIO_WHATSAPP_FROM', '')
    if not (sid and token and from_):
        return
    try:
        from twilio.rest import Client
        op_type = 'Recep de Mercancias' if operation.operation_type == 'ENTRY' else 'Salida de Mercancias'
        msg = (f"*DYSER GROUP — {op_type}*\n"
               f"ID: {operation.custom_id}\n"
               f"Date: {operation.date}\n"
               f"Customer: {operation.get_customer_display()}\n"
               f"Shipper: {operation.get_shipper_display()}\n"
               f"Po/Order: {operation.po_order or '—'}\n"
               f"Description: {operation.description or '—'}\n"
               f"Bundles: {operation.bundle_qty or '—'}\n"
               f"Weight: {operation.weight_lbs or '—'} LBS")
        Client(sid, token).messages.create(
            body=msg,
            from_=from_,
            to=f'whatsapp:{wa_number}'
        )
    except Exception:
        pass


# ── OPERATION CREATE ──────────────────────────────────────────────────────────

@login_required
@require_POST
def operation_create(request):
    profile = get_profile(request.user)
    if not profile.can_create_operations():
        return HttpResponse('<div class="msg-error">✗ Permission denied.</div>', status=422)

    p = request.POST
    op_type  = p.get('operation_type', '').strip()
    date_str = p.get('date', '').strip()

    if op_type not in ('ENTRY', 'EXIT'):
        return HttpResponse('<div class="msg-error">✗ Type of Operation is required.</div>', status=422)
    if not date_str:
        return HttpResponse('<div class="msg-error">✗ Date is required.</div>', status=422)

    from datetime import datetime
    try:
        op_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return HttpResponse('<div class="msg-error">✗ Invalid date format.</div>', status=422)

    def get_catalog(pk_str, category):
        try:
            return Catalog.objects.get(pk=int(pk_str), category=category, active=True)
        except (ValueError, TypeError, Catalog.DoesNotExist):
            return None

    def to_int(val):
        try: return int(val) if val and str(val).strip() else None
        except: return None

    def to_dec(val):
        try:
            from decimal import Decimal
            return Decimal(val) if val and str(val).strip() else None
        except: return None

    customer_obj = get_catalog(p.get('customer_id'), 'CUSTOMER')
    customer_manual = p.get('customer_text', '').strip() if not customer_obj else ''

    required_errors = []
    if not customer_obj and not customer_manual:
        required_errors.append('Customer')
    if not p.get('shipper_id') and not p.get('shipper_text','').strip():
        required_errors.append('Shipper')
    if not p.get('carrier_id') and not p.get('carrier_text','').strip():
        required_errors.append('Carrier')
    if not p.get('bundle_type_id') and not p.get('bundle_type_text','').strip():
        required_errors.append('Bundle Type')
    if not p.get('bundle_qty','').strip():
        required_errors.append('Bundle Qty')
    if not p.get('weight_lbs','').strip() and not p.get('weight_kgs','').strip():
        required_errors.append('Weight (LBS or KGS)')
    if not p.get('description','').strip():
        required_errors.append('Description')

    if required_errors:
        fields = ', '.join(required_errors)
        err_html = (
            '<div class="msg-error" id="op-err" '
            'style="display:flex;align-items:flex-start;justify-content:space-between;gap:10px">'
            '<span>&#10007; Required fields missing: <strong>' + fields + '</strong></span>'
            '<button type="button" onclick="this.parentElement.style.display=\'none\'" '
            'style="background:none;border:none;cursor:pointer;font-size:18px;color:#991b1b;'
            'padding:0 4px;flex-shrink:0;line-height:1">&#x2715;</button>'
            '</div>'
        )
        return HttpResponse(err_html, status=422)

    shipper_obj     = get_catalog(p.get('shipper_id'),     'SHIPPER')
    carrier_obj     = get_catalog(p.get('carrier_id'),     'CARRIER')
    bundle_type_obj = get_catalog(p.get('bundle_type_id'), 'BUNDLE_TYPE')

    op = WarehouseOperation(
        date=op_date, operation_type=op_type,
        entry_dispatched=p.get('entry_dispatched', '').strip(),
        customer=customer_obj, customer_name_manual=customer_manual,
        shipper=shipper_obj,
        shipper_name_manual=p.get('shipper_text','').strip() if not shipper_obj else '',
        invoice=p.get('invoice','').strip(), po_order=p.get('po_order','').strip(),
        seal=p.get('seal','').strip(), carrier=carrier_obj,
        carrier_name_manual=p.get('carrier_text','').strip() if not carrier_obj else '',
        pro=p.get('pro','').strip(), trailer=p.get('trailer','').strip(),
        bundle_type=bundle_type_obj,
        bundle_type_manual=p.get('bundle_type_text','').strip() if not bundle_type_obj else '',
        bundle_qty=to_int(p.get('bundle_qty')),
        weight_lbs=to_dec(p.get('weight_lbs')), weight_kgs=to_dec(p.get('weight_kgs')),
        description=p.get('description','').strip(), note=p.get('note','').strip(),
        damage=bool(p.get('damage')), damage_description=p.get('damage_description','').strip(),
        created_by=request.user,
        # NUEVOS CAMPOS
        ref_aa=p.get('ref_aa', '').strip(),
        ref_dys=p.get('ref_dys', '').strip(),
        pedimento=p.get('pedimento', '').strip(),
    )
    op.save()

    def guess_type(name):
        ext = name.rsplit('.',1)[-1].lower() if '.' in name else ''
        if ext in ('jpg','jpeg','png','gif','webp','heic'): return 'PHOTO'
        if ext in ('mp4','mov','avi','mkv','webm'):         return 'VIDEO'
        if ext in ('pdf','doc','docx','xls','xlsx','csv'): return 'DOCUMENT'
        return 'OTHER'

    for f in request.FILES.getlist('photos'):
        OperationDocument.objects.create(
            operation=op, file_type=guess_type(f.name),
            file=f, original_name=f.name, uploaded_by=request.user)
    for f in request.FILES.getlist('documents'):
        OperationDocument.objects.create(
            operation=op, file_type=guess_type(f.name),
            file=f, original_name=f.name, uploaded_by=request.user)

    if op.operation_type == 'EXIT' and op.entry_dispatched:
        for eid in [x.strip() for x in op.entry_dispatched.split(',') if x.strip()]:
            try:
                entry_op = WarehouseOperation.objects.get(custom_id=eid)
                entry_op.entry_dispatched = op.custom_id
                entry_op.save(update_fields=['entry_dispatched'])
            except WarehouseOperation.DoesNotExist:
                pass

    send_wa = p.get('send_whatsapp') == '1'
    email_sent, email_error = _send_operation_email(op)
    if send_wa:
        _send_whatsapp(op)

    smtp_not_configured = email_error and any(
        x in str(email_error) for x in ['getaddrinfo','Connection refused','tuservidor'])
    if smtp_not_configured:
        email_sent, email_error = False, 'smtp_not_configured'

    ops = WarehouseOperation.objects.select_related(
        'customer','shipper','carrier','bundle_type').all()
    ops = customer_ops_filter(request.user, ops)[:200]
    return render(request, 'warehouse/partials/operation_success.html', {
        'operation': op, 'operations': ops,
        'email_sent': email_sent, 'email_error': email_error,
        'has_whatsapp': bool(op.get_customer_whatsapp()),
    })


# ── OPERATION DETAIL / DELETE / PDF / LABEL ───────────────────────────────────

@login_required
def operation_detail(request, pk):
    op = get_object_or_404(WarehouseOperation, pk=pk)
    if is_customer_user(request.user):
        profile = get_profile(request.user)
        if profile.customer and op.customer != profile.customer:
            return HttpResponse('Permission denied.', status=403)

    fields = [
        ('Date',             op.date.strftime('%Y-%m-%d')),
        ('Type',             op.get_operation_type_display()),
        ('Custom ID',        op.custom_id),
        ('Status',           op.status),
        ('Customer',         op.get_customer_display()),
        ('Shipper',          op.get_shipper_display()),
        ('Entries Disp.',    op.entry_dispatched),
        ('Invoice',          op.invoice), ('PO / Order', op.po_order),
        ('Seal',             op.seal),    ('Carrier',    op.get_carrier_display()),
        ('PRO',              op.pro),     ('Trailer',    op.trailer),
        ('Bundle Type',      op.get_bundle_type_display_name()),
        ('Bundle Qty',       op.bundle_qty),
        ('Weight LBS',       op.weight_lbs), ('Weight KGS', op.weight_kgs),
        ('Description',      op.description), ('Note', op.note),
        ('Customer Notes',   op.customer_notes),
        ('Damage',           '⚠ YES' if op.damage else 'No'),
        ('Email Sent',       op.email_sent_at.strftime('%Y-%m-%d') if op.email_sent else 'Not sent'),
        ('Created By',       op.created_by.username if op.created_by else '—'),
        ('REF AA',           op.ref_aa or '—'),
        ('DYS',              op.ref_dys or '—'),
        ('PEDIMENTO',        op.pedimento or '—'),
    ]
    return render(request, 'warehouse/partials/operation_detail.html', {
        'operation': op, 'fields': fields,
        'customer_email': op.get_customer_email() or '',
        'email_subject':  _build_subject(op),
        'is_home':        is_home(request.user),
        'profile':        get_profile(request.user),
    })


@login_required
def operation_delete(request, pk):
    profile = get_profile(request.user)
    if not profile.can_delete():
        return HttpResponse('Permission denied.', status=403)
    op = get_object_or_404(WarehouseOperation, pk=pk)
    if request.method == 'POST':
        _log_deletion(op, request.user)
        op.delete()
        ops = WarehouseOperation.objects.select_related(
            'customer','shipper','carrier','bundle_type').all()
        ops = customer_ops_filter(request.user, ops)[:200]
        return render(request, 'warehouse/partials/operations_table.html',
                      {'operations': ops, 'is_home': is_home(request.user),
                       'profile': profile})
    return HttpResponse(status=405)


def _log_deletion(op, user):
    DeletionLog.objects.create(
        deleted_by=user,
        custom_id=op.custom_id,
        operation_type=op.operation_type,
        operation_date=op.date,
        customer_name=op.get_customer_display(),
        description=op.description or '',
    )


@login_required
def operation_delete_confirm(request, pk):
    profile = get_profile(request.user)
    if not profile.can_delete():
        return HttpResponse('Permission denied.', status=403)
    if request.method != 'POST':
        return HttpResponse(status=405)

    password = request.POST.get('confirm_password','')
    op = get_object_or_404(WarehouseOperation, pk=pk)
    ops_qs = WarehouseOperation.objects.select_related(
        'customer','shipper','carrier','bundle_type').all()
    ops_qs = customer_ops_filter(request.user, ops_qs)[:200]

    # Superadmin/manager can delete any record
    # Staff: no delete (already blocked above)
    # Check: if user has a custom delete_password set, use it; else fall back to login password
    if profile.delete_password:
        # Custom delete password set — use it
        if password != profile.delete_password:
            # Also allow login password as fallback for managers/superadmins
            user_auth = authenticate(request, username=request.user.username, password=password)
            if not (user_auth and profile.is_manager()):
                return render(request, 'warehouse/partials/operations_table.html', {
                    'operations': ops_qs,
                    'delete_error': f'Incorrect password. Record #{pk} was NOT deleted.',
                    'is_home': is_home(request.user), 'profile': profile,
                })
    else:
        # No custom delete password — authenticate via login password
        user_auth = authenticate(request, username=request.user.username, password=password)
        if not user_auth:
            # For superadmin, also try comparing against all users' passwords
            # If the user is superadmin, check if password matches any manager
            if profile.is_superadmin():
                # Check if password matches any user's delete_password or any manager
                found = False
                for up in UserProfile.objects.filter(role__in=['superadmin','manager']):
                    if up.delete_password and password == up.delete_password:
                        found = True
                        break
                if not found:
                    return render(request, 'warehouse/partials/operations_table.html', {
                        'operations': ops_qs,
                        'delete_error': f'Incorrect password. Record #{pk} was NOT deleted.',
                        'is_home': is_home(request.user), 'profile': profile,
                    })
            else:
                return render(request, 'warehouse/partials/operations_table.html', {
                    'operations': ops_qs,
                    'delete_error': f'Incorrect password. Record #{pk} was NOT deleted.',
                    'is_home': is_home(request.user), 'profile': profile,
                })

    # Check ownership restriction: non-superadmin managers can only delete own records
    if not profile.is_superadmin() and op.created_by and op.created_by != request.user:
        if not profile.is_superadmin():
            return render(request, 'warehouse/partials/operations_table.html', {
                'operations': ops_qs,
                'delete_error': f'You can only delete records you created. Record #{pk} was NOT deleted.',
                'is_home': is_home(request.user), 'profile': profile,
            })

    _log_deletion(op, request.user)
    op.delete()
    return render(request, 'warehouse/partials/operations_table.html', {
        'operations': ops_qs, 'delete_success': 'Record deleted successfully.',
        'is_home': is_home(request.user), 'profile': profile,
    })


@login_required
def operation_pdf(request, pk):
    op = get_object_or_404(WarehouseOperation, pk=pk)
    if is_customer_user(request.user):
        profile = get_profile(request.user)
        if profile.customer and op.customer != profile.customer:
            return HttpResponse('Permission denied.', status=403)
    pdf = generate_pdf_report(op)
    resp = HttpResponse(pdf, content_type='application/pdf')
    resp['Content-Disposition'] = f'inline; filename="{op.custom_id}.pdf"'
    return resp


@login_required
def operation_label(request, pk):
    op = get_object_or_404(WarehouseOperation, pk=pk)
    pdf = generate_label_pdf(op)
    resp = HttpResponse(pdf, content_type='application/pdf')
    resp['Content-Disposition'] = f'inline; filename="{op.custom_id}_label.pdf"'
    return resp


@login_required
def operation_download_all(request, pk):
    op = get_object_or_404(WarehouseOperation, pk=pk)
    docs = op.documents.all()

    if not docs.exists():
        return HttpResponse('No files attached.', status=404)

    # Obtener abreviatura del customer
    customer_abbr = get_customer_abbreviation(op)

    # Construir el prefijo del nombre del archivo
    # Formato: ABREV PO/ORDER INVOICE CUSTOM_ID REF_AA REF_DYS PEDIMENTO
    name_parts = [customer_abbr]

    if op.po_order:
        name_parts.append(op.po_order)
    if op.invoice:
        name_parts.append(op.invoice)
    if op.custom_id:
        name_parts.append(op.custom_id)
    if op.ref_aa:
        name_parts.append(op.ref_aa)
    if op.ref_dys:
        name_parts.append(op.ref_dys)
    if op.pedimento:
        name_parts.append(op.pedimento)

    base_name = ' '.join(name_parts).replace('/', '_')

    # Agrupar archivos por tipo (extensión)
    files_by_type = {}
    for doc in docs:
        ext = os.path.splitext(doc.original_name or doc.file.name)[1].lower()
        if ext not in files_by_type:
            files_by_type[ext] = []
        files_by_type[ext].append(doc)

    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for ext, file_list in files_by_type.items():
            for idx, doc in enumerate(file_list, 1):
                new_filename = f"{base_name} {idx}{ext}"
                try:
                    zf.write(doc.file.path, new_filename)
                except Exception as e:
                    print(f"Error adding {doc.file.path}: {e}")

    buf.seek(0)
    resp = HttpResponse(buf.read(), content_type='application/zip')
    resp['Content-Disposition'] = f'attachment; filename="{base_name}.zip"'
    return resp


def get_customer_abbreviation(operation):
    """Obtiene la abreviatura del customer"""
    # Opción A: Desde el modelo Catalog (si agregaste el campo abbreviation)
    if operation.customer and hasattr(operation.customer, 'abbreviation') and operation.customer.abbreviation:
        return operation.customer.abbreviation.upper()

    # Opción B: Desde archivo de configuraciones (opcional)
    try:
        from .customer_abbreviations import CUSTOMER_ABBREVIATIONS
        customer_name = operation.get_customer_display()
        if customer_name in CUSTOMER_ABBREVIATIONS:
            return CUSTOMER_ABBREVIATIONS[customer_name]
    except ImportError:
        pass

    # Fallback: primeras 4 letras del nombre
    customer_name = operation.get_customer_display()
    return customer_name[:4].upper() if customer_name and customer_name != '—' else 'UNKN'


@login_required
@require_POST
def operation_send_email(request, pk):
    op        = get_object_or_404(WarehouseOperation, pk=pk)
    recipient = request.POST.get('recipient_email','').strip()
    subject   = request.POST.get('subject', _build_subject(op))
    message   = request.POST.get('message','')
    if not recipient:
        return HttpResponse('<div class="msg-error">✗ Recipient email required.</div>')
    try:
        pdf = generate_pdf_report(op)
        html_body = render_to_string('warehouse/email/report_email.html',
                                     {'operation': op, 'message_body': message})
        email = EmailMessage(subject=subject, body=html_body,
                             to=[recipient], cc=_get_cc_emails())
        email.content_subtype = 'html'
        email.attach(f'{op.custom_id}.pdf', pdf, 'application/pdf')
        for doc in op.documents.all():
            try: email.attach_file(doc.file.path)
            except: pass
        email.send()
        op.email_sent = True; op.email_sent_at = timezone.now()
        op.save(update_fields=['email_sent','email_sent_at'])
        return HttpResponse(f'<div class="msg-success">✓ Report sent to {recipient}.</div>')
    except Exception as e:
        return HttpResponse(f'<div class="msg-error">✗ Email failed: {e}</div>')


@login_required
@require_POST
def operation_send_whatsapp(request, pk):
    """Send WhatsApp message for a specific operation."""
    op = get_object_or_404(WarehouseOperation, pk=pk)
    if is_customer_user(request.user):
        profile = get_profile(request.user)
        if profile.customer and op.customer != profile.customer:
            return HttpResponse('Permission denied.', status=403)
    _send_whatsapp(op)
    wa = op.get_customer_whatsapp()
    if wa:
        return HttpResponse(f'<div class="msg-success">✓ WhatsApp sent to {wa}.</div>')
    return HttpResponse('<div class="msg-error">✗ No WhatsApp number for this customer.</div>')


# ── SEARCH ────────────────────────────────────────────────────────────────────

@login_required
@require_GET
def operations_search(request):
    q   = request.GET.get('q','').strip()
    status_filter = request.GET.get('status','').strip()
    ops = WarehouseOperation.objects.select_related(
        'customer','shipper','carrier','bundle_type').all()
    ops = customer_ops_filter(request.user, ops)
    if q:
        ops = ops.filter(
            Q(custom_id__icontains=q) | Q(operation_type__icontains=q) |
            Q(customer__name__icontains=q) | Q(customer_name_manual__icontains=q) |
            Q(shipper__name__icontains=q) | Q(carrier__name__icontains=q) |
            Q(invoice__icontains=q) | Q(po_order__icontains=q) |
            Q(pro__icontains=q) | Q(trailer__icontains=q) |
            Q(description__icontains=q) | Q(date__icontains=q)
        )
    ops_list = list(ops[:500])
    if status_filter in ('Released Goods', 'In Warehouse'):
        ops_list = [o for o in ops_list if o.status == status_filter]
    profile = get_profile(request.user)
    return render(request, 'warehouse/partials/operations_table.html',
                  {'operations': ops_list[:200], 'search_query': q,
                   'is_home': is_home(request.user), 'profile': profile})


# ── FREE ENTRIES ──────────────────────────────────────────────────────────────

@login_required
@require_GET
def free_entries(request):
    customer_id = request.GET.get('customer_id','').strip()
    if not customer_id:
        return JsonResponse([], safe=False)
    try:
        cid = int(customer_id)
    except (ValueError, TypeError):
        return JsonResponse([], safe=False)
    ops = WarehouseOperation.objects.filter(
        operation_type='ENTRY', customer_id=cid,
    ).filter(Q(entry_dispatched__isnull=True)|Q(entry_dispatched='')).order_by('-date')
    return JsonResponse(
        [{'custom_id': op.custom_id, 'date': str(op.date), 'po_order': op.po_order or ''} for op in ops[:100]],
        safe=False)


# ── DIGITAL TAB ───────────────────────────────────────────────────────────────

@login_required
def digital_search(request):
    q  = request.GET.get('q','').strip()
    op = None
    if q:
        try:
            op = WarehouseOperation.objects.get(custom_id__iexact=q)
            if is_customer_user(request.user):
                profile = get_profile(request.user)
                if profile.customer and op.customer != profile.customer:
                    op = None
        except WarehouseOperation.DoesNotExist:
            op = None
    profile = get_profile(request.user)
    return render(request, 'warehouse/partials/digital_panel.html',
                  {'operation': op, 'query': q, 'is_home': is_home(request.user),
                   'profile': profile})


@login_required
@require_POST
def digital_upload(request, pk):
    op = get_object_or_404(WarehouseOperation, pk=pk)
    if is_customer_user(request.user):
        profile = get_profile(request.user)
        if profile.customer and op.customer != profile.customer:
            return HttpResponse('Permission denied.', status=403)

    from datetime import date as date_cls
    today_str = date_cls.today().strftime('%d%m%y')
    existing_today = OperationDocument.objects.filter(
        digital_name__startswith=today_str).count()

    uploaded = []
    for f in request.FILES.getlist('files'):
        consecutive = existing_today + len(uploaded) + 1
        digital_name = f'{today_str}-{consecutive}'
        ext = f.name.rsplit('.',1)[-1].lower() if '.' in f.name else ''
        if ext in ('jpg','jpeg','png','gif','webp','heic'):  ftype = 'PHOTO'
        elif ext in ('mp4','mov','avi','mkv','webm'):         ftype = 'VIDEO'
        elif ext in ('pdf','doc','docx','xls','xlsx','csv'): ftype = 'DOCUMENT'
        else:                                                  ftype = 'OTHER'

        doc = OperationDocument.objects.create(
            operation=op, file_type=ftype, file=f,
            original_name=f.name, digital_name=digital_name,
            uploaded_by=request.user)
        uploaded.append(doc)

    profile = get_profile(request.user)
    return render(request, 'warehouse/partials/digital_panel.html', {
        'operation': op, 'query': op.custom_id,
        'is_home': is_home(request.user),
        'profile': profile,
        'upload_success': f'{len(uploaded)} file(s) uploaded.',
    })

@login_required
def digital_delete_file(request, doc_pk):
    # Verificar que el usuario tenga permiso para eliminar
    profile = get_profile(request.user)
    if not profile.can_delete():
        return HttpResponse('Permission denied.', status=403)

    # Obtener la contraseña del request
    if request.method == 'POST':
        password = request.POST.get('confirm_password', '')
    else:
        password = request.GET.get('password', '')

    # 🔐 SOLO VERIFICAR delete_password (NO la contraseña de login)
    password_valid = False

    # Solo verificar si tiene delete_password personalizado
    if profile.delete_password and password == profile.delete_password:
        password_valid = True

    if not password_valid:
        # Contraseña incorrecta
        doc = get_object_or_404(OperationDocument, pk=doc_pk)
        op = doc.operation
        return render(request, 'warehouse/partials/digital_panel.html', {
            'operation': op, 'query': op.custom_id,
            'is_home': profile.is_home(),
            'profile': profile,
            'upload_error': '❌ Contraseña de eliminación incorrecta. No se pudo eliminar el archivo.',
        })

    # ✅ Contraseña correcta - proceder a eliminar
    doc = get_object_or_404(OperationDocument, pk=doc_pk)
    op = doc.operation
    try:
        if os.path.exists(doc.file.path):
            os.remove(doc.file.path)
    except:
        pass
    doc.delete()

    return render(request, 'warehouse/partials/digital_panel.html', {
        'operation': op, 'query': op.custom_id,
        'is_home': profile.is_home(),
        'profile': profile,
        'upload_success': '✓ Archivo eliminado correctamente.',
    })

@login_required
@require_POST
def digital_delete_multiple(request):
    """Elimina múltiples archivos de Digital con verificación de contraseña."""
    profile = get_profile(request.user)
    if not profile.can_delete():
        return HttpResponse('Permission denied.', status=403)

    ids_str = request.POST.get('ids', '').strip()
    if not ids_str:
        return HttpResponse('<div class="msg-error">✗ No se seleccionaron archivos.</div>')

    doc_ids = [int(x.strip()) for x in ids_str.split(',') if x.strip().isdigit()]
    if not doc_ids:
        return HttpResponse('<div class="msg-error">✗ IDs inválidos.</div>')

    password = request.POST.get('confirm_password', '')
    password_valid = False
    if profile.delete_password and password == profile.delete_password:
        password_valid = True

    if not password_valid:
        return HttpResponse('<div class="msg-error">❌ Contraseña de eliminación incorrecta.</div>')

    # Obtener los documentos
    docs = OperationDocument.objects.filter(pk__in=doc_ids)
    if not docs.exists():
        return HttpResponse('<div class="msg-error">✗ Ninguno de los archivos seleccionados existe.</div>')

    # Obtener la operación del primer documento (asumimos que todos son de la misma operación)
    op = docs.first().operation
    deleted_count = 0
    errors = []

    for doc in docs:
        # Verificar permisos
        if not (profile.is_superadmin() or profile.is_home() or profile.is_manager()):
            if doc.operation.created_by != request.user:
                errors.append(f'No tienes permiso para eliminar {doc.original_name}')
                continue
        try:
            # Eliminar archivo físico
            if doc.file and os.path.exists(doc.file.path):
                os.remove(doc.file.path)
            doc.delete()
            deleted_count += 1
        except Exception as e:
            errors.append(f'{doc.original_name}: {str(e)}')

    # Construir contexto
    context = {
        'operation': op,
        'query': op.custom_id if op else '',
        'is_home': profile.is_home(),
        'profile': profile,
    }

    if deleted_count > 0:
        context['upload_success'] = f'✓ {deleted_count} archivo(s) eliminado(s) correctamente.'
    if errors:
        context['upload_error'] = '⚠️ Algunos archivos no se pudieron eliminar: ' + '; '.join(errors[:3])

    return render(request, 'warehouse/partials/digital_panel.html', context)

# ── REPORT GENERATOR ──────────────────────────────────────────────────────────

@login_required
def report_generator(request):
    customers = Catalog.objects.filter(category='CUSTOMER', active=True).order_by('name')
    profile   = get_profile(request.user)

    users = []
    if profile.is_superadmin() or profile.is_home():
        users = User.objects.filter(is_active=True).order_by('username')
    elif profile.is_manager() or profile.is_staff_role():
        users = User.objects.filter(pk=request.user.pk)
    else:
        users = User.objects.filter(pk=request.user.pk)

    if profile.is_customer() and profile.customer:
        customers = customers.filter(pk=profile.customer.pk)

    results  = None
    filters  = {}
    error    = None

    if request.GET.get('search'):
        all_customers  = request.GET.get('all_customers', '')
        customer_ids   = request.GET.getlist('customer_ids')
        created_by_id  = request.GET.get('created_by', '').strip()

        if not customer_ids and not all_customers:
            error = 'Please select at least one customer or choose All Customers.'
        else:
            ops = WarehouseOperation.objects.select_related(
                'customer', 'shipper', 'carrier', 'bundle_type', 'created_by').all()
            ops = customer_ops_filter(request.user, ops)

            date_from    = request.GET.get('date_from', '').strip()
            date_to      = request.GET.get('date_to', '').strip()
            op_type      = request.GET.get('op_type', '').strip()
            undispatched = request.GET.get('undispatched', '')
            status_f     = request.GET.get('status_filter', '').strip()

            if created_by_id and created_by_id.isdigit():
                ops = ops.filter(created_by_id=int(created_by_id))
                filters['created_by_id'] = created_by_id
                try:
                    creator = User.objects.get(pk=int(created_by_id))
                    filters['created_by_name'] = creator.username
                except User.DoesNotExist:
                    pass

            if all_customers:
                filters['all_customers'] = True
                filters['customer_label'] = 'All Customers'
                filters['customer_ids_list'] = []
            elif customer_ids:
                cid_ints = [int(x) for x in customer_ids if x.isdigit()]
                ops = ops.filter(customer_id__in=cid_ints)
                filters['customer_ids_list'] = customer_ids
                names = list(Catalog.objects.filter(pk__in=cid_ints).values_list('name', flat=True))
                filters['customer_label'] = ', '.join(names)
                if cid_ints:
                    filters['customer_id'] = str(cid_ints[0])

            if date_from:
                ops = ops.filter(date__gte=date_from); filters['date_from'] = date_from
            if date_to:
                ops = ops.filter(date__lte=date_to);   filters['date_to']   = date_to
            if op_type in ('ENTRY', 'EXIT'):
                ops = ops.filter(operation_type=op_type); filters['op_type'] = op_type
            if undispatched == '1':
                ops = ops.filter(operation_type='ENTRY').filter(
                    Q(entry_dispatched__isnull=True) | Q(entry_dispatched=''))
                filters['undispatched'] = True
            if status_f:
                filters['status_filter'] = status_f

            results_list = list(ops.order_by('-date')[:500])
            if status_f in ('Released Goods', 'In Warehouse'):
                results_list = [o for o in results_list if o.status == status_f]
            results = results_list

    return render(request, 'warehouse/partials/report_generator.html', {
        'customers': customers,
        'results': results,
        'filters': filters,
        'is_home': is_home(request.user),
        'error': error,
        'profile': profile,
        'users': users,
    })


@login_required
def report_generator_pdf(request):
    from .utils import generate_operations_report_pdf
    ops_ids = request.GET.get('ids','').split(',')
    ops = WarehouseOperation.objects.filter(pk__in=[i for i in ops_ids if i]).select_related(
        'customer','shipper','carrier','bundle_type', 'created_by')
    ops = customer_ops_filter(request.user, ops)
    title   = request.GET.get('title', 'Operations Report')
    pdf     = generate_operations_report_pdf(list(ops), title)
    resp    = HttpResponse(pdf, content_type='application/pdf')
    resp['Content-Disposition'] = f'inline; filename="report.pdf"'
    return resp


@login_required
@require_POST
def report_generator_email(request):
    from .utils import generate_operations_report_pdf
    ids_raw          = request.POST.get('ids', '').strip().rstrip(',')
    title            = request.POST.get('title', 'Operations Report')
    extra_emails_str = request.POST.get('extra_emails', '').strip()
    customer_id      = request.POST.get('customer_id', '').strip()
    all_customers    = request.POST.get('all_customers', '') == '1'

    pk_list = []
    for i in ids_raw.split(','):
        i = i.strip()
        if i.isdigit():
            pk_list.append(int(i))

    if not pk_list:
        return HttpResponse('<div class="msg-error">✗ No records selected.</div>')

    ops = WarehouseOperation.objects.filter(pk__in=pk_list).select_related(
        'customer', 'shipper', 'carrier', 'bundle_type', 'created_by').order_by('-date')
    ops = customer_ops_filter(request.user, ops)
    ops_list = list(ops)

    if not ops_list:
        return HttpResponse('<div class="msg-error">✗ No records found.</div>')

    recipients = []

    if not all_customers and customer_id and customer_id.isdigit():
        try:
            cat = Catalog.objects.get(pk=int(customer_id))
            if cat.contact_email:
                for addr in cat.contact_email.split(','):
                    addr = addr.strip()
                    if addr and addr not in recipients:
                        recipients.append(addr)
        except (Catalog.DoesNotExist, ValueError):
            pass

    if extra_emails_str:
        for em in extra_emails_str.split(','):
            em = em.strip()
            if em and em not in recipients:
                recipients.append(em)

    if (all_customers or (customer_id and ',' in customer_id)) and not recipients:
        return HttpResponse('<div class="msg-error">✗ Para múltiples clientes, debes ingresar al menos un email en el campo "Email address".</div>')

    if not recipients:
        return HttpResponse('<div class="msg-error">✗ No hay destinatarios. Ingresa un email en el campo correspondiente.</div>')

    try:
        pdf = generate_operations_report_pdf(ops_list, title)
        email = EmailMessage(
            subject=title,
            body=f'Adjunto encontrará el reporte de operaciones solicitado.\n\n'
                 f'Registros incluidos: {len(ops_list)}\n'
                 f'Fecha y hora de generación: {generated_at} (Hora Central)\n\n'
                 f'Saludos cordiales,\n'
                 f'DYSER Group LLC',
            to=recipients,
            cc=_get_cc_emails(),
        )
        email.attach('report.pdf', pdf, 'application/pdf')
        email.send()
        return HttpResponse(
            f'<div class="msg-success">✓ Reporte con {len(ops_list)} registro(s) enviado a {", ".join(recipients)}.</div>'
        )
    except Exception as e:
        return HttpResponse(f'<div class="msg-error">✗ Error al enviar: {e}</div>')


# ── CATALOG ───────────────────────────────────────────────────────────────────

@login_required
@require_POST
def catalog_create(request):
    profile = get_profile(request.user)
    if profile.is_customer():
        return HttpResponse('Permission denied.', status=403)
    p = request.POST
    category = p.get('category','').strip()
    name     = p.get('name','').strip()
    if not category or not name:
        return HttpResponse('<div class="msg-error">✗ Category and Name are required.</div>')
    entry = Catalog.objects.create(
        category=category, name=name,
        contact_email=p.get('contact_email','').strip(),
        phone=p.get('phone','').strip(),
        address=p.get('address','').strip(),
        notes=p.get('notes','').strip(),
        whatsapp=p.get('whatsapp','').strip(),
    )
    catalog_entries = Catalog.objects.filter(active=True).order_by('category','name')
    table_html = render_to_string('warehouse/partials/catalog_table.html',
                                  {'catalog_entries': catalog_entries}, request=request)
    return HttpResponse(
        f'<div class="msg-success">✓ {entry.name} ({entry.get_category_display()}) saved.</div>'
        f'<div id="catalog-table" hx-swap-oob="innerHTML">{table_html}</div>'
    )


@login_required
def catalog_edit(request, pk):
    profile = get_profile(request.user)
    if profile.is_customer():
        return HttpResponse('Permission denied.', status=403)
    entry = get_object_or_404(Catalog, pk=pk)
    if request.method == 'POST':
        p = request.POST
        entry.name          = p.get('name', entry.name).strip()
        entry.contact_email = p.get('contact_email', '').strip()
        entry.phone         = p.get('phone', '').strip()
        entry.address       = p.get('address', '').strip()
        entry.notes         = p.get('notes', '').strip()
        entry.whatsapp      = p.get('whatsapp', '').strip()
        entry.abbreviation  = p.get('abbreviation', '').strip().upper()   # ← AGREGAR ESTA LÍNEA
        entry.save()
        catalog_entries = Catalog.objects.filter(active=True).order_by('category','name')
        return render(request, 'warehouse/partials/catalog_table.html',
                      {'catalog_entries': catalog_entries, 'edit_success': f'{entry.name} updated.'})
    return render(request, 'warehouse/partials/catalog_edit_form.html', {'entry': entry})


@login_required
def catalog_delete(request, pk):
    profile = get_profile(request.user)
    if profile.is_customer():
        return HttpResponse('Permission denied.', status=403)
    entry = get_object_or_404(Catalog, pk=pk)
    if request.method == 'POST':
        entry.active = False
        entry.save()
        return render(request, 'warehouse/partials/catalog_table.html', {
            'catalog_entries': Catalog.objects.filter(active=True).order_by('category','name')
        })
    return HttpResponse(status=405)


@login_required
def catalog_list(request):
    return render(request, 'warehouse/partials/catalog_table.html', {
        'catalog_entries': Catalog.objects.filter(active=True).order_by('category','name')
    })


@login_required
def catalog_autocomplete(request):
    category = request.GET.get('category','')
    q        = request.GET.get('q','')
    entries  = Catalog.objects.filter(category=category, active=True)
    if q: entries = entries.filter(name__icontains=q)
    return JsonResponse([{'id': e.pk, 'name': e.name} for e in entries[:20]], safe=False)


# ── USER MANAGEMENT ───────────────────────────────────────────────────────────

@login_required
def user_management(request):
    profile = get_profile(request.user)
    if not profile.can_manage_users():
        return HttpResponse('Permission denied.', status=403)
    users    = User.objects.all().order_by('username')
    profiles = {p.user_id: p for p in UserProfile.objects.select_related('customer').all()}
    customers = Catalog.objects.filter(category='CUSTOMER', active=True).order_by('name')
    msg = ''
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create':
            uname = request.POST.get('username','').strip()
            pwd   = request.POST.get('password','').strip()
            role  = request.POST.get('role','staff')
            cid   = request.POST.get('customer_id','').strip()
            if uname and pwd:
                if not User.objects.filter(username=uname).exists():
                    u = User.objects.create_user(username=uname, password=pwd)
                    cat = Catalog.objects.get(pk=int(cid)) if cid else None
                    UserProfile.objects.create(user=u, role=role, customer=cat, plain_password=pwd)
                    msg = f'User "{uname}" created with role "{role}".'
                else:
                    msg = f'Username "{uname}" already exists.'
        elif action == 'delete':
            uid = request.POST.get('user_id')
            u   = get_object_or_404(User, pk=uid)
            if u != request.user:
                u.delete()
                msg = 'User deleted.'
        elif action == 'change_password':
            uid = request.POST.get('user_id')
            new_pwd = request.POST.get('new_password','').strip()
            if uid and new_pwd:
                u = get_object_or_404(User, pk=uid)
                u.set_password(new_pwd)
                u.save()
                p, _ = UserProfile.objects.get_or_create(user=u)
                p.plain_password = new_pwd
                p.save()
                msg = f'Password updated for "{u.username}".'
        elif action == 'update_role':
            uid  = request.POST.get('user_id')
            role = request.POST.get('role','staff')
            cid  = request.POST.get('customer_id','').strip()
            del_pwd = request.POST.get('delete_password','').strip()
            u    = get_object_or_404(User, pk=uid)
            p, _ = UserProfile.objects.get_or_create(user=u)
            p.role     = role
            p.customer = Catalog.objects.get(pk=int(cid)) if cid else None
            if del_pwd:
                p.delete_password = del_pwd
            p.save()
            msg = f'User "{u.username}" updated.'
        users    = User.objects.all().order_by('username')
        profiles = {p.user_id: p for p in UserProfile.objects.select_related('customer').all()}

    users    = User.objects.all().order_by('username')
    profiles = {p.user_id: p for p in UserProfile.objects.select_related('customer').all()}
    deletion_log = DeletionLog.objects.select_related('deleted_by').all()[:50]
    return render(request, 'warehouse/partials/user_management.html', {
        'users': users, 'profiles': profiles,
        'customers': customers, 'msg': msg,
        'request': request,
        'deletion_log': deletion_log,
    })


# ── DEBUG ─────────────────────────────────────────────────────────────────────

@login_required
def debug_catalog(request):
    def to_list(qs):
        return [{'id': e.pk, 'name': e.name} for e in qs]
    return JsonResponse({
        'customers':    to_list(Catalog.objects.filter(category='CUSTOMER',    active=True).order_by('name')),
        'shippers':     to_list(Catalog.objects.filter(category='SHIPPER',     active=True).order_by('name')),
        'carriers':     to_list(Catalog.objects.filter(category='CARRIER',     active=True).order_by('name')),
        'bundle_types': to_list(Catalog.objects.filter(category='BUNDLE_TYPE', active=True).order_by('name')),
    })


# ── MOBILE ────────────────────────────────────────────────────────────────────

@login_required
def mobile_dashboard(request):
    profile = get_profile(request.user)
    ops = WarehouseOperation.objects.select_related(
        'customer','shipper','carrier','bundle_type').all()
    ops = customer_ops_filter(request.user, ops)[:200]
    def cat_json(category):
        return json.dumps([
            {'id': e.pk, 'name': e.name}
            for e in Catalog.objects.filter(category=category, active=True).order_by('name')
        ])
    context = {
        'profile': profile,
        'is_home': profile.is_home(),
        'operations': ops,
        'customers_json':    cat_json('CUSTOMER'),
        'shippers_json':     cat_json('SHIPPER'),
        'carriers_json':     cat_json('CARRIER'),
        'bundle_types_json': cat_json('BUNDLE_TYPE'),
    }
    return render(request, 'warehouse/mobile.html', context)


@login_required
@require_GET
def exit_entry_totals(request):
    ids_str = request.GET.get('ids', '')
    if not ids_str:
        return JsonResponse({})
    custom_ids = [x.strip() for x in ids_str.split(',') if x.strip()]
    entries = WarehouseOperation.objects.filter(
        custom_id__in=custom_ids, operation_type='ENTRY'
    ).select_related('shipper', 'bundle_type')

    total_qty  = 0
    total_lbs  = 0
    total_kgs  = 0
    desc_parts = []
    po_parts   = []
    shippers   = set()
    bundle_types = set()

    for e in entries:
        total_qty += e.bundle_qty or 0
        total_lbs += float(e.weight_lbs or 0)
        total_kgs += float(e.weight_kgs or 0)
        if e.description:
            desc_parts.append(e.description)
        if e.po_order:
            po_parts.append(e.po_order)
        s = e.get_shipper_display()
        if s and s != '—': shippers.add(s)
        bt = e.get_bundle_type_display_name()
        if bt and bt != '—': bundle_types.add(bt)

    shipper_val     = list(shippers)[0] if len(shippers) == 1 else ('VARIOUS' if shippers else '')
    bundle_type_val = ', '.join(bundle_types) if bundle_types else ''

    return JsonResponse({
        'bundle_qty':   total_qty,
        'weight_lbs':   round(total_lbs, 2),
        'weight_kgs':   round(total_kgs, 2),
        'description':  ' / '.join(desc_parts),
        'po_order':     ' / '.join(po_parts),
        'shipper':      shipper_val,
        'bundle_type':  bundle_type_val,
    })


@login_required
def operation_edit(request, pk):
    op = get_object_or_404(WarehouseOperation, pk=pk)
    profile = get_profile(request.user)
    if not is_home(request.user) and not profile.is_customer():
        return HttpResponse('Permission denied.', status=403)

    if request.method == 'POST':
        p = request.POST
        def to_dec(v):
            try:
                from decimal import Decimal
                return Decimal(v) if v and str(v).strip() else None
            except: return None
        def to_int(v):
            try: return int(v) if v and str(v).strip() else None
            except: return None

        if profile.is_customer():
            op.customer_notes = p.get('customer_notes', op.customer_notes or '')
            op.save(update_fields=['customer_notes', 'updated_at'])
        else:
            op.date             = p.get('date', str(op.date))
            op.entry_dispatched = p.get('entry_dispatched', '')
            op.invoice          = p.get('invoice', '')
            op.po_order         = p.get('po_order', '')
            op.seal             = p.get('seal', '')
            op.pro              = p.get('pro', '')
            op.trailer          = p.get('trailer', '')
            op.bundle_qty       = to_int(p.get('bundle_qty'))
            op.weight_lbs       = to_dec(p.get('weight_lbs'))
            op.weight_kgs       = to_dec(p.get('weight_kgs'))
            op.description      = p.get('description', '')
            op.note             = p.get('note', '')
            op.damage           = p.get('damage') == '1'
            op.damage_description = p.get('damage_description', '')
            op.customer_notes   = p.get('customer_notes', op.customer_notes or '')
            op.customer_name_manual  = p.get('customer_name_manual', op.customer_name_manual or '')
            op.shipper_name_manual   = p.get('shipper_name_manual',  op.shipper_name_manual  or '')
            op.carrier_name_manual   = p.get('carrier_name_manual',  op.carrier_name_manual  or '')
            op.bundle_type_manual    = p.get('bundle_type_manual',   op.bundle_type_manual   or '')
            # Nuevos campos
            op.ref_aa = p.get('ref_aa', op.ref_aa or '')
            op.ref_dys = p.get('ref_dys', op.ref_dys or '')
            op.pedimento = p.get('pedimento', op.pedimento or '')
            op.save()

        if request.headers.get('HX-Request'):
            return HttpResponse(
                '<script>'
                'document.getElementById("modal")?.classList.remove("on");'
                'if(typeof htmx !== "undefined") htmx.ajax("GET", "/operations/search/", {target:"#ops-table", swap:"innerHTML"});'
                '</script>'
            )
        else:
            return HttpResponse(
                '<script>'
                'if(window.opener){'
                '   if(typeof window.opener.htmx !== "undefined"){'
                '       window.opener.htmx.ajax("GET", "/operations/search/", {target:"#ops-table", swap:"innerHTML"});'
                '   } else {'
                '       window.opener.location.reload();'
                '   }'
                '   window.close();'
                '} else {'
                '   window.location.href = "/dashboard/";'
                '}'
                '</script>'
            )

    edit_fields = [
        ('Date',             'date',             str(op.date)),
        ('Type',             'operation_type',   op.operation_type),
        ('Entries Disp.',    'entry_dispatched',  op.entry_dispatched),
        ('Customer',         'customer_name_manual', op.get_customer_display()),
        ('Shipper',          'shipper_name_manual',  op.get_shipper_display()),
        ('Invoice',          'invoice',          op.invoice),
        ('PO / Order',       'po_order',         op.po_order),
        ('Seal',             'seal',             op.seal),
        ('Carrier',          'carrier_name_manual',  op.get_carrier_display()),
        ('PRO',              'pro',              op.pro),
        ('Trailer',          'trailer',          op.trailer),
        ('Bundle Type',      'bundle_type_manual',   op.get_bundle_type_display_name()),
        ('Bundle Qty',       'bundle_qty',       op.bundle_qty),
        ('Weight LBS',       'weight_lbs',       op.weight_lbs),
        ('Weight KGS',       'weight_kgs',       op.weight_kgs),
        ('Description',      'description',      op.description),
        ('Note',             'note',             op.note),
        ('Damage Desc.',     'damage_description', op.damage_description),
        ('REF AA',           'ref_aa',           op.ref_aa or ''),
        ('DYS',              'ref_dys',          op.ref_dys or ''),
        ('PEDIMENTO',        'pedimento',        op.pedimento or ''),
    ]
    return render(request, 'warehouse/partials/operation_edit.html', {
        'operation': op, 'edit_fields': edit_fields,
        'profile': profile,
    })


@login_required
def report_generator_excel(request):
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return HttpResponse('openpyxl not installed. Run: pip install openpyxl', status=500)

    ops_ids = request.GET.get('ids','').split(',')
    ops = WarehouseOperation.objects.filter(pk__in=[i for i in ops_ids if i]).select_related(
        'customer','shipper','carrier','bundle_type').order_by('-date')
    ops = customer_ops_filter(request.user, ops)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Operations Report'

    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill('solid', fgColor='0F172A')
    headers = ['#','Date','Custom ID','Type','Status','Customer','Shipper','Carrier',
               'Invoice','PO/Order','Seal','PRO','Trailer','Bundle Type',
               'Bundle Qty','Weight LBS','Weight KGS','Description','Note',
               'Entries Dispatched','Damage','Email Sent', 'REF AA', 'DYS', 'PEDIMENTO']

    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font   = header_font
        cell.fill   = header_fill
        cell.alignment = Alignment(horizontal='center')

    for row, op in enumerate(ops, 2):
        ws.append([
            op.pk, str(op.date), op.custom_id, op.get_operation_type_display(),
            op.status,
            op.get_customer_display(), op.get_shipper_display(), op.get_carrier_display(),
            op.invoice or '', op.po_order or '', op.seal or '',
            op.pro or '', op.trailer or '', op.get_bundle_type_display_name(),
            op.bundle_qty, float(op.weight_lbs) if op.weight_lbs else None,
            float(op.weight_kgs) if op.weight_kgs else None,
            op.description or '', op.note or '', op.entry_dispatched or '',
            'Yes' if op.damage else 'No', 'Yes' if op.email_sent else 'No',
            op.ref_aa or '', op.ref_dys or '', op.pedimento or '',
        ])

    for col in ws.columns:
        max_len = max((len(str(cell.value)) if cell.value else 0) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 3, 40)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(buf.read(),
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = 'attachment; filename="operations_report.xlsx"'
    return resp


# ── IMPORT / EXPORT — OPERATIONS ─────────────────────────────────────────────

@login_required
def operations_layout(request):
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return HttpResponse('openpyxl not installed.', status=500)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Operations Import'

    headers = [
        'date (YYYY-MM-DD)', 'operation_type (ENTRY/EXIT)', 'customer_name',
        'shipper_name', 'carrier_name', 'bundle_type_name',
        'invoice', 'po_order', 'seal', 'pro', 'trailer',
        'bundle_qty', 'weight_lbs', 'weight_kgs',
        'description', 'note', 'damage (yes/no)', 'damage_description',
        'entry_dispatched', 'ref_aa', 'ref_dys', 'pedimento',
    ]
    hfont = Font(bold=True, color='FFFFFF')
    hfill = PatternFill('solid', fgColor='0F172A')
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = hfont
        cell.fill = hfill
        cell.alignment = Alignment(horizontal='center')
        ws.column_dimensions[cell.column_letter].width = max(len(h) + 4, 16)

    ws.append([
        '2026-04-01', 'ENTRY', 'ACME Corp', 'Fast Ship', 'SuperCarrier', 'PALLET',
        'INV-001', 'PO-001', 'SEAL-001', 'PRO-001', 'TRAIL-001',
        10, 500.00, 226.80,
        'Electronic components', 'Handle with care', 'no', '',
        '', '', '', '',
    ])
    ws['A2'].font = Font(italic=True, color='94a3b8')

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = 'attachment; filename="operations_import_layout.xlsx"'
    return resp


@login_required
@require_POST
def operations_import(request):
    profile = get_profile(request.user)
    if not profile.can_create_operations():
        return HttpResponse('Permission denied.', status=403)
    try:
        import openpyxl
    except ImportError:
        return HttpResponse('openpyxl not installed.', status=500)

    f = request.FILES.get('import_file')
    if not f:
        return HttpResponse('No file uploaded.', status=400)

    try:
        wb = openpyxl.load_workbook(f, data_only=True)
        ws = wb.active
        created = 0
        errors = []

        for row_idx, row in enumerate(ws.iter_rows(min_row=3, values_only=True), start=3):
            if not any(row):
                continue
            try:
                date_val, op_type, cust_name, shipper_name, carrier_name, bundle_name, \
                invoice, po_order, seal, pro, trailer, \
                bundle_qty, weight_lbs, weight_kgs, \
                description, note, damage_str, damage_desc, entry_disp, \
                ref_aa, ref_dys, pedimento = (list(row) + [None]*25)[:22]

                if not date_val or not op_type:
                    continue

                from datetime import date as date_cls
                if hasattr(date_val, 'date'):
                    op_date = date_val.date()
                elif isinstance(date_val, date_cls):
                    op_date = date_val
                else:
                    from datetime import datetime
                    op_date = datetime.strptime(str(date_val), '%Y-%m-%d').date()

                op_type = str(op_type).strip().upper()
                if op_type not in ('ENTRY', 'EXIT'):
                    errors.append(f'Row {row_idx}: invalid type "{op_type}"')
                    continue

                def find_cat(name, category):
                    if not name: return None, ''
                    name = str(name).strip()
                    obj = Catalog.objects.filter(category=category, name__iexact=name, active=True).first()
                    return (obj, '') if obj else (None, name)

                cust_obj, cust_manual       = find_cat(cust_name, 'CUSTOMER')
                ship_obj, ship_manual       = find_cat(shipper_name, 'SHIPPER')
                carr_obj, carr_manual       = find_cat(carrier_name, 'CARRIER')
                bundle_obj, bundle_manual   = find_cat(bundle_name, 'BUNDLE_TYPE')

                from decimal import Decimal
                def to_dec(v):
                    try: return Decimal(str(v)) if v is not None and str(v).strip() else None
                    except: return None
                def to_int(v):
                    try: return int(v) if v is not None else None
                    except: return None

                op = WarehouseOperation(
                    date=op_date, operation_type=op_type,
                    customer=cust_obj, customer_name_manual=cust_manual,
                    shipper=ship_obj, shipper_name_manual=ship_manual,
                    carrier=carr_obj, carrier_name_manual=carr_manual,
                    bundle_type=bundle_obj, bundle_type_manual=bundle_manual,
                    invoice=str(invoice or '').strip(),
                    po_order=str(po_order or '').strip(),
                    seal=str(seal or '').strip(),
                    pro=str(pro or '').strip(),
                    trailer=str(trailer or '').strip(),
                    bundle_qty=to_int(bundle_qty),
                    weight_lbs=to_dec(weight_lbs),
                    weight_kgs=to_dec(weight_kgs),
                    description=str(description or '').strip(),
                    note=str(note or '').strip(),
                    damage=str(damage_str or '').strip().lower() == 'yes',
                    damage_description=str(damage_desc or '').strip(),
                    entry_dispatched=str(entry_disp or '').strip(),
                    created_by=request.user,
                    ref_aa=str(ref_aa or '').strip(),
                    ref_dys=str(ref_dys or '').strip(),
                    pedimento=str(pedimento or '').strip(),
                )
                op.save()
                created += 1
            except Exception as e:
                errors.append(f'Row {row_idx}: {e}')

        ops = WarehouseOperation.objects.select_related(
            'customer', 'shipper', 'carrier', 'bundle_type').all()
        ops = customer_ops_filter(request.user, ops)[:200]
        profile = get_profile(request.user)
        msg = f'{created} operation(s) imported successfully.'
        if errors:
            msg += f' Errors: {"; ".join(errors[:3])}'
        return render(request, 'warehouse/partials/operations_table.html', {
            'operations': ops,
            'import_success' if not errors else 'import_error': msg,
            'is_home': is_home(request.user), 'profile': profile,
        })
    except Exception as e:
        return HttpResponse(f'Import failed: {e}', status=500)


# ── IMPORT / EXPORT — CATALOG ─────────────────────────────────────────────────

@login_required
def catalog_layout(request):
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return HttpResponse('openpyxl not installed.', status=500)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Catalog Import'

    headers = ['category', 'name', 'contact_email', 'phone', 'whatsapp', 'address', 'notes']
    notes_row = [
        'CUSTOMER / SHIPPER / CARRIER / BUNDLE_TYPE / CC_EMAIL',
        'Required', 'Optional, comma-separated', 'Optional',
        'Optional (+521XXXXXXXXXX)', 'Optional', 'Optional',
    ]
    hfont = Font(bold=True, color='FFFFFF')
    hfill = PatternFill('solid', fgColor='0F172A')
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = hfont; cell.fill = hfill
        cell.alignment = Alignment(horizontal='center')
        ws.column_dimensions[cell.column_letter].width = max(len(h)+4, 20)

    ws.append(notes_row)
    for cell in ws[2]:
        cell.font = Font(italic=True, color='94a3b8')

    ws.append(['CUSTOMER', 'Example Corp', 'contact@example.com', '+5218001234567', '+5218001234567', '123 Main St', 'VIP client'])

    buf = BytesIO()
    wb.save(buf); buf.seek(0)
    resp = HttpResponse(buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = 'attachment; filename="catalog_import_layout.xlsx"'
    return resp


@login_required
@require_POST
def catalog_import(request):
    profile = get_profile(request.user)
    if profile.is_customer():
        return HttpResponse('Permission denied.', status=403)
    try:
        import openpyxl
    except ImportError:
        return HttpResponse('openpyxl not installed.', status=500)

    f = request.FILES.get('import_file')
    if not f:
        return HttpResponse('No file.', status=400)

    try:
        wb = openpyxl.load_workbook(f, data_only=True)
        ws = wb.active
        created = 0
        errors = []
        valid_cats = ['CUSTOMER','SHIPPER','CARRIER','BUNDLE_TYPE','TYPE_OP','CC_EMAIL']

        for row_idx, row in enumerate(ws.iter_rows(min_row=3, values_only=True), start=3):
            if not any(row): continue
            try:
                category, name, email, phone, whatsapp, address, notes = (list(row)+[None]*7)[:7]
                category = str(category or '').strip().upper()
                name     = str(name or '').strip()
                if not category or not name or category not in valid_cats:
                    errors.append(f'Row {row_idx}: invalid category or missing name')
                    continue
                if not Catalog.objects.filter(category=category, name__iexact=name).exists():
                    Catalog.objects.create(
                        category=category, name=name,
                        contact_email=str(email or '').strip(),
                        phone=str(phone or '').strip(),
                        whatsapp=str(whatsapp or '').strip(),
                        address=str(address or '').strip(),
                        notes=str(notes or '').strip(),
                    )
                    created += 1
            except Exception as e:
                errors.append(f'Row {row_idx}: {e}')

        catalog_entries = Catalog.objects.filter(active=True).order_by('category','name')
        msg = f'{created} catalog entries imported.'
        if errors: msg += f' Errors: {"; ".join(errors[:3])}'
        key = 'import_success' if not errors else 'import_error'
        return render(request, 'warehouse/partials/catalog_table.html',
                      {'catalog_entries': catalog_entries, key: msg})
    except Exception as e:
        return HttpResponse(f'Import failed: {e}', status=500)