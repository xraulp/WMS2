# DYSWMS v27 — Deployment Guide

## Steps to deploy on PythonAnywhere

### 1. Upload files
Upload the contents of this folder to your PythonAnywhere account via:
- Files tab → Upload ZIP → Extract, OR
- Git push and pull on server

### 2. Install new dependency
In a PythonAnywhere Bash console:
```bash
pip install openpyxl --break-system-packages
# or inside your virtualenv:
pip install openpyxl
```

### 3. Run migrations
```bash
python manage.py migrate
```
This applies migration `0002_v27` which adds:
- `created_by` field to WarehouseOperation
- `customer_notes` field to WarehouseOperation
- `delete_password` field to UserProfile
- New role choices (superadmin, manager, staff, customer)
- New `DeletionLog` model

### 4. Update existing users
In Django shell (`python manage.py shell`):
```python
from warehouse.models import UserProfile
from django.contrib.auth.models import User

# Existing "home" users → set as manager
for p in UserProfile.objects.all():
    if p.role == 'home':
        p.role = 'manager'
        p.save()
    print(p.user.username, p.role)
```

### 5. Reload the web app
In PythonAnywhere Web tab → Reload.

---

## New Features Summary (v27)

| # | Feature | Location |
|---|---------|----------|
| 1 | Fixed top menu + tabs | All pages |
| 2 | EXIT concatenates PO/ORDER from entries | New Operation |
| 3 | Validation errors don't clear form fields | New Operation |
| 4 | WhatsApp checked by default | New Operation |
| 5 | Mobile dropdowns catalog-only | Mobile |
| 6 | Delete with password + log | Database |
| 7 | Delete button added per row | Database |
| 8 | Status column (Released/In Warehouse) | Database |
| 9 | Auto ID # column | Database |
| 10 | Alternating row colors | Database |
| 11 | Sticky dual scrollbar | Database |
| 12 | WhatsApp button in operation view | Database |
| 13 | Soft-delete catalog (Archive) + Edit | Catalog |
| 14 | WhatsApp column in catalog list | Catalog |
| 15 | Camera button in digital tab (mobile) | Digital |
| 16 | Status filter | Report Generator |
| 17 | All Customers + multi-customer filter | Report Generator |
| 18 | openpyxl installed → Excel export fixed | Report Generator |
| 19 | New roles: superadmin/manager/staff/customer | Users |
| 20 | Role permissions enforced | All tabs |
| 21 | Users see only their customer's records | Database/Reports |
| 22 | Mobile: stay on New Operation after save | Mobile |
| 23 | Mobile: tabs shown by role | Mobile |
| 24 | Login: show/hide password button | Login |
