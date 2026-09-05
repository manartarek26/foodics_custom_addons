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


def orders_data(status_filter=None):
    """No canned orders - foodics_accounting's pull sync/"Refresh from Foodics" only ever sees
    whatever you build by hand under Foodics Mock > Mock POS Orders (foodics.mock.pos.order).
    `status_filter` mirrors Foodics' `filter[status]` query param (comma-separated string/list),
    kept here even though it's a no-op on an always-empty list so callers don't need to special-case it.
    """
    all_orders = []
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
