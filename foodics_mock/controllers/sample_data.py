"""Canned sample payloads, shaped after Foodics' real API documentation,
used by the mock controller so foodics_base/foodics_order_sync have
something realistic to sync against before real credentials are available.
"""


def whoami_data(app):
    return {
        'data': {
            'id': app.client_id,
            'name': app.business_name,
            'reference': app.business_reference,
        }
    }


def settings_data():
    return {
        'data': {
            'business_currency': 'USD',
            'business_logo': None,
            'business_timezone': '+03:00',
            'tax_inclusive_pricing': False,
            'cashier_final_price_rounding_level': 0.1,
            'cashier_final_price_rounding_method': 'none',
            'loyalty_enabled': False,
            'receipt_footer': 'Thanks for testing!',
            'receipt_header': 'Foodics Mock Server',
        }
    }


def branches_data():
    return {
        # `links`/`meta` are here so foodics_base's `_get_all()` paginator
        # has something realistic to walk through, even though there's only
        # ever one page of mock data.
        'links': {'first': None, 'last': None, 'prev': None, 'next': None},
        'meta': {'current_page': 1, 'last_page': 1, 'per_page': 50, 'total': 2},
        'data': [
            {
                'id': 'mock-branch-001',
                'name': 'Main Branch (Mock)',
                'reference': 'B01',
                'latitude': None,
                'longitude': None,
                'phone': None,
                'opening_from': '08:00',
                'opening_to': '23:59',
                'receives_online_orders': True,
                'created_at': '2024-01-01 08:00:00',
                'updated_at': '2024-01-01 08:00:00',
                'deleted_at': None,
            },
            {
                'id': 'mock-branch-002',
                'name': 'Drive Thru Branch (Mock)',
                'reference': 'B02',
                'latitude': None,
                'longitude': None,
                'phone': None,
                'opening_from': '10:00',
                'opening_to': '22:00',
                'receives_online_orders': False,
                'created_at': '2024-01-01 08:00:00',
                'updated_at': '2024-01-01 08:00:00',
                'deleted_at': None,
            },
        ]
    }


def categories_data():
    return {
        'data': [
            {'id': 'mock-cat-drinks', 'name': 'Drinks', 'name_localized': None},
            {'id': 'mock-cat-sandwiches', 'name': 'Sandwiches', 'name_localized': None},
        ]
    }


def taxes_data():
    return {
        'links': {'first': None, 'last': None, 'prev': None, 'next': None},
        'meta': {'current_page': 1, 'last_page': 1, 'per_page': 50, 'total': 1},
        'data': [
            {'id': 'mock-tax-vat15', 'name': 'VAT 15% (Mock)', 'name_localized': None, 'rate': 15},
        ]
    }


def payment_methods_data():
    return {
        'links': {'first': None, 'last': None, 'prev': None, 'next': None},
        'meta': {'current_page': 1, 'last_page': 1, 'per_page': 50, 'total': 2},
        'data': [
            {'id': 'mock-pm-cash', 'name': 'Cash (Mock)', 'code': 'cash', 'type': 1, 'is_active': True},
            {'id': 'mock-pm-card', 'name': 'Card (Mock)', 'code': 'card', 'type': 2, 'is_active': True},
        ]
    }


