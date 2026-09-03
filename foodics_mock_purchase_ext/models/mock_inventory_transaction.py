import uuid

from odoo import fields, models

# Docs "Inventory Transactions > Types": 1 Purchasing, 2 Transfer Sending,
# 3 Quantity Adjustment, 4 Return to Supplier, 5 Production, 6 Transfer
# Receiving, 7 Consumption from Production, 8 Consumption from Order,
# 9 Return from Order, 10 Waste from Order, 11 Return from Transfer,
# 12 Waste from Production.
TX_TYPE_PURCHASING = 1
TX_TYPE_QUANTITY_ADJUSTMENT = 3
TX_TYPE_RETURN_TO_SUPPLIER = 4

# Docs "Inventory Transactions > Statuses": 1 Draft, 2 Pending,
# 3 Declined, 4 Closed.
TX_STATUS_DRAFT = 1
TX_STATUS_PENDING = 2
TX_STATUS_DECLINED = 3
TX_STATUS_CLOSED = 4


class FoodicsMockInventoryTransaction(models.Model):
    """Fake-server stand-in for the Foodics 'Inventory Transaction' object.

    Mirrors the API Docs PDF section "Inventory Transactions" (the single
    endpoint grouping Purchasing / Return to Supplier / etc.). Per the ERP
    guide these transactions - NOT purchase orders - are what actually move
    stock, so the mock computes live inventory levels from them.
    """
    _name = 'foodics.mock.inventory.transaction'
    _description = 'Foodics Mock Inventory Transaction'
    _rec_name = 'reference'
    _order = 'create_date desc'

    app_id = fields.Many2one('foodics.mock.app', required=True, ondelete='cascade', index=True)
    foodics_id = fields.Char(required=True, default=lambda s: uuid.uuid4().hex[:8], copy=False)
    business_date = fields.Char()
    reference = fields.Char()
    type = fields.Integer(default=TX_TYPE_PURCHASING)
    status = fields.Integer(default=TX_STATUS_DRAFT)
    paid_tax = fields.Float(default=0.0)
    additional_cost = fields.Float(default=0.0)
    notes = fields.Char()
    invoice_number = fields.Char()
    invoice_date = fields.Char()
    branch_id = fields.Char()
    other_branch_id = fields.Char()
    supplier_id = fields.Char()
    reason_id = fields.Char()
    order_id = fields.Char()
    creator_id = fields.Char()
    poster_id = fields.Char()
    tag_id = fields.Char()
    purchase_order_id = fields.Char(index=True)
    transfer_order_id = fields.Char()
    other_transaction_id = fields.Char()
    posted_at = fields.Datetime(copy=False)
    line_ids = fields.One2many('foodics.mock.inventory.transaction.line', 'transaction_id')
    raw_payload = fields.Text()

    _sql_constraints = [
        ('mock_tx_unique', 'unique(app_id, foodics_id)', 'Duplicate mock transaction id'),
    ]

    def api_dump(self):
        """Serialize exactly like the docs' Inventory Transaction sample."""
        self.ensure_one()
        return {
            'branch': {'id': self.branch_id} if self.branch_id else None,
            'other_branch': {'id': self.other_branch_id} if self.other_branch_id else None,
            'supplier': {'id': self.supplier_id} if self.supplier_id else None,
            'order': {'id': self.order_id} if self.order_id else None,
            'creator': {'id': self.creator_id} if self.creator_id else None,
            'poster': {'id': self.poster_id} if self.poster_id else None,
            'purchase_order': ({'id': self.purchase_order_id}
                               if self.purchase_order_id else None),
            'transfer_order': ({'id': self.transfer_order_id}
                               if self.transfer_order_id else None),
            'other_transaction': ({'id': self.other_transaction_id}
                                  if self.other_transaction_id else None),
            'items': [line.api_dump() for line in self.line_ids],
            'reason': {'id': self.reason_id} if self.reason_id else None,
            'id': self.foodics_id,
            'business_date': self.business_date or None,
            'reference': self.reference or None,
            'type': self.type,
            'status': self.status,
            'paid_tax': self.paid_tax,
            'additional_cost': self.additional_cost,
            'notes': self.notes or None,
            'invoice_number': self.invoice_number or None,
            'invoice_date': self.invoice_date or None,
            'created_at': fields.Datetime.to_string(self.create_date),
            'updated_at': fields.Datetime.to_string(self.write_date),
            'posted_at': fields.Datetime.to_string(self.posted_at) if self.posted_at else None,
        }

    def write(self, vals):
        if vals.get('status') == TX_STATUS_CLOSED and not vals.get('posted_at'):
            vals['posted_at'] = fields.Datetime.now()
        return super().write(vals)


class FoodicsMockInventoryTransactionLine(models.Model):
    """The TX item pivot: quantity + cost (docs sample shows only those two
    pivot keys on transactions)."""
    _name = 'foodics.mock.inventory.transaction.line'
    _description = 'Foodics Mock Inventory Transaction Line'

    transaction_id = fields.Many2one(
        'foodics.mock.inventory.transaction', required=True, ondelete='cascade')
    item_id = fields.Many2one('foodics.mock.inventory.item', required=True, ondelete='cascade')
    quantity = fields.Float(default=0.0)
    cost = fields.Float(default=0.0)

    def api_dump(self):
        self.ensure_one()
        return {
            'pivot': {'quantity': self.quantity, 'cost': self.cost},
            'id': self.item_id.foodics_id,
        }
