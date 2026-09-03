import uuid

from odoo import fields, models


class FoodicsMockInventoryItem(models.Model):
    """Fake-server stand-in for the Foodics 'Inventory Item' object.

    Field names/shapes mirror the API Docs PDF section "Inventory Items":
    id, name, name_localized, sku, barcode, minimum_level, maximum_level,
    par_level, storage_unit, ingredient_unit, storage_to_ingredient_factor,
    costing_method (1=Fixed Cost / 2=Calculate cost from ingredients),
    cost, is_product, created_at/updated_at/deleted_at.
    """
    _name = 'foodics.mock.inventory.item'
    _description = 'Foodics Mock Inventory Item'
    _rec_name = 'name'

    app_id = fields.Many2one('foodics.mock.app', required=True, ondelete='cascade', index=True)
    foodics_id = fields.Char(required=True, default=lambda s: uuid.uuid4().hex[:8], copy=False)
    name = fields.Char(required=True)
    name_localized = fields.Char()
    sku = fields.Char()
    barcode = fields.Char()
    minimum_level = fields.Float(default=0.0)
    maximum_level = fields.Float(default=0.0)
    par_level = fields.Float(default=0.0)
    storage_unit = fields.Char(default='box')
    ingredient_unit = fields.Char(default='gram')
    # Docs: "The conversion factor between storage and ingredient units"
    storage_to_ingredient_factor = fields.Float(default=1.0)
    # 1 = Fixed Cost, 2 = Calculate cost from ingredients (docs table)
    costing_method = fields.Integer(default=1)
    cost = fields.Float(default=0.0)
    is_product = fields.Boolean(default=False)
    category_name = fields.Char()

    deleted_at = fields.Datetime(copy=False)

    _sql_constraints = [
        ('mock_item_unique', 'unique(app_id, foodics_id)', 'Duplicate mock inventory item id'),
    ]

    def api_dump(self):
        """Serialize exactly like the docs' Inventory Item sample."""
        self.ensure_one()
        return {
            'id': self.foodics_id,
            'name': self.name,
            'name_localized': self.name_localized or None,
            'sku': self.sku,
            'barcode': self.barcode or None,
            'minimum_level': str(self.minimum_level),
            'maximum_level': str(self.maximum_level),
            'par_level': str(self.par_level),
            'storage_unit': self.storage_unit,
            'ingredient_unit': self.ingredient_unit,
            'storage_to_ingredient_factor': self.storage_to_ingredient_factor,
            'costing_method': self.costing_method,
            'cost': self.cost,
            'is_product': self.is_product,
            'created_at': fields.Datetime.to_string(self.create_date),
            'updated_at': fields.Datetime.to_string(self.write_date),
            'deleted_at': fields.Datetime.to_string(self.deleted_at) if self.deleted_at else None,
        }
