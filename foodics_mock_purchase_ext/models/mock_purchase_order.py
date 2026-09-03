import uuid

from odoo import fields, models

# Docs "Purchase Order > Statuses": 1 Draft, 2 Pending, 3 Approved,
# 4 Declined, 5 Partially Received, 6 Closed.
PO_STATUS_DRAFT = 1
PO_STATUS_PENDING = 2
PO_STATUS_APPROVED = 3
PO_STATUS_DECLINED = 4
PO_STATUS_PARTIAL = 5
PO_STATUS_CLOSED = 6


class FoodicsMockPurchaseOrder(models.Model):
    """Fake-server stand-in for the Foodics 'Purchase Order' object.

    Mirrors the API Docs PDF section "Purchase Order". Create is only
    accepted in Draft/Pending ("You can create a purchase order in draft &
    pending statuses only. If in pending status the submitter_id should be
    included."); Update accepts any status; Delete removes the record.
    """
    _name = 'foodics.mock.purchase.order'
    _description = 'Foodics Mock Purchase Order'
    _rec_name = 'reference'
    _order = 'create_date desc'

    app_id = fields.Many2one('foodics.mock.app', required=True, ondelete='cascade', index=True)
    foodics_id = fields.Char(required=True, default=lambda s: uuid.uuid4().hex[:8], copy=False)
    business_date = fields.Char()
    delivery_date = fields.Char()
    reference = fields.Char()
    additional_cost = fields.Float(default=0.0)
    status = fields.Integer(default=PO_STATUS_DRAFT)
    notes = fields.Char()
    branch_id = fields.Char()
    supplier_id = fields.Char()
    creator_id = fields.Char()
    submitter_id = fields.Char()
    poster_id = fields.Char()
    reviewed_at = fields.Datetime(copy=False)
    submitted_at = fields.Datetime(copy=False)
    closed_at = fields.Datetime(copy=False)
    line_ids = fields.One2many('foodics.mock.purchase.order.line', 'order_id')
    raw_payload = fields.Text()

    _sql_constraints = [
        ('mock_po_unique', 'unique(app_id, foodics_id)', 'Duplicate mock purchase order id'),
    ]

    def api_dump(self):
        """Serialize exactly like the docs' Purchase Order sample."""
        self.ensure_one()
        return {
            'items': [line.api_dump() for line in self.line_ids],
            'supplier': {'id': self.supplier_id, 'name': self._supplier_name()},
            'submitter': {'id': self.submitter_id, 'name': 'Chief'} if self.submitter_id else None,
            'branch': {'id': self.branch_id, 'name': self._branch_name()},
            'creator': {'id': self.creator_id, 'name': 'Chief'},
            'poster': {'id': self.poster_id, 'name': 'Chief'} if self.poster_id else None,
            'id': self.foodics_id,
            'business_date': self.business_date,
            'delivery_date': self.delivery_date or None,
            'reference': self.reference or None,
            'additional_cost': self.additional_cost,
            'status': self.status,
            'notes': self.notes or None,
            'created_at': fields.Datetime.to_string(self.create_date),
            'updated_at': fields.Datetime.to_string(self.write_date),
            'reviewed_at': fields.Datetime.to_string(self.reviewed_at) if self.reviewed_at else None,
            'closed_at': fields.Datetime.to_string(self.closed_at) if self.closed_at else None,
            'submitter_id': self.submitter_id or None,
            'submitted_at': fields.Datetime.to_string(self.submitted_at) if self.submitted_at else None,
        }

    def _supplier_name(self):
        sup = self.env['foodics.mock.supplier'].search([
            ('app_id', '=', self.app_id.id), ('foodics_id', '=', self.supplier_id)], limit=1)
        return sup.name if sup else 'Hyper Market'

    def _branch_name(self):
        branch = self.env['foodics.branch'].search([('foodics_id', '=', self.branch_id)], limit=1)
        return branch.name if branch else 'Branch 1'

    def apply_status(self, new_status):
        """Apply a status change with the doc'd side-effect timestamps."""
        for po in self:
            po.status = new_status
            now = fields.Datetime.now()
            if new_status == PO_STATUS_PENDING and not po.submitted_at:
                po.submitted_at = now
                po.submitter_id = po.submitter_id or po.creator_id
            elif new_status == PO_STATUS_APPROVED and not po.reviewed_at:
                po.reviewed_at = now
            elif new_status == PO_STATUS_CLOSED:
                po.closed_at = now


class FoodicsMockPurchaseOrderLine(models.Model):
    """The PO item pivot: quantity, cost, unit, unit_to_storage_factor and
    quantity_received (docs Purchase Order sample)."""
    _name = 'foodics.mock.purchase.order.line'
    _description = 'Foodics Mock Purchase Order Line'

    order_id = fields.Many2one('foodics.mock.purchase.order', required=True, ondelete='cascade')
    item_id = fields.Many2one('foodics.mock.inventory.item', required=True, ondelete='cascade')
    quantity = fields.Float(default=0.0)
    cost = fields.Float(default=0.0)
    unit = fields.Char(default='box')
    # "The conversion factor between order and storage units" (per-item on POs)
    unit_to_storage_factor = fields.Float(default=1.0)
    quantity_received = fields.Float(default=0.0)

    def api_dump(self):
        self.ensure_one()
        return {
            'pivot': {
                'quantity': self.quantity,
                'cost': self.cost,
                'unit': self.unit,
                'unit_to_storage_factor': self.unit_to_storage_factor,
                'quantity_received': self.quantity_received,
            },
            'id': self.item_id.foodics_id,
            'name': self.item_id.name,
            'sku': self.item_id.sku,
        }
