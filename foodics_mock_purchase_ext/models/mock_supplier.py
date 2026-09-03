import uuid

from odoo import fields, models


class FoodicsMockSupplier(models.Model):
    """Fake-server stand-in for the Foodics 'Supplier' object.

    Mirrors the API Docs PDF section "Suppliers": id, name, contact_name,
    phone, code, email, items[] (pivot: order_unit, order_to_storage_factor,
    minimum_order_quantity, cost, code), tags[], created_at/updated_at/
    deleted_at. Delete is soft (deleted_at) because the docs expose a
    dedicated /suppliers/{id}/restore endpoint.
    """
    _name = 'foodics.mock.supplier'
    _description = 'Foodics Mock Supplier'
    _rec_name = 'name'

    app_id = fields.Many2one('foodics.mock.app', required=True, ondelete='cascade', index=True)
    foodics_id = fields.Char(required=True, default=lambda s: uuid.uuid4().hex[:8], copy=False)
    name = fields.Char(required=True)
    contact_name = fields.Char()
    phone = fields.Char()
    email = fields.Char()
    code = fields.Char()
    item_ids = fields.One2many('foodics.mock.supplier.item', 'supplier_id')
    deleted_at = fields.Datetime(copy=False)

    _sql_constraints = [
        ('mock_supplier_unique', 'unique(app_id, foodics_id)', 'Duplicate mock supplier id'),
    ]

    def api_dump(self):
        """Serialize exactly like the docs' Supplier sample."""
        self.ensure_one()
        return {
            'id': self.foodics_id,
            'name': self.name,
            'contact_name': self.contact_name or None,
            'phone': self.phone or None,
            'code': self.code or None,
            'email': self.email or None,
            'items': [line.api_dump() for line in self.item_ids],
            'tags': [],
            'created_at': fields.Datetime.to_string(self.create_date),
            'updated_at': fields.Datetime.to_string(self.write_date),
            'deleted_at': fields.Datetime.to_string(self.deleted_at) if self.deleted_at else None,
        }


class FoodicsMockSupplierItem(models.Model):
    """The supplier<->inventory-item pivot from the docs ("items" array)."""
    _name = 'foodics.mock.supplier.item'
    _description = 'Foodics Mock Supplier / Inventory Item Pivot'
    _rec_name = 'item_id'

    supplier_id = fields.Many2one('foodics.mock.supplier', required=True, ondelete='cascade')
    item_id = fields.Many2one('foodics.mock.inventory.item', required=True, ondelete='cascade')
    # "The unit used in purchase orders and purchasing from the given supplier"
    order_unit = fields.Char(default='box')
    # "The conversion factor between order and storage units"
    order_to_storage_factor = fields.Float(default=1.0)
    # "Purchase orders must have quantity greater than or equal to minimum
    #  order quantity in order unit"
    minimum_order_quantity = fields.Float(default=0.0)
    cost = fields.Float(default=0.0)
    code = fields.Char()

    def api_dump(self):
        self.ensure_one()
        return {
            'pivot': {
                'order_unit': self.order_unit,
                'order_to_storage_factor': self.order_to_storage_factor,
                'minimum_order_quantity': self.minimum_order_quantity,
                'cost': self.cost,
                'code': self.code or None,
            },
            'id': self.item_id.foodics_id,
        }
