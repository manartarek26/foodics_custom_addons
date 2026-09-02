from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    is_foodics_product = fields.Boolean(
        string='From Foodics', default=False, readonly=True, copy=False,
        help='Set automatically when this product was created or linked through a Foodics '
             'menu sync (see Foodics > Product Mapping). Informational only - editing it by '
             'hand has no effect on the sync logic itself.')
    foodics_mapping_id = fields.Many2one(
        'foodics.product.mapping', string='Foodics Mapping', readonly=True, copy=False,
        help='The Foodics Product Mapping record that created or linked this product.')
