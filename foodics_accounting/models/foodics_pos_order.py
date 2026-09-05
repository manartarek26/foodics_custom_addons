import json
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Foodics order.status (see their Orders resource docs) - only 4 and 5 are
# accounting-relevant per the "Accounting/ERP Integration" guide: "ignore any
# webhooks/orders with a status other than 4 or 5". Everything else (still
# open at the till, voided before payment, ...) has no financial impact yet.
STATUS_TO_STATE = {
    4: 'to_invoice',   # Closed
    5: 'to_refund',    # Returned
}

# A line/charge amount is considered fully explained by our lines once the
# invoice total lands within this tolerance of Foodics' own total_price -
# small differences here are almost always a missing tax/payment mapping,
# not a bug, so we hold the record for a human rather than silently posting
# a wrong total.
TOTAL_TOLERANCE = 0.05


class FoodicsPosOrder(models.Model):
    """One row per Foodics order that reached status 'Closed' or 'Returned'
    - i.e. one row per Foodics sale that should become an Odoo invoice or
    credit note. Populated identically whether it arrives via webhook
    (`foodics_webhook_log.py`) or via the pull cron/button
    (`foodics_config.py`): both funnel into `_sync_from_payload()` below,
    which upserts by (config, foodics_order_id) and only ever creates the
    invoice/payments once - re-running either path (a retried webhook, a
    re-run of the pull cron) is always a safe no-op on top of what's already
    there.
    """
    _name = 'foodics.pos.order'
    _description = 'Foodics POS Order'
    _order = 'business_date desc, foodics_reference desc'
    _rec_name = 'foodics_order_id'

    config_id = fields.Many2one('foodics.config', required=True, ondelete='cascade')
    branch_id = fields.Many2one('foodics.branch', string='Branch')
    foodics_order_id = fields.Char(string='Foodics Order UUID', required=True, copy=False)
    foodics_reference = fields.Integer(
        string='Foodics #', copy=False,
        help='Foodics order `number` - a per-business sequential integer, used as the pull '
             'cursor (see foodics.config.last_order_reference).')
    foodics_status = fields.Integer(readonly=True, copy=False, string='Foodics Status (raw)')
    order_type = fields.Integer(readonly=True, help='Foodics order.type: 1 Dine In, 2 Delivery, '
                                                       '3 Pickup, 4 Drive Thru, ...')

    state = fields.Selection([
        ('new', 'New'),
        ('ignored', 'Not Accounting-Relevant'),
        ('to_invoice', 'To Invoice'),
        ('invoiced', 'Invoiced'),
        ('to_refund', 'To Credit Note'),
        ('refunded', 'Credit Noted'),
        ('needs_review', 'Needs Review'),
    ], default='new', required=True, copy=False)
    error_message = fields.Text(readonly=True, copy=False)

    partner_id = fields.Many2one('res.partner', string='Customer')
    currency_id = fields.Many2one('res.currency', related='config_id.company_id.currency_id')
    business_date = fields.Date()
    opened_at = fields.Datetime()
    closed_at = fields.Datetime()

    subtotal_price = fields.Float()
    discount_amount = fields.Float()
    rounding_amount = fields.Float()
    total_price = fields.Float()

    order_line_ids = fields.One2many('foodics.pos.order.line', 'order_id')
    payment_ids = fields.One2many('foodics.pos.order.payment', 'order_id')

    original_order_id = fields.Many2one(
        'foodics.pos.order', readonly=True, copy=False,
        help='For a return (state to_refund/refunded): the order it returns, resolved from the '
             'Foodics `original_order.id` on the payload when present. Used to link the credit '
             'note back to the original invoice (account.move.reversed_entry_id) for '
             'traceability - see _create_via_pos_refund(). Left empty if Foodics did not report '
             'it or the original order was never itself synced (e.g. pre-dates this connection).')

    pos_order_id = fields.Many2one(
        'pos.order', readonly=True, copy=False,
        help='The real Odoo POS order this Foodics order was turned into - built from the '
             'parsed lines/payments in the branch\'s persistent POS session, then invoiced '
             '(see _create_via_pos_order()/_create_via_pos_refund()). A return gets its own '
             'pos.order with negated quantities rather than reusing this field for two things.')
    pos_order_state = fields.Selection(related='pos_order_id.state', string='POS Order Status')
    invoice_id = fields.Many2one('account.move', readonly=True, copy=False, string='Invoice/Credit Note')
    invoice_move_state = fields.Selection(related='invoice_id.state', string='Invoice Status')
    invoice_payment_state = fields.Selection(related='invoice_id.payment_state', string='Payment Status')

    raw_payload = fields.Text(readonly=True, copy=False)

    _sql_constraints = [
        ('foodics_pos_order_unique', 'unique(config_id, foodics_order_id)',
         'This Foodics order has already been imported.'),
    ]

    # ------------------------------------------------------------------
    # Entry point shared by the webhook handler and the pull sync/cron
    # ------------------------------------------------------------------
    @api.model
    def _sync_from_payload(self, config, order_data):
        foodics_order_id = order_data.get('id')
        if not foodics_order_id:
            raise ValueError('Foodics order payload has no id')
        order = self.search([('config_id', '=', config.id), ('foodics_order_id', '=', foodics_order_id)], limit=1)
        if not order:
            order = self.create({'config_id': config.id, 'foodics_order_id': foodics_order_id})
        order._apply_payload(order_data)
        return order

    def _apply_payload(self, data):
        self.ensure_one()
        self.raw_payload = json.dumps(data, indent=2, default=str)

        branch = self.env['foodics.branch']
        branch_data = data.get('branch') or {}
        if branch_data.get('id'):
            branch = self.env['foodics.branch'].search(
                [('config_id', '=', self.config_id.id), ('foodics_id', '=', branch_data['id'])], limit=1)

        self.write({
            'branch_id': branch.id if branch else self.branch_id.id,
            'foodics_reference': data.get('number') or self.foodics_reference,
            'foodics_status': data.get('status') or 0,
            'order_type': data.get('type') or self.order_type,
            'business_date': data.get('business_date') or self.business_date,
            'opened_at': data.get('opened_at') or self.opened_at,
            'closed_at': data.get('closed_at') or self.closed_at,
            'subtotal_price': data.get('subtotal_price', 0.0),
            'discount_amount': data.get('discount_amount', 0.0),
            'rounding_amount': data.get('rounding_amount', 0.0),
            'total_price': data.get('total_price', 0.0),
            'partner_id': self._resolve_partner(data).id,
            'original_order_id': self._resolve_original_order(data).id,
        })

        self._apply_lines(data)
        self._apply_payments(data)

        # Once invoiced/credit-noted, a later update (e.g. Foodics correcting
        # a typo after close) must not silently flip the record back to
        # 'to_invoice' and re-trigger invoicing - only the still-open states
        # follow the latest Foodics status.
        if self.state not in ('invoiced', 'refunded'):
            self.state = STATUS_TO_STATE.get(data.get('status'), 'ignored')

        if self.state in ('to_invoice', 'to_refund') and not self.invoice_id:
            try:
                self._create_invoice()
            except Exception as e:  # noqa: BLE001 - never let a bad order block the webhook/cron;
                # the failure is recorded here for a human to fix (mapping, journal, ...) and
                # retried by the "Retry Pending Invoices" cron or the button on this form.
                _logger.exception('Foodics: could not invoice order %s', self.foodics_order_id)
                self.write({'state': 'needs_review', 'error_message': str(e)})

    def _resolve_partner(self, data):
        self.ensure_one()
        Partner = self.env['res.partner']
        customer = data.get('customer') or {}
        if customer.get('id'):
            partner = Partner.search([('foodics_customer_id', '=', customer['id'])], limit=1)
            if not partner:
                partner = Partner.create({
                    'name': customer.get('name') or _('Foodics Customer'),
                    'phone': customer.get('phone'),
                    'email': customer.get('email'),
                    'foodics_customer_id': customer['id'],
                    'company_id': self.config_id.company_id.id,
                })
            return partner
        return self.partner_id or self.config_id.default_customer_id

    def _resolve_original_order(self, data):
        """For a return: the order it returns, if Foodics reported `original_order.id` and
        that order was itself synced already (it must have been Closed - and therefore synced -
        before it could be returned, so this is normally already there). Used to link the
        credit note to the original invoice - see _create_via_pos_refund().
        """
        self.ensure_one()
        original = data.get('original_order') or {}
        if original.get('id'):
            found = self.search(
                [('config_id', '=', self.config_id.id), ('foodics_order_id', '=', original['id'])], limit=1)
            if found:
                return found
        return self.original_order_id

    # ------------------------------------------------------------------
    # Lines: top-level products, combo products (flattened), and charges.
    # ------------------------------------------------------------------
    def _apply_lines(self, data):
        self.ensure_one()
        Line = self.env['foodics.pos.order.line']
        self.order_line_ids.unlink()

        vals_list = []
        sequence = 10
        for item in data.get('products', []) or []:
            vals_list.append(self._line_vals_from_product(item, sequence, is_combo=False))
            sequence += 10
        for combo in data.get('combos', []) or []:
            for item in combo.get('products', []) or []:
                vals_list.append(self._line_vals_from_product(item, sequence, is_combo=True))
                sequence += 10
        for item in data.get('charges', []) or []:
            vals_list.append(self._charge_vals(item, sequence))
            sequence += 10

        for vals in vals_list:
            Line.create(dict(vals, order_id=self.id))

    def _resolve_tax_ids(self, tax_entries):
        tax_ids = []
        for t in tax_entries or []:
            if not t.get('id'):
                continue
            mapping = self.env['foodics.tax.mapping'].search(
                [('config_id', '=', self.config_id.id), ('foodics_id', '=', t['id'])], limit=1)
            if mapping and mapping.account_tax_id:
                tax_ids.append(mapping.account_tax_id.id)
        return tax_ids

    def _line_vals_from_product(self, item, sequence, is_combo):
        product = item.get('product') or {}
        mapping = self.env['foodics.product.mapping']
        if product.get('id'):
            mapping = mapping.search(
                [('config_id', '=', self.config_id.id), ('foodics_id', '=', product['id'])], limit=1)
        return {
            'sequence': sequence,
            'foodics_line_id': item.get('id'),
            'name': product.get('name') or _('Foodics Item'),
            'is_combo': is_combo,
            'product_mapping_id': mapping.id if mapping else False,
            'quantity': item.get('quantity') or 1,
            'discount_amount': item.get('discount_amount', 0.0),
            'price_subtotal': item.get('tax_exclusive_total_price', item.get('total_price', 0.0)),
            'tax_ids': [(6, 0, self._resolve_tax_ids(item.get('taxes')))],
        }

    def _charge_vals(self, item, sequence):
        charge = item.get('charge') or {}
        return {
            'sequence': sequence,
            'foodics_line_id': item.get('id'),
            'name': charge.get('name') or _('Service Charge'),
            'is_charge': True,
            'quantity': 1,
            'price_subtotal': item.get('tax_exclusive_amount', item.get('amount', 0.0)),
            'tax_ids': [(6, 0, self._resolve_tax_ids(item.get('taxes')))],
        }

    # ------------------------------------------------------------------
    # Payments
    # ------------------------------------------------------------------
    def _apply_payments(self, data):
        self.ensure_one()
        Payment = self.env['foodics.pos.order.payment']
        PayMap = self.env['foodics.payment.method']
        existing_by_foodics_id = {p.foodics_payment_id: p for p in self.payment_ids if p.foodics_payment_id}

        for pay in data.get('payments', []) or []:
            foodics_payment_id = pay.get('id')
            if foodics_payment_id and foodics_payment_id in existing_by_foodics_id:
                continue  # already recorded - never touch a payment we may have already reconciled
            method_data = pay.get('payment_method') or {}
            mapping = PayMap
            if method_data.get('id'):
                mapping = PayMap.search(
                    [('config_id', '=', self.config_id.id), ('foodics_id', '=', method_data['id'])], limit=1)
            Payment.create({
                'order_id': self.id,
                'foodics_payment_id': foodics_payment_id,
                'foodics_payment_method_id': method_data.get('id'),
                'payment_method_id': mapping.id if mapping else False,
                'amount': pay.get('amount', 0.0),
                'tendered': pay.get('tendered', 0.0),
                'tips': pay.get('tips', 0.0),
                'business_date': pay.get('business_date') or self.business_date,
            })

    # ------------------------------------------------------------------
    # Shared line building - one per product/combo-item/charge, plus a
    # rounding line if Foodics reported one. Used to build pos.order.line
    # vals for both the invoice path (to_invoice) and the refund path
    # (to_refund) from the same parsed foodics.pos.order.line rows, so the
    # two paths can't drift apart on how a line's amount/tax gets computed.
    # ------------------------------------------------------------------
    def _order_line_common_vals(self):
        self.ensure_one()
        common = []
        for line in self.order_line_ids:
            common.append({
                'name': line.name,
                'product_id': line.product_mapping_id.product_id.id if line.product_mapping_id else False,
                'quantity': line.quantity,
                'price_unit': (line.price_subtotal / line.quantity) if line.quantity else line.price_subtotal,
                'tax_ids': line.tax_ids.ids,
            })
        if abs(self.rounding_amount) > 0.005:
            common.append({
                'name': _('Rounding'), 'product_id': False, 'quantity': 1,
                'price_unit': self.rounding_amount, 'tax_ids': [],
            })
        return common

    def _pos_order_line_vals_list(self, negate=False):
        """Same shape as _order_line_common_vals, but a pos.order.line (like a sale.order.line)
        cannot exist without a product at all - Odoo blocks it outright. A line ends up without
        one whenever it's a Foodics charge (never has a product), a rounding line, or a product
        Foodics sent that has no foodics.product.mapping yet - so those need the connection's
        Fallback Product substituted in, or a clear error instead of a confusing failure from
        the pos.order create() itself. `negate=True` (the return path) flips quantity so the
        resulting order totals negative - Odoo then picks 'out_refund' automatically when this
        gets invoiced (see _prepare_invoice_vals() on pos.order).
        """
        self.ensure_one()
        common = self._order_line_common_vals()
        if any(not c['product_id'] for c in common) and not self.config_id.fallback_product_id:
            raise UserError(_(
                'One or more lines on this order have no mapped Foodics product (or are a '
                'charge/rounding line) - a POS order line cannot be created without a product. '
                'Set a Fallback Product on the Foodics Connection, or map the missing product(s) '
                'under Foodics > Product Mapping, then click "Retry".'))
        return [(0, 0, {
            'name': c['name'],
            'product_id': c['product_id'] or self.config_id.fallback_product_id.id,
            'qty': -c['quantity'] if negate else c['quantity'],
            'price_unit': c['price_unit'],
            'discount': 0,
            'tax_ids': [(6, 0, c['tax_ids'])],
            # price_subtotal/price_subtotal_incl are required=True with no default and no
            # @api.depends compute (only an @api.onchange, UI-only) - a bare create() with them
            # omitted inserts NULL and violates the DB constraint. Seed them at 0.0 here;
            # _finalize_pos_order_amounts() overwrites them with the real computed values right
            # after creation, via the same _compute_amount_line_all() the UI onchange calls.
            'price_subtotal': 0.0,
            'price_subtotal_incl': 0.0,
        }) for c in common]

    def _pos_payment_vals_list(self, negate=False):
        """(0, 0, {...}) dicts for pos.payment, one per foodics.pos.order.payment. Unlike a
        product, there's no safe "fallback" for money movement - an unmapped Foodics payment
        method holds the whole order in Needs Review (see _create_via_pos_order()) rather than
        guessing which Odoo POS payment method it should land in. `negate=True` (the return path)
        flips the sign so the payments add up to the refund order's negative total - Foodics
        reports a return's payments with the same positive sign as the original sale (see
        _create_via_pos_refund()).
        """
        self.ensure_one()
        vals_list = []
        for pay in self.payment_ids:
            mapping = pay.payment_method_id
            if not mapping or not mapping.pos_payment_method_id:
                raise UserError(_(
                    'Foodics payment method "%s" on this order has no Odoo POS Payment Method '
                    'mapped - map it under Foodics > Payment Methods, then click "Retry".'
                ) % (pay.payment_method_id.name or pay.foodics_payment_method_id or _('Unknown')))
            vals_list.append((0, 0, {
                'payment_method_id': mapping.pos_payment_method_id.id,
                'amount': -pay.amount if negate else pay.amount,
                'payment_date': pay.business_date or self.business_date or fields.Date.context_today(self),
            }))
        return vals_list

    # ------------------------------------------------------------------
    # POS session / order plumbing
    # ------------------------------------------------------------------
    def _get_or_create_session(self):
        """One persistent, lazily-opened session per branch - reused for every order. Closing
        it has no accounting effect in this design (every order is invoiced individually the
        moment it's synced, so nothing is ever left for the session-close bundle entry to pick
        up) - it's purely optional reporting hygiene, not automated here.
        """
        self.ensure_one()
        config = self.branch_id.pos_config_id
        if not config:
            raise UserError(_('Branch "%s" has no Odoo POS Point mapped yet - set one under '
                               'Foodics > Branches first.') % (self.branch_id.name or self.branch_id.foodics_id))
        return config.current_session_id or self.env['pos.session'].create({'config_id': config.id})

    @api.model
    def _zero_pos_amounts(self):
        """amount_tax/amount_total/amount_paid/amount_return are required=True on pos.order with
        no default and no @api.depends compute (only an @api.onchange, UI-only) - a bare create()
        with them omitted inserts NULL and violates the DB constraint. Seed them here;
        _finalize_pos_order_amounts() overwrites them with the real computed values right after.
        """
        return {'amount_tax': 0.0, 'amount_total': 0.0, 'amount_paid': 0.0, 'amount_return': 0.0}

    def _finalize_pos_order_amounts(self, order):
        """pos.order/pos.order.line amount fields are plain stored fields wired only to
        @api.onchange handlers (UI-only) - they are NOT recomputed by a bare ORM create()/write().
        _compute_amount_line_all()/_compute_prices() are the same plain, callable methods the UI
        onchange uses under the hood, safe to call directly from server code.
        """
        for line in order.lines:
            line.write(line._compute_amount_line_all())
        order._compute_prices()
        # Odoo buffers stored-field writes and only issues the actual SQL at the next flush point
        # (often only at the end of the whole HTTP request/commit) - a DB constraint violation
        # (e.g. a required field left NULL) would otherwise surface way outside the try/except in
        # _apply_payload()/action_retry(), breaking the whole request instead of landing safely in
        # 'needs_review'. Flushing here, still inside our own try/except, catches that early.
        order.flush_recordset()

    # ------------------------------------------------------------------
    # Invoice / credit note generation
    # ------------------------------------------------------------------
    def _create_invoice(self):
        self.ensure_one()
        if self.invoice_id:
            return self.invoice_id
        if self.state not in ('to_invoice', 'to_refund'):
            raise UserError(_('Only orders in "To Invoice"/"To Credit Note" can be invoiced.'))
        partner = self.partner_id or self.config_id.default_customer_id
        if not partner:
            raise UserError(_('No customer on this order and no Default Customer set on the '
                               'Foodics Connection - set one of the two first.'))

        if self.state == 'to_refund':
            return self._create_via_pos_refund(partner)
        return self._create_via_pos_order(partner)

    def _create_via_pos_order(self, partner):
        """The invoice path: build (or reuse) a real pos.order from the order lines/payments in
        the branch's persistent session, mark it paid, then invoice it via Odoo's own
        action_pos_order_invoice() - which posts the invoice and reconciles the pos.payments
        against it automatically (_apply_invoice_payments()), no manual payment-registration
        needed. This is what puts these sales into real POS reporting instead of a
        bookkeeping-only entry.
        """
        self.ensure_one()
        if self.pos_order_id:
            order = self.pos_order_id
            order.lines.unlink()
            order.payment_ids.unlink()
            order.write({
                'lines': self._pos_order_line_vals_list(),
                'payment_ids': self._pos_payment_vals_list(),
            })
        else:
            session = self._get_or_create_session()
            order = self.env['pos.order'].create({
                **self._zero_pos_amounts(),
                'session_id': session.id,
                'company_id': self.config_id.company_id.id,
                'partner_id': partner.id,
                'date_order': self.opened_at or fields.Datetime.now(),
                'lines': self._pos_order_line_vals_list(),
                'payment_ids': self._pos_payment_vals_list(),
            })
            self.pos_order_id = order.id
        self._finalize_pos_order_amounts(order)

        if abs(order.amount_total - self.total_price) > TOTAL_TOLERANCE:
            self.write({
                'state': 'needs_review',
                'error_message': _(
                    'POS order %(po)s total %(po_total)s does not match Foodics order total '
                    '%(foodics_total)s (likely a missing tax/payment mapping). Fix the mapping, '
                    'then click "Retry".'
                ) % {'po': order.name, 'po_total': order.amount_total, 'foodics_total': self.total_price},
            })
            return False

        try:
            order.action_pos_order_paid()
        except UserError as e:
            self.write({'state': 'needs_review', 'error_message': f"can not make it paid: {str(e)}"})
            return False
        order.action_pos_order_invoice()

        self.invoice_id = order.account_move.id
        self.write({'state': 'invoiced', 'error_message': False})
        return order.account_move

    def _create_via_pos_refund(self, partner):
        """The return path: a second pos.order in the same session, built from the return
        payload's own lines with negated quantities - Odoo's own invoicing then picks
        'out_refund' automatically from the resulting negative total (_prepare_invoice_vals()).
        Foodics is the source of truth for the return too: the payment(s) it reports on the
        return payload already happened for real at the till, so they're attached here (negated,
        see _pos_payment_vals_list()) and the order is marked paid exactly like a normal sale -
        no manual "Payment" step needed on the credit note afterwards. reversed_entry_id is
        linked ourselves afterwards rather than via the native refunded_orderline_id mechanism,
        since that would require matching each returned line 1:1 against the original order's
        lines, which a partial return doesn't guarantee.
        """
        self.ensure_one()
        if self.pos_order_id:
            order = self.pos_order_id
            order.lines.unlink()
            order.payment_ids.unlink()
            order.write({
                'lines': self._pos_order_line_vals_list(negate=True),
                'payment_ids': self._pos_payment_vals_list(negate=True),
            })
        else:
            session = self._get_or_create_session()
            order = self.env['pos.order'].create({
                **self._zero_pos_amounts(),
                'session_id': session.id,
                'company_id': self.config_id.company_id.id,
                'partner_id': partner.id,
                'date_order': self.opened_at or fields.Datetime.now(),
                'lines': self._pos_order_line_vals_list(negate=True),
                'payment_ids': self._pos_payment_vals_list(negate=True),
            })
            self.pos_order_id = order.id
        self._finalize_pos_order_amounts(order)

        if abs(abs(order.amount_total) - self.total_price) > TOTAL_TOLERANCE:
            self.write({
                'state': 'needs_review',
                'error_message': _(
                    'POS refund order %(po)s total %(po_total)s does not match Foodics order '
                    'total %(foodics_total)s (likely a missing tax mapping). Fix the mapping, '
                    'then click "Retry".'
                ) % {'po': order.name, 'po_total': abs(order.amount_total), 'foodics_total': self.total_price},
            })
            return False

        try:
            order.action_pos_order_paid()
        except UserError as e:
            self.write({'state': 'needs_review', 'error_message': f"can not make it paid: {str(e)}"})
            return False
        order.action_pos_order_invoice()
        move = order.account_move
        original_invoice = self.original_order_id.pos_order_id.account_move
        if original_invoice and not move.reversed_entry_id:
            move.reversed_entry_id = original_invoice.id

        self.invoice_id = move.id
        self.write({'state': 'refunded', 'error_message': False})
        return move

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------
    def action_create_invoice(self):
        for rec in self:
            rec._create_invoice()

    def action_retry(self):
        """Re-attempt POS order/invoice creation for an order stuck in Needs Review, e.g. after
        fixing a tax/product/payment-method mapping. Unlike the old Sale-Order-based approach,
        there's no manual "confirm it by hand" step in between - _create_via_pos_order()/
        _create_via_pos_refund() always rebuild the pos.order's lines/payments from scratch and
        re-attempt paying+invoicing it, so simply calling _create_invoice() again is enough.
        """
        for rec in self:
            if rec.invoice_id:
                continue  # already fully processed
            try:
                rec._create_invoice()
            except Exception as e:  # noqa: BLE001 - keep the record actionable instead of raising
                rec.write({'state': 'needs_review', 'error_message': str(e)})

    def action_open_invoice(self):
        self.ensure_one()
        if not self.invoice_id:
            raise UserError(_('No invoice/credit note has been created for this order yet.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Invoice'),
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.invoice_id.id,
        }

    def action_open_pos_order(self):
        self.ensure_one()
        if not self.pos_order_id:
            raise UserError(_('No POS order has been created for this order yet.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('POS Order'),
            'res_model': 'pos.order',
            'view_mode': 'form',
            'res_id': self.pos_order_id.id,
        }

    def action_refresh_from_foodics(self):
        for rec in self:
            data = rec.config_id._get('/orders/%s' % rec.foodics_order_id, params={
                'include': 'branch,customer,charges,payments.payment_method,discount,'
                           'products.discount,products.taxes,products.options.taxes,'
                           'combos.discount,combos.products.taxes,combos.products.options.taxes',
            })
            rec._apply_payload(data.get('data', data))

    # ------------------------------------------------------------------
    # Cron: retry anything left in Needs Review, e.g. after a mapping fix.
    # ------------------------------------------------------------------
    @api.model
    def _cron_retry_needs_review(self):
        stuck = self.search([('state', '=', 'needs_review')])
        _logger.info('Foodics: retrying %s order(s) stuck in Needs Review', len(stuck))
        for order in stuck:
            order.action_retry()
