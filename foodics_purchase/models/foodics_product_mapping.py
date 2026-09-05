from odoo import api, fields, models


class FoodicsProductMapping(models.Model):
    """Additive: cross-reference to the purchase-side mapping.

    foodics_base owns foodics.product.mapping (sale-side "menu products"); this module owns
    foodics.inventory.item (purchase-side "inventory items"). See the reasoning on
    foodics.inventory.item.linked_sale_mapping_id - same idea, other direction.
    """
    _inherit = 'foodics.product.mapping'

    linked_inventory_item_id = fields.Many2one(
        'foodics.inventory.item', compute='_compute_linked_inventory_item',
        string='Linked Inventory Item',
        help='The Foodics inventory item (purchase-side mapping) sharing this menu product\'s '
             'Odoo product, if any. Most menu products won\'t have one - a sold "Burger" is '
             'built from ingredient inventory items, it isn\'t itself purchased.')

    @api.depends('config_id', 'product_id')
    def _compute_linked_inventory_item(self):
        Item = self.env['foodics.inventory.item']
        for mapping in self:
            mapping.linked_inventory_item_id = Item.search([
                ('config_id', '=', mapping.config_id.id),
                ('product_id', '=', mapping.product_id.id),
            ], limit=1) if mapping.product_id else Item.browse()