def _mock_pos_order(order_id, number, status, product_qty_price, tax_amount, payment_method_id):
    """Shaped after the sample in Foodics' "Accounting/ERP Integration" guide: enough of the
    real /orders response for foodics_accounting to build an invoice line-by-line from it -
    one product line (with its tax) and one full payment covering the order total.
    """
    quantity, unit_price = product_qty_price
    subtotal = quantity * unit_price
    total = subtotal + tax_amount
    return {
        'id': order_id,
        'number': number,
        'type': 1,
        'status': status,
        'branch': {'id': 'mock-branch-001', 'name': 'Main Branch (Mock)', 'reference': 'B01'},
        'customer': None,
        'business_date': '2024-07-28',
        'opened_at': '2024-07-28 12:38:39',
        'closed_at': '2024-07-28 12:43:47',
        'subtotal_price': subtotal,
        'discount_amount': 0,
        'rounding_amount': 0,
        'total_price': total,
        'products': [
            {
                'id': f'{order_id}-line-1',
                'product': {'id': 'mock-prod-burger', 'name': 'Burger'},
                'quantity': quantity,
                'unit_price': unit_price,
                'discount_amount': 0,
                'total_price': total,
                'tax_exclusive_unit_price': unit_price,
                'tax_exclusive_total_price': subtotal,
                'status': 3,
                'taxes': [{'id': 'mock-tax-vat15', 'name': 'VAT 15% (Mock)', 'rate': 15,
                           'pivot': {'amount': tax_amount, 'rate': 15}}],
                'options': [],
            }
        ],
        'combos': [],
        'charges': [],
        'payments': [
            {
                'id': f'{order_id}-pay-1',
                'amount': total,
                'tendered': total,
                'tips': 0,
                'business_date': '2024-07-28',
                'payment_method': {'id': payment_method_id, 'name': 'Cash (Mock)', 'type': 1},
            }
        ],
    }


def orders_data(status_filter=None):
    """Two canned orders: one Closed (4), one Returned (5) - enough to exercise both the
    invoice and credit-note paths of foodics_accounting's pull sync without a real account.
    `status_filter` mirrors Foodics' `filter[status]` query param (comma-separated string/list).
    """
    all_orders = [
        _mock_pos_order('mock-order-closed-001', 101, 4, (2, 28.0), 8.4, 'mock-pm-cash'),
        _mock_pos_order('mock-order-returned-001', 102, 5, (1, 28.0), 4.2, 'mock-pm-card'),
    ]
    if status_filter:
        wanted = {int(s) for s in str(status_filter).split(',') if s.strip().isdigit()}
        all_orders = [o for o in all_orders if o['status'] in wanted]
    return {
        'links': {'first': None, 'last': None, 'prev': None, 'next': None},
        'meta': {'current_page': 1, 'last_page': 1, 'per_page': 50, 'total': len(all_orders)},
        'data': all_orders,
    }


def products_data():
    return {
        'links': {'first': None, 'last': None, 'prev': None, 'next': None},
        'meta': {'current_page': 1, 'last_page': 1, 'per_page': 50, 'total': 3},
        'data': [
            {
                'id': 'mock-prod-burger',
                'sku': 'P001',
                'barcode': None,
                'name': 'Burger',
                'name_localized': None,
                'description': 'A mock burger for testing.',
                'is_active': True,
                'is_stock_product': False,
                'is_ready': True,
                'pricing_method': 1,
                'selling_method': 2,
                'price': 28.0,
                'calories': 624,
                'category': {'id': 'mock-cat-sandwiches', 'name': 'Sandwiches'},
            },
            {
                'id': 'mock-prod-pepsi',
                'sku': 'P002',
                'barcode': None,
                'name': 'Pepsi',
                'name_localized': None,
                'description': 'A mock soft drink for testing.',
                'is_active': True,
                'is_stock_product': False,
                'is_ready': True,
                'pricing_method': 1,
                'selling_method': 2,
                'price': 6.0,
                'calories': 150,
                'category': {'id': 'mock-cat-drinks', 'name': 'Drinks'},
            },
            {
                'id': 'mock-prod-milk',
                'sku': 'P003',
                'barcode': None,
                'name': 'Milk',
                'name_localized': None,
                'description': 'A mock non-taxable item for testing.',
                'is_active': True,
                'is_stock_product': False,
                'is_ready': True,
                'pricing_method': 1,
                'selling_method': 2,
                'price': 4.5,
                'calories': 120,
                'category': {'id': 'mock-cat-drinks', 'name': 'Drinks'},
            },
        ]
    }
