from odoo import api, fields, models


class FoodicsBranch(models.Model):
    _name = 'foodics.branch'
    _description = 'Foodics Branch'
    _rec_name = 'name'

    config_id = fields.Many2one('foodics.config', required=True, ondelete='cascade')
    foodics_id = fields.Char(required=True, string='Foodics ID')
    name = fields.Char(required=True)
    reference = fields.Char()
    receives_online_orders = fields.Boolean(
        help='Mirrors the same flag in Foodics. Branches with this off should not be picked as a '
             'destination when pushing an order - Foodics does not accept online orders for them.')
    opening_from = fields.Char()
    opening_to = fields.Char()
    active = fields.Boolean(default=True)

    # --- The Odoo side of the branch mapping -----------------------------
    # See docs/base_and_mock.html ("Branches: how Foodics and Odoo agree on
    # a place") for the full explanation. Short version: Foodics has no way
    # to tell us which Odoo Warehouse/Company a branch corresponds to - a
    # human has to say so once, the same way menu products get mapped.
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Odoo Warehouse',
        help='One Foodics branch = one physical restaurant/location. Map it to the Odoo '
             'Warehouse that represents that same location, so Sale Orders and stock '
             'movements for this branch land in the right place.')
    company_id = fields.Many2one(
        'res.company', string='Company', related='warehouse_id.company_id', store=True, readonly=True,
        help='Filled in automatically from the mapped Warehouse.')

    _sql_constraints = [
        ('foodics_branch_unique', 'unique(config_id, foodics_id)', 'This branch is already synced.'),
    ]

    @api.model
    def _sync_from_foodics(self, config, records):
        """Upsert branches pulled from GET /branches. Never touches
        warehouse_id/company_id - that mapping is manual and must survive
        re-syncs.
        """
        created = updated = 0
        for rec in records:
            foodics_id = rec.get('id')
            if not foodics_id:
                continue
            branch = self.search([('config_id', '=', config.id), ('foodics_id', '=', foodics_id)], limit=1)
            vals = {
                'config_id': config.id,
                'foodics_id': foodics_id,
                'name': rec.get('name'),
                'reference': rec.get('reference'),
                'receives_online_orders': rec.get('receives_online_orders', False),
                'opening_from': rec.get('opening_from'),
                'opening_to': rec.get('opening_to'),
            }
            if branch:
                branch.write(vals)
                updated += 1
            else:
                self.create(vals)
                created += 1
        return created, updated
