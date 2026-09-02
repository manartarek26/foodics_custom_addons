from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    foodics_customer_id = fields.Char(
        string='Foodics Customer ID', copy=False, index=True,
        help='Set when this contact was created from (or matched to) a Foodics customer while '
             'importing a POS order - see foodics.pos.order.')
