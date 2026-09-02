from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    foodics_product_requires_approval = fields.Boolean(
        related='company_id.foodics_product_requires_approval', readonly=False)
