from odoo import api, fields, models


class FoodicsPaymentMethod(models.Model):
    """One row per Foodics payment method (GET /payment_methods), for one
    Foodics connection. Needs a human to pick the Odoo POS payment method
    used to record pos.payment for it (e.g. Foodics "Cash" -> Odoo Cash,
    Foodics "Card" -> Odoo Card) - Foodics has no concept of an Odoo POS
    payment method to tell us this itself.
    """
    _name = 'foodics.payment.method'
    _description = 'Foodics Payment Method Mapping'
    _rec_name = 'name'

    # Mirrors Foodics' Payment Method Types table (payment_methods.html).
    METHOD_TYPES = [
        ('1', 'Cash'),
        ('2', 'Card'),
        ('3', 'Other'),
        ('4', 'Gift Card'),
        ('5', 'House Account'),
        ('7', '3rd Party'),
    ]

    config_id = fields.Many2one('foodics.config', required=True, ondelete='cascade')
    foodics_id = fields.Char(required=True, string='Foodics Payment Method ID')
    name = fields.Char(required=True)
    code = fields.Char()
    method_type = fields.Selection(METHOD_TYPES, default='3', required=True)
    pos_payment_method_id = fields.Many2one(
        'pos.payment.method', string='Odoo POS Payment Method',
        help='Used to record Odoo pos.payment for POS payments made through this Foodics '
             'payment method - must be one of the payment methods enabled on the mapped '
             'branch\'s POS Point (Foodics > Branches). Leave empty and any order using this '
             'payment method is held in "Needs Review" instead - unlike an unmapped product, '
             'there is no safe fallback for money movement. Note: a "cash"-type POS payment '
             'method can only belong to one POS Point, so a single mapping here only covers '
             'every branch cleanly when they all share the same POS Point, or none of them take '
             'cash - a multi-branch, multi-till cash setup needs a dedicated mapping per branch, '
             'not built here.')
    is_active = fields.Boolean(default=True)

    _sql_constraints = [
        ('foodics_payment_method_unique', 'unique(config_id, foodics_id)',
         'This payment method is already mapped.'),
    ]

    @api.model
    def _sync_from_foodics(self, config, records):
        created = updated = 0
        for rec in records:
            foodics_id = rec.get('id')
            if not foodics_id:
                continue
            mapping = self.search([('config_id', '=', config.id), ('foodics_id', '=', foodics_id)], limit=1)
            vals = {
                'name': rec.get('name') or foodics_id,
                'code': rec.get('code'),
                'method_type': str(rec.get('type') or 3),
                'is_active': rec.get('is_active', True),
            }
            if mapping:
                # Never touch pos_payment_method_id here - that mapping is manual and must survive re-syncs.
                mapping.write(vals)
                updated += 1
            else:
                vals.update({'config_id': config.id, 'foodics_id': foodics_id})
                self.create(vals)
                created += 1
        return created, updated
