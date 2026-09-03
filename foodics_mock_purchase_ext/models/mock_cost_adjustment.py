import uuid

from odoo import fields, models


class FoodicsMockCostAdjustment(models.Model):
    """Fake-server stand-in for the Foodics 'Cost Adjustment Transaction'.

    Mirrors the API Docs PDF section "Cost Adjustment Transactions":
    "Cost Adjustment is an inventory transaction that is done to
    correct/override current inventory item costs." Items pivot carries
    cost_per_unit + previous_cost_per_unit.
    """
    _name = 'foodics.mock.cost.adjustment'
    _description = 'Foodics Mock Cost Adjustment Transaction'
    _rec_name = 'reference'
    _order = 'create_date desc'

    app_id = fields.Many2one('foodics.mock.app', required=True, ondelete='cascade', index=True)
    foodics_id = fields.Char(required=True, default=lambda s: uuid.uuid4().hex[:8], copy=False)
    business_date = fields.Char()
    reference = fields.Char()
    status = fields.Integer(default=1)
    notes = fields.Char()
    branch_id = fields.Char()
    creator_id = fields.Char()
    poster_id = fields.Char()
    line_ids = fields.One2many('foodics.mock.cost.adjustment.line', 'adjustment_id')

    _sql_constraints = [
        ('mock_ca_unique', 'unique(app_id, foodics_id)', 'Duplicate mock cost adjustment id'),
    ]

    def api_dump(self):
        """Serialize exactly like the docs' Cost Adjustment sample."""
        self.ensure_one()
        return {
            'id': self.foodics_id,
            'business_date': self.business_date or None,
            'reference': self.reference or None,
            'status': self.status,
            'notes': self.notes or None,
            'items': [line.api_dump() for line in self.line_ids],
            'branch': {'id': self.branch_id} if self.branch_id else None,
            'creator': {'id': self.creator_id} if self.creator_id else None,
            'poster': {'id': self.poster_id} if self.poster_id else None,
            'created_at': fields.Datetime.to_string(self.create_date),
            'updated_at': fields.Datetime.to_string(self.write_date),
        }


class FoodicsMockCostAdjustmentLine(models.Model):
    _name = 'foodics.mock.cost.adjustment.line'
    _description = 'Foodics Mock Cost Adjustment Line'

    adjustment_id = fields.Many2one(
        'foodics.mock.cost.adjustment', required=True, ondelete='cascade')
    item_id = fields.Many2one('foodics.mock.inventory.item', required=True, ondelete='cascade')
    cost_per_unit = fields.Float()
    previous_cost_per_unit = fields.Float()

    def api_dump(self):
        self.ensure_one()
        return {
            'pivot': {
                'cost_per_unit': self.cost_per_unit,
                'previous_cost_per_unit': self.previous_cost_per_unit,
            },
            'id': self.item_id.foodics_id,
        }
