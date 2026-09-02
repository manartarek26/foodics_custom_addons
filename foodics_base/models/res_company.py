from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    foodics_product_requires_approval = fields.Boolean(
        string='Foodics products require approval',
        default=True,
        help='If enabled, products pulled in from a Foodics menu sync wait in Foodics > '
             'Product Mapping for someone to approve them before the matching Odoo product is '
             'created/linked and made available (e.g. for inventory or Sale Orders). If '
             'disabled, they are created/linked automatically as soon as they are synced.')
