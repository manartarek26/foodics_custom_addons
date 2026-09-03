from odoo import fields, models


class FoodicsMockApp(models.Model):
    """Additive extension of the mock app: lazily seeds doc-shaped sample
    purchasing data the first time any purchase endpoint of this app is
    hit. The original foodics_mock module is NOT modified - this uses
    standard Odoo model inheritance."""
    _inherit = 'foodics.mock.app'

    def _ensure_purchase_seed(self):
        """Seed sample inventory items / suppliers / one historical PO +
        Purchasing transaction + Cost Adjustment, shaped after the samples
        in the API Docs PDFs (Tomatoes in boxes, Hyper Market, PUR-000001,
        CA1231...). Only runs when the app has no items yet."""
        self.ensure_one()
        Item = self.env['foodics.mock.inventory.item']
        if Item.search_count([('app_id', '=', self.id)]):
            return

        def item(name, sku, unit='box', factor=1.0, cost=0.0, min_lvl=0.0,
                 max_lvl=0.0, par=0.0, category=None):
            return Item.create({
                'app_id': self.id, 'name': name, 'sku': sku,
                'storage_unit': unit, 'ingredient_unit': 'gram',
                'storage_to_ingredient_factor': factor * 1000 or 1.0,
                'costing_method': 1, 'cost': cost,
                'minimum_level': min_lvl, 'maximum_level': max_lvl,
                'par_level': par, 'is_product': False,
                'category_name': category,
            })

        tomatoes = item('Tomatoes', 'I001', 'box', 1.0, cost=10.0,
                        min_lvl=300, max_lvl=1000, par=700)
        cheese = item('Cheese Slices', 'I002', 'Kg', 1.0, cost=15.0, category='Dairy')
        beef = item('Beef Patty', 'I003', 'Kg', 1.0, cost=22.0, category='Meat')
        oil = item('Cooking Oil', 'I004', 'box', 1.0, cost=30.0)

        Supplier = self.env['foodics.mock.supplier']
        hyper = Supplier.create({
            'app_id': self.id, 'name': 'Hyper Market',
            'contact_name': 'Jonatan', 'phone': '2362056789',
            'email': 'jonatan@mail.com', 'code': '1234',
        })
        fresh = Supplier.create({
            'app_id': self.id, 'name': 'Fresh Farms Co',
            'contact_name': 'Sara', 'phone': '5550101',
            'email': 'sara@freshfarms.test', 'code': 'F002',
        })
        SI = self.env['foodics.mock.supplier.item']
        # pivot values mirror the docs' supplier sample style
        SI.create({'supplier_id': hyper.id, 'item_id': tomatoes.id,
                   'order_unit': 'box', 'order_to_storage_factor': 2,
                   'minimum_order_quantity': 10, 'cost': 90.0, 'code': 'TOM-SUP'})
        SI.create({'supplier_id': hyper.id, 'item_id': oil.id,
                   'order_unit': 'box', 'order_to_storage_factor': 4,
                   'minimum_order_quantity': 5, 'cost': 28.0, 'code': 'OIL-SUP'})
        SI.create({'supplier_id': fresh.id, 'item_id': cheese.id,
                   'order_unit': 'Kg', 'order_to_storage_factor': 1,
                   'minimum_order_quantity': 2, 'cost': 16.5, 'code': 'CHS-SUP'})
        SI.create({'supplier_id': fresh.id, 'item_id': beef.id,
                   'order_unit': 'Kg', 'order_to_storage_factor': 1,
                   'minimum_order_quantity': 3, 'cost': 23.5, 'code': 'BEEF-SUP'})

        # One CLOSED historical PO + its Purchasing transaction so status
        # polling and reconciliation have data on day one.
        now = fields.Datetime.now()
        po = self.env['foodics.mock.purchase.order'].create({
            'app_id': self.id, 'foodics_id': 'seed-po-000001',
            'business_date': fields.Date.to_string(fields.Date.today()),
            'reference': 'PO-SEED-0001', 'status': 6,
            'branch_id': 'mock-branch-001',
            'supplier_id': hyper.foodics_id,
            'creator_id': 'seed-user-1', 'submitter_id': 'seed-user-1',
            'poster_id': 'seed-user-1',
            'submitted_at': now, 'reviewed_at': now, 'closed_at': now,
            'notes': 'Seeded closed order (docs-style example)',
        })
        line = self.env['foodics.mock.purchase.order.line'].create({
            'order_id': po.id, 'item_id': tomatoes.id,
            'quantity': 10, 'cost': 90.0, 'unit': 'box',
            'unit_to_storage_factor': 2, 'quantity_received': 20,
        })
        tx = self.env['foodics.mock.inventory.transaction'].create({
            'app_id': self.id, 'foodics_id': 'seed-tx-000001',
            'business_date': fields.Date.to_string(fields.Date.today()),
            'reference': 'PUR-0000001', 'type': 1, 'status': 4,
            'branch_id': 'mock-branch-001', 'supplier_id': hyper.foodics_id,
            'creator_id': 'seed-user-1', 'poster_id': 'seed-user-1',
            'purchase_order_id': po.foodics_id,
            'notes': 'Purchase from Supplier',
            'invoice_number': '500100',
            'posted_at': now,
        })
        self.env['foodics.mock.inventory.transaction.line'].create({
            'transaction_id': tx.id, 'item_id': tomatoes.id,
            # storage units: 10 boxes x factor 2 = 20
            'quantity': line.quantity * line.unit_to_storage_factor,
            'cost': line.cost / line.unit_to_storage_factor,
        })

        ca = self.env['foodics.mock.cost.adjustment'].create({
            'app_id': self.id, 'foodics_id': 'seed-ca-000001',
            'business_date': fields.Date.to_string(fields.Date.today()),
            'reference': 'CA-SEED-0001', 'status': 1,
            'branch_id': 'mock-branch-001', 'creator_id': 'seed-user-1',
            'notes': 'Seeded cost adjustment (docs-style example)',
        })
        self.env['foodics.mock.cost.adjustment.line'].create({
            'adjustment_id': ca.id, 'item_id': cheese.id,
            'cost_per_unit': cheese.cost, 'previous_cost_per_unit': cheese.cost + 1,
        })
