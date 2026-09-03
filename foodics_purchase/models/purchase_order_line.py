from odoo import api, fields, models


class PurchaseOrderLine(models.Model):
    """Adds the Foodics mapping needed to translate Odoo PO lines into the
    documented POST/PUT /purchase_orders items pivot:

        items[{"id": <inventory item id>,
               "quantity": ..., "cost": ...,
               "unit": <order unit>, "unit_to_storage_factor": ...}]

    Quantity policy (documented simplification): the quantity entered on
    the Odoo line IS expressed in the Foodics *order unit* taken from the
    supplier pivot - exactly what a buyer orders from that supplier.
    Storage conversion happens downstream when receipts are pushed as
    inventory transactions (see stock_picking.py).
    """
    _inherit = 'purchase.order.line'

    foodics_item_id = fields.Many2one(
        'foodics.inventory.item', string='Foodics Inventory Item',
        domain="[('config_id', '=', parent.foodics_config_id),"
               "('active', '=', True)]",
        help='The Foodics inventory item this line purchases.')
    foodics_order_unit = fields.Char(
        compute='_compute_foodics_pivot', string='Order Unit')
    foodics_unit_to_storage_factor = fields.Float(
        compute='_compute_foodics_pivot', string='Order→Storage Factor')
    foodics_moq = fields.Float(
        compute='_compute_foodics_pivot',
        string='Minimum Order Qty')

    @api.depends('foodics_item_id', 'order_id.partner_id')
    def _compute_foodics_pivot(self):
        """Pull order unit / factor / MOQ from the supplier pivot
        (docs Suppliers section: order_unit, order_to_storage_factor,
        minimum_order_quantity)."""
        for line in self:
            supplier = line.order_id.foodics_supplier_id
            pivot = self.env['foodics.supplier.item'].search([
                ('supplier_id', '=', supplier.id),
                ('item_id', '=', line.foodics_item_id.id),
            ], limit=1) if supplier and line.foodics_item_id else self.env[
                'foodics.supplier.item'].browse()
            line.foodics_order_unit = pivot.order_unit or (
                line.foodics_item_id.storage_unit if line.foodics_item_id else False)
            line.foodics_unit_to_storage_factor = \
                pivot.order_to_storage_factor or 1.0
            line.foodics_moq = pivot.minimum_order_quantity or 0.0

    @api.onchange('product_id', 'order_id')
    def _onchange_product_id_foodics(self):
        """Auto-map the inventory item when a product has exactly one match
        on the selected connection."""
        for line in self:
            if line.foodics_item_id or not line.product_id:
                continue
            config = line.order_id.foodics_config_id
            if not config:
                continue
            matches = self.env['foodics.inventory.item'].search([
                ('config_id', '=', config.id),
                ('product_id', '=', line.product_id.id)], limit=1)
            if matches:
                line.foodics_item_id = matches

    def _foodics_payload_item(self):
        """Build one doc-shaped item dict for this line."""
        self.ensure_one()
        return {
            'id': self.foodics_item_id.foodics_id,
            'quantity': self.product_uom_qty,
            'cost': self.price_unit,
            'unit': self.foodics_order_unit,
            'unit_to_storage_factor': self.foodics_unit_to_storage_factor or 1.0,
        }
