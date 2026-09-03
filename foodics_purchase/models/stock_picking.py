import json

from odoo import api, fields, models, _

from odoo.addons.foodics_purchase.models.foodics_api import FoodicsApiException


class StockPicking(models.Model):
    """Pushes real stock movements to Foodics as Inventory Transactions.

    Implements the ERP-guide rule from the developer PDF: "/purchase_orders
    ... do not directly reflect actual inventory transactions impacting
    inventory quantities at stores. For actual transactions, rely solely on
    the /inventory_transactions endpoint." Hence:

      - validating a receipt (picking_type_code == 'incoming') whose PO was
        pushed to Foodics creates an Inventory Transaction of type 1
        "Purchasing" referencing purchase_order_id (docs Create Inventory
        Transaction request). Partial deliveries naturally produce one
        transaction per validated picking; backorders follow later.
      - validating a return of a received product (outgoing picking whose
        moves have origin_returned_move_id pointing at a synced receipt)
        creates a type 4 "Return to Supplier" transaction.

    Quantities/costs are expressed in STORAGE units on transaction items
    (the docs' TX item pivot carries only quantity+cost), converted from
    order units with each PO line's unit_to_storage_factor.
    """
    _inherit = 'stock.picking'

    foodics_tx_id = fields.Char(string='Foodics TX ID', copy=False, readonly=True,
                                index=True)
    foodics_sync_state = fields.Selection([
        ('not_needed', 'Not Needed'),
        ('synced', 'Synced'),
        ('error', 'Sync Error'),
    ], default='not_needed', string='Foodics Sync State', copy=False, readonly=True)

    def button_validate(self):
        """Validate locally first (Odoo stays source of truth), then mirror
        upstream; transient failures land in the retry queue instead of
        blocking warehouse work."""
        result = super().button_validate()
        if isinstance(result, dict) or self.env.context.get('foodics_skip_push'):
            # wizard shown (backorder/immediate transfer) or suppressed -
            # the actual done-state push happens when validation completes
            return result
        for picking in self:
            if picking.state != 'done':
                continue
            try:
                picking._foodics_post_stock_transaction()
            except FoodicsApiException:
                picking.foodics_sync_state = 'error'
        return result

    # ------------------------------------------------------------------
    def _foodics_post_stock_transaction(self):
        self.ensure_one()
        receipt_po = self._foodics_linked_purchase_order()
        returned_po, origin_picking = False, False
        if not receipt_po:
            returned_po, origin_picking = self._foodics_return_context()

        items, mismatch_notes = [], []
        if receipt_po:
            items, mismatch_notes = self._foodics_build_receipt_items(receipt_po)
            tx_type = self.env['foodics.api'].TX_TYPE_PURCHASING
        elif returned_po:
            items, mismatch_notes = self._foodics_build_return_items(returned_po)
            tx_type = self.env['foodics.api'].TX_TYPE_RETURN_TO_SUPPLIER
        else:
            self.foodics_sync_state = 'not_needed'
            return
        if not items:
            self.foodics_sync_state = 'not_needed'
            return

        po = receipt_po or returned_po
        payload = {
            # docs statuses: post Closed(4) so levels move immediately;
            # draft/pending would require console approval upstream
            'status': self.env['foodics.api'].TX_STATUS_CLOSED,
            'type': tx_type,
            'business_date': fields.Date.to_string(fields.Date.context_today(self)),
            'reference': self.name,
            'notes': _('Receipt of %s') % po.name if receipt_po else
                     _('Return against %s') % po.name,
            'invoice_number': False,
            'invoice_date': False,
            'paid_tax': 0,
            'additional_cost': 0,
            'branch_id': po.foodics_branch_id.foodics_id,
            'supplier_id': po.foodics_supplier_id.foodics_id,
            'creator_id': po.foodics_config_id.po_default_creator_id,
            'poster_id': po.foodics_config_id.po_default_creator_id,
            'purchase_order_id': po.foodics_po_id,
            'items': items,
        }
        entry = self._foodics_queue_entry('post_tx', 'POST',
                                          '/inventory_transactions', payload)
        data = self.env['foodics.api'].execute(entry)
        row = data.get('data') or {}
        self.write({
            'foodics_tx_id': row.get('id'),
            'foodics_sync_state': 'synced',
        })
        for note in mismatch_notes:
            entry._mark_warning(note)
        # refresh the PO mirror so Partially Received / Closed show up
        po._foodics_refresh_after_movement()

    def _foodics_queue_entry(self, operation, method, endpoint, payload):
        return self.env['foodics.sync.log'].create({
            'config_id': (self.purchase_id.foodics_config_id or
                          self.env['foodics.config'].search([], limit=1)).id,
            'direction': 'push',
            'operation': operation,
            'res_model': self._name,
            'res_id': self.id,
            'method': method,
            'endpoint': endpoint,
            'payload_json': json.dumps(payload),
            'state': 'pending',
        })

    # ------------------------------------------------------------------
    def _foodics_linked_purchase_order(self):
        """The pushed PO this receipt belongs to (if any)."""
        self.ensure_one()
        po = self.purchase_id
        if po and po.foodics_po_id and \
                self.picking_type_code == 'incoming':
            return po
        return self.env['purchase.order'].browse()

    def _foodics_return_context(self):
        """Detect returns-to-supplier of previously synced receipts.

        In Odoo, returning a receipt creates an OUTGOING picking (verified:
        in_type.return_picking_type_id = out_type). We trace each move back
        through origin_returned_move_id to find the originating receipt and
        its Foodics-linked PO."""
        self.ensure_one()
        if self.picking_type_code != 'outgoing':
            return self.env['purchase.order'].browse(), False
        for move in self.move_ids:
            origin_move = move.origin_returned_move_id
            while origin_move and origin_move.origin_returned_move_id:
                origin_move = origin_move.origin_returned_move_id
            if not origin_move:
                continue
            source_po = origin_move.purchase_line_id.order_id if \
                origin_move.purchase_line_id else origin_move.picking_id.purchase_id
            if source_po and source_po.foodics_po_id:
                return source_po, origin_move.picking_id
        return self.env['purchase.order'].browse(), False

    # ------------------------------------------------------------------
    def _foodics_item_for_product(self, po, product):
        return self.env['foodics.inventory.item'].search([
            ('config_id', '=', po.foodics_config_id.id),
            ('product_id', '=', product.id)], limit=1)

    def _foodics_build_receipt_items(self, po):
        """Doc-shaped items[] from this picking's moves:
        storage_qty = order_units x unit_to_storage_factor
        storage_cost = price_per_order_unit / factor   (totals preserved)
        Also flags price deviations beyond the configured tolerance using
        the PO line as reference."""
        tolerance = po.foodics_config_id.po_price_tolerance_pct or 0.0
        items, notes = [], []
        for move in self.move_ids.filtered(
                lambda m: m.state == 'done' and m.quantity and m.purchase_line_id):
            line = move.purchase_line_id
            item = line.foodics_item_id or self._foodics_item_for_product(
                po, move.product_id)
            if not item:
                notes.append(_('No Foodics item mapped for %s - skipped.')
                             % move.product_id.display_name)
                continue
            factor = line.foodics_unit_to_storage_factor or 1.0
            qty_done = abs(move.quantity)
            items.append({
                'id': item.foodics_id,
                'quantity': item.order_qty_to_storage(qty_done, factor),
                'cost': item.storage_cost_per_unit(line.price_unit, factor),
            })
            if tolerance and line.price_unit:
                expected = line.price_unit
                sent = item.storage_cost_per_unit(line.price_unit, factor) * factor
                deviation = abs(sent - expected) / expected * 100.0
                if deviation > tolerance:
                    notes.append(_('%s: effective cost %.4f deviates %.2f%% '
                                   'from PO line (%.4f).')
                                 % (item.name, sent, deviation, expected))
        return items, notes

    def _foodics_build_return_items(self, po):
        """Same conversion for returned quantities (positive numbers, type 4
        transaction handles the direction)."""
        items, notes = [], []
        for move in self.move_ids.filtered(lambda m: m.quantity):
            origin_move = move.origin_returned_move_id
            line = origin_move.purchase_line_id if origin_move else \
                move.purchase_line_id
            item = (line.foodics_item_id if line else False) or \
                self._foodics_item_for_product(po, move.product_id)
            if not item:
                notes.append(_('No Foodics item mapped for %s - skipped.')
                             % move.product_id.display_name)
                continue
            factor = line.foodics_unit_to_storage_factor if line else 1.0
            factor = factor or 1.0
            items.append({
                'id': item.foodics_id,
                'quantity': item.order_qty_to_storage(abs(move.quantity), factor),
                'cost': item.storage_cost_per_unit(line.price_unit if line else 0.0,
                                                   factor),
            })
        return items, notes

    # ------------------------------------------------------------------
    def _foodics_apply_post_tx(self, entry, data):
        """Queued-success handler: store the returned transaction id."""
        row = data.get('data') or {}
        if not self.exists():
            return
        self.write({
            'foodics_tx_id': row.get('id'),
            'foodics_sync_state': 'synced',
        })
        po = self._foodics_linked_purchase_order() or \
            self._foodics_return_context()[0]
        if po:
            po._foodics_refresh_after_movement()
