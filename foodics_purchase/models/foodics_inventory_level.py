from odoo import fields, models


class FoodicsInventoryLevel(models.Model):
    """Snapshot of live stock levels pulled from Foodics
    (docs: GET /inventory_levels/{branch_id} - rows are
    {"pivot": {"quantity", "cost_per_unit"}, "id": <inventory item id>}).

    Kept per (item, branch) and refreshed in place so buyers can see what
    each branch currently holds before raising purchase orders. This gives
    "out-of-stock / below minimum" visibility without inventing any data:
    the numbers come straight from Foodics transactions.
    """
    _name = 'foodics.inventory.level'
    _description = 'Foodics Inventory Level (live snapshot)'
    _rec_name = 'item_id'

    config_id = fields.Many2one('foodics.config', required=True, ondelete='cascade',
                                index=True)
    item_id = fields.Many2one('foodics.inventory.item', required=True,
                              ondelete='cascade', index=True)
    branch_id = fields.Many2one('foodics.branch', required=True, ondelete='cascade',
                                index=True)
    quantity = fields.Float(string='Quantity (storage units)')
    cost_per_unit = fields.Float()
    fetched_at = fields.Datetime(default=fields.Datetime.now, readonly=True)

    _sql_constraints = [
        ('foodics_level_unique', 'unique(config_id, item_id, branch_id)',
         'Only one level snapshot per item/branch.'),
    ]

    below_minimum = fields.Boolean(compute='_compute_below_minimum')

    def _compute_below_minimum(self):
        """Flags items at/below their doc'd minimum_level - a hint that a
        new purchase order may be needed."""
        for rec in self:
            rec.below_minimum = bool(
                rec.item_id.minimum_level and rec.quantity <= rec.item_id.minimum_level)
