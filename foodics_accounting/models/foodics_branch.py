from odoo import fields, models


class FoodicsBranch(models.Model):
    _inherit = 'foodics.branch'

    # See docs/base_and_mock.html for the same reasoning as warehouse_id
    # (foodics_base) - Foodics has no way to tell us which Odoo POS Point a
    # branch corresponds to, a human has to say so once.
    pos_config_id = fields.Many2one(
        'pos.config', string='Odoo POS Point',
        help='The Odoo Point of Sale this branch\'s Foodics sales are recorded into - '
             'foodics.pos.order creates its POS orders in this config\'s current (or a newly '
             'opened) session. One Foodics branch = one Odoo POS Point.')
