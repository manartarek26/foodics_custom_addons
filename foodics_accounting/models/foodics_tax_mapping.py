from odoo import api, fields, models


class FoodicsTaxMapping(models.Model):
    """One row per Foodics tax (GET /taxes), for one Foodics connection.
    Needs a human to pick the matching Odoo tax once - Foodics has no way to
    tell us which Odoo tax record corresponds to e.g. its "VAT 15%".
    """
    _name = 'foodics.tax.mapping'
    _description = 'Foodics Tax Mapping'
    _rec_name = 'name'

    config_id = fields.Many2one('foodics.config', required=True, ondelete='cascade')
    foodics_id = fields.Char(required=True, string='Foodics Tax ID')
    name = fields.Char(required=True)
    rate = fields.Float(help='Tax rate as last reported by Foodics, for reference only.')
    account_tax_id = fields.Many2one(
        'account.tax', string='Odoo Tax', domain="[('type_tax_use', '=', 'sale')]",
        help='Applied on invoice/credit note lines generated from Foodics order lines carrying '
             'this tax. Leave empty to post those lines with no tax (not recommended - orders '
             'using an unmapped tax are held back in "Needs Review" instead, see foodics.pos.order).')

    _sql_constraints = [
        ('foodics_tax_mapping_unique', 'unique(config_id, foodics_id)', 'This tax is already mapped.'),
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
                'rate': rec.get('rate') or 0.0,
            }
            if mapping:
                # Never touch account_tax_id here - that mapping is manual and must survive re-syncs.
                mapping.write(vals)
                updated += 1
            else:
                vals.update({'config_id': config.id, 'foodics_id': foodics_id})
                self.create(vals)
                created += 1
        return created, updated
