import datetime
import logging

from odoo import models, fields, api, SUPERUSER_ID, _
from odoo.osv import expression
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_round, float_is_zero, float_compare
from odoo.tools.misc import formatLang
from odoo.tools.misc import clean_context, OrderedSet
from operator import itemgetter
from itertools import groupby
from collections import defaultdict, Counter

_logger = logging.getLogger(__name__)

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    estimated_arrival_str = fields.Text(string="Estimated Arrival Date", compute="_compute_estimated_arrival_date")
    incoming_container_str = fields.Text(string="Incoming Containers", compute="_compute_containers_str")
    
    def _compute_estimated_arrival_date(self):
        for record in self:
            res = ""
            for move in record.move_ids:
                res += move.estimated_arrival_str
            record['estimated_arrival_str'] = res

    def _compute_containers_str(self):
        for record in self:
            res = ""
            for move in record.move_ids:
                res += move.incoming_container_str
            record['incoming_container_str'] = res

class StockQuant(models.Model):
    _inherit = 'stock.quant'

    def update_reserved_qty(self):
        for record in self:
            if record.location_id.usage == 'internal' and record.lot_id:
                move_lines = self.env['stock.move.line'].search([('lot_id','=',record.lot_id.id)])
                move_lines_filt = move_lines.filtered(lambda x: x.picking_code != 'incoming' and x.product_uom_qty > 0)
                qty = 0
                for mline in move_lines_filt:
                    qty += mline.product_uom_qty
                record.write({
                    'reserved_quantity': record.reserved_quantity + qty,
                })
                

class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    ctr_lot_id_delivery = fields.Many2one('container_lot', string="CTR Lot")
    ctr_reserved_qty = fields.Float(string="CTR Reserved")
    ctr_lot_id_receipt = fields.Many2one('container_lot', string="CTR Lot")
    estimated_arrival_str = fields.Text(string="Estimated Arrival Date", compute="_compute_estimated_arrival_date")
    incoming_container_str = fields.Text(string="Incoming Containers", compute="_compute_containers_str")
    sales_order_id = fields.Many2one('sale.order', string="Order Reference", compute="_compute_order_reference")

    @api.onchange('ctr_reserved_qty','product_uom_id','ctr_lot_id_delivery')
    def _onchange_ctr_reserved_qty(self):
        res = {}
        for record in self:
            diff = record.ctr_reserved_qty - self._origin.ctr_reserved_qty
            if record.ctr_lot_id_delivery.remaining_qty - diff < 0:
                message = _('You cannot reserve more than available on this CTR Lot.')
                res['warning'] = {'title': _('Warning'), 'message': message}
                record['ctr_reserved_qty'] = self._origin.ctr_reserved_qty
        return res

    def _compute_containers_str(self):
        for record in self:
            res = ""
            if record.qty_done > 0:
                if record.picking_code != 'incoming':
                    lot_name = ""
                    if record.lot_id:
                        lot_name = record.lot_id.name
                    res += str(record.qty_done) + " - Done " + str(lot_name) + "<br/>"
                else:
                    res += str(record.qty_done) + " - Received to Stock<br/>"

            if record.product_uom_qty > record.qty_done:
                diff = record.product_uom_qty - record.qty_done
                res += str(diff)
                if record.picking_code != 'incoming':
                    lot_name = ""
                    if record.lot_id:
                        lot_name = record.lot_id.name
                    res += " - In Stock " + str(lot_name) + "<br/>"
                else:
                    res += " - " + str(record.ctr_lot_id_receipt) + "<br/>"

            if record.ctr_reserved_qty > record.product_uom_qty and record.qty_done <= 0 and record.ctr_lot_id_delivery:
                diff = record.ctr_reserved_qty - record.product_uom_qty
                date_str = record.ctr_lot_id_delivery.name
                if record.ctr_lot_id_delivery.remaining_qty < 0:
                    date_str = "<span style='color:red !important;'>" + date_str + "</span>"
                elif record.ctr_lot_id_delivery.remaining_qty > 0:
                    date_str = "<span style='color: #00e600 !important;'>" + date_str + "</span>"
                else:
                    date_str = "<span style='color: #a6a6a6 !important;'>" + date_str + "</span>"
                res += str(diff) + " - " + date_str + "<br/>"
            record['incoming_container_str'] = res

    def _compute_estimated_arrival_date(self):
        for record in self:
            res = ""
            if record.qty_done > 0:
                if record.picking_code != 'incoming':
                    res += str(record.qty_done) + " - Done<br/>"
                else:
                    res += str(record.qty_done) + " - Received to Stock<br/>"

            if record.product_uom_qty > record.qty_done:
                diff = record.product_uom_qty - record.qty_done
                res += str(diff)
                if record.picking_code != 'incoming':
                    res += " - In Stock<br/>"
                else:
                    month = datetime.date(1900, int(record.move_id.date.date().month), 1).strftime('%B')
                    res += " - " + month
                    res += " " + str(record.move_id.date.date().day) + "<br/>"

            if record.ctr_reserved_qty > record.product_uom_qty and record.qty_done <= 0 and record.ctr_lot_id_delivery:
                diff = record.ctr_reserved_qty - record.product_uom_qty
                date_str = record.ctr_lot_id_delivery.compute_scheduled_date_string()
                res += str(diff) + " - " + date_str + "<br/>"
            record['estimated_arrival_str'] = res
                


    def receive_ctr_lots(self):
        for record in self:
            _logger.warning("RECEIVING LOT ID: " + str(record.lot_id))
            if record.lot_id and record.move_id and record.move_id.purchase_line_id and record.ctr_lot_id_receipt and record.qty_done > 0:
                record.ctr_lot_id_receipt.write({
                    'lot_id': record.lot_id.id,
                    'received': True,
                })
                record.lot_id.write({
                    'finish_preset_id': record.ctr_lot_id_receipt.finish_preset_id.id,
                })
                _logger.warning("Received the CTR")
                for ctr_line in record.ctr_lot_id_receipt.delivery_move_line_ids:
                    if not ctr_line.lot_id:
                        _logger.warning("About to reserve on a move line")
                        ctr_line.write({
                            'lot_id': record.lot_id.id,
                            'product_uom_qty': ctr_line.ctr_reserved_qty,
                        })


    def update_ctr_lot_on_write(self):
        for record in self:
            if record.move_id and record.move_id.picking_id and record.move_id.purchase_line_id and not record.lot_id:
                if record.ctr_lot_id_receipt:
                    record.ctr_lot_id_receipt.write({
                        'initial_demand': record.product_uom_qty,
                    })
                    if record.product_uom_qty < record.ctr_lot_id_receipt.ctr_reserved_qty:
                        record.ctr_lot_id_receipt.unreserve_on_reduce_qty(record.product_uom_qty)
                        
                                
    def update_ctr_lot(self):
        for record in self:
            products = self.env['product.template'].search(['&',('detailed_type','=','product'),('tracking','=','none')])
            for p in products:
                p.write({
                    'tracking': 'lot',
                })
            picking_types = self.env['stock.picking.type'].search(['&',('code','!=','incoming'),('reservation_method','!=','manual')])
            if len(picking_types) > 0:
                picking_types.write({'reservation_method': 'manual'})
            if record.move_id and record.move_id.picking_id and record.move_id.picking_id.purchase_id and record.move_id.purchase_line_id and not record.lot_id:
                ctr_lots = self.env['container_lot'].search(['&',('receipt_move_id','=',record.move_id.id),('receipt_line_id','=',False)])
                if len(ctr_lots) > 0:
                    for ctr_lot in ctr_lots:
                        ctr_lot.write({
                            'receipt_line_id': record.id,
                            'initial_demand': record.product_uom_qty,
                        })
                        ctr_lot['receipt_line_id'] = record.id
                        record.write({'ctr_lot_id_receipt': ctr_lot.id, 'lot_name': ctr_lot.name.split('(')[0]})
                        if record.product_uom_qty < record.ctr_lot_id_receipt.ctr_reserved_qty:
                            record.ctr_lot_id_receipt.unreserve_on_reduce_qty(record.product_uom_qty)
                        break
                else:
                    move = record.move_id
                    line = record
                    finish_preset_id = record.move_id.purchase_line_id.finish_preset_id
                    fp_id = False
                    if finish_preset_id:
                        fp_id = finish_preset_id.id
                    original_name = move.compute_original_name()
                    if len(move.move_line_ids) > 1:
                        i = 0
                        for l in move.move_line_ids:
                            if l.id == record.id and i > 0:
                                original_name += "_" + str(i)
                            i += 1
                    _logger.warning("Creating lot for product: " + str(line.product_id.name))
                    lot = self.env['container_lot'].create({
                        'receipt_line_id' : line.id,
                        'receipt_move_id': move.id,
                        'receipt_id' : line.picking_id.id, 
                        'product_id' : line.product_id.id,
                        'received': False,
                        'finish_preset_id': fp_id,
                        'original_name': original_name,
                        'initial_demand': line.product_uom_qty,
                        'ctr_reserved_qty': 0,
                        'remaining_qty': line.product_uom_qty,
                        'approximate_dates': line.move_id.picking_id.purchase_id.approximate_dates,
                        })
                    record.write({'ctr_lot_id_receipt': lot.id, 'lot_name': lot.name.split('(')[0]})



    def _compute_order_reference(self):
        res = False
        for record in self:
            if record.move_id and record.move_id.sale_line_id and record.move_id.sale_line_id.order_id:
                res = record.move_id.sale_line_id.order_id
        record['sales_order_id'] = res

    @api.onchange('lot_name', 'lot_id')
    def _onchange_serial_number(self):
        """ When the user is encoding a move line for a tracked product, we apply some logic to
        help him. This includes:
            - automatically switch `qty_done` to 1.0
            - warn if he has already encoded `lot_name` in another move line
            - warn (and update if appropriate) if the SN is in a different source location than selected
        """
        res = {}
        if self.product_id.tracking == 'serial':
            if not self.qty_done:
                self.qty_done = 0

            message = None
            if self.lot_name or self.lot_id:
                move_lines_to_check = self._get_similar_move_lines() - self
                if self.lot_name:
                    counter = Counter([line.lot_name for line in move_lines_to_check])
                    if counter.get(self.lot_name) and counter[self.lot_name] > 1:
                        message = _('You cannot use the same serial number twice. Please correct the serial numbers encoded.')
                    elif not self.lot_id:
                        lots = self.env['stock.production.lot'].search([('product_id', '=', self.product_id.id),
                                                                        ('name', '=', self.lot_name),
                                                                        ('company_id', '=', self.company_id.id)])
                        quants = lots.quant_ids.filtered(lambda q: q.quantity != 0 and q.location_id.usage in ['customer', 'internal', 'transit'])
                        if quants:
                            message = _('Serial number (%s) already exists in location(s): %s. Please correct the serial number encoded.', self.lot_name, ', '.join(quants.location_id.mapped('display_name')))
                elif self.lot_id:
                    counter = Counter([line.lot_id.id for line in move_lines_to_check])
                    if counter.get(self.lot_id.id) and counter[self.lot_id.id] > 1:
                        message = _('You cannot use the same serial number twice. Please correct the serial numbers encoded.')
                    else:
                        # check if in correct source location
                        message, recommended_location = self.env['stock.quant']._check_serial_number(self.product_id,
                                                                                                     self.lot_id,
                                                                                                     self.company_id,
                                                                                                     self.location_id,
                                                                                                     self.picking_id.location_id)
                        if recommended_location:
                            self.location_id = recommended_location
            if message:
                res['warning'] = {'title': _('Warning'), 'message': message}
        return res

class StockMove(models.Model):
    _inherit = 'stock.move'

    def _action_assign(self):
        """ Reserve stock moves by creating their stock move lines. A stock move is
        considered reserved once the sum of `product_qty` for all its move lines is
        equal to its `product_qty`. If it is less, the stock move is considered
        partially available.
        """

        def _get_available_move_lines(move):
            move_lines_in = move.move_orig_ids.filtered(lambda m: m.state == 'done').mapped('move_line_ids')
            keys_in_groupby = ['location_dest_id', 'lot_id', 'result_package_id', 'owner_id']

            def _keys_in_sorted(ml):
                return (ml.location_dest_id.id, ml.lot_id.id, ml.result_package_id.id, ml.owner_id.id)

            grouped_move_lines_in = {}
            for k, g in groupby(sorted(move_lines_in, key=_keys_in_sorted), key=itemgetter(*keys_in_groupby)):
                qty_done = 0
                for ml in g:
                    qty_done += ml.product_uom_id._compute_quantity(ml.qty_done, ml.product_id.uom_id)
                grouped_move_lines_in[k] = qty_done
            move_lines_out_done = (move.move_orig_ids.mapped('move_dest_ids') - move)\
                .filtered(lambda m: m.state in ['done'])\
                .mapped('move_line_ids')
            # As we defer the write on the stock.move's state at the end of the loop, there
            # could be moves to consider in what our siblings already took.
            moves_out_siblings = move.move_orig_ids.mapped('move_dest_ids') - move
            moves_out_siblings_to_consider = moves_out_siblings & (StockMove.browse(assigned_moves_ids) + StockMove.browse(partially_available_moves_ids))
            reserved_moves_out_siblings = moves_out_siblings.filtered(lambda m: m.state in ['partially_available', 'assigned'])
            move_lines_out_reserved = (reserved_moves_out_siblings | moves_out_siblings_to_consider).mapped('move_line_ids')
            keys_out_groupby = ['location_id', 'lot_id', 'package_id', 'owner_id']

            def _keys_out_sorted(ml):
                return (ml.location_id.id, ml.lot_id.id, ml.package_id.id, ml.owner_id.id)

            grouped_move_lines_out = {}
            for k, g in groupby(sorted(move_lines_out_done, key=_keys_out_sorted), key=itemgetter(*keys_out_groupby)):
                qty_done = 0
                for ml in g:
                    qty_done += ml.product_uom_id._compute_quantity(ml.qty_done, ml.product_id.uom_id)
                grouped_move_lines_out[k] = qty_done
            for k, g in groupby(sorted(move_lines_out_reserved, key=_keys_out_sorted), key=itemgetter(*keys_out_groupby)):
                grouped_move_lines_out[k] = sum(self.env['stock.move.line'].concat(*list(g)).mapped('product_qty'))
            available_move_lines = {key: grouped_move_lines_in[key] - grouped_move_lines_out.get(key, 0) for key in grouped_move_lines_in}
            # pop key if the quantity available amount to 0
            rounding = move.product_id.uom_id.rounding
            return dict((k, v) for k, v in available_move_lines.items() if float_compare(v, 0, precision_rounding=rounding) > 0)

        StockMove = self.env['stock.move']
        assigned_moves_ids = OrderedSet()
        partially_available_moves_ids = OrderedSet()
        # Read the `reserved_availability` field of the moves out of the loop to prevent unwanted
        # cache invalidation when actually reserving the move.
        reserved_availability = {move: move.reserved_availability + sum(item.ctr_reserved_qty for item in move.move_line_ids.filtered(lambda mline: mline.ctr_reserved_qty > 0 and mline.product_uom_qty <= 0)) for move in self}
        roundings = {move: move.product_id.uom_id.rounding for move in self}
        move_line_vals_list = []
        # Once the quantities are assigned, we want to find a better destination location thanks
        # to the putaway rules. This redirection will be applied on moves of `moves_to_redirect`.
        moves_to_redirect = OrderedSet()
        for move in self.filtered(lambda m: m.state in ['confirmed', 'waiting', 'partially_available']):
            rounding = roundings[move]
            missing_reserved_uom_quantity = move.product_uom_qty - reserved_availability[move]
            missing_reserved_quantity = move.product_uom._compute_quantity(missing_reserved_uom_quantity, move.product_id.uom_id, rounding_method='HALF-UP')
            if move._should_bypass_reservation():
                # create the move line(s) but do not impact quants
                if move.move_orig_ids:
                    available_move_lines = _get_available_move_lines(move)
                    for (location_id, lot_id, package_id, owner_id), quantity in available_move_lines.items():
                        qty_added = min(missing_reserved_quantity, quantity)
                        move_line_vals = move._prepare_move_line_vals(qty_added)
                        move_line_vals.update({
                            'location_id': location_id.id,
                            'lot_id': lot_id.id,
                            'lot_name': lot_id.name,
                            'owner_id': owner_id.id,
                        })
                        move_line_vals_list.append(move_line_vals)
                        missing_reserved_quantity -= qty_added
                        if float_is_zero(missing_reserved_quantity, precision_rounding=move.product_id.uom_id.rounding):
                            break

                if missing_reserved_quantity and move.product_id.tracking == 'serial' and (move.picking_type_id.use_create_lots or move.picking_type_id.use_existing_lots):
                    for i in range(0, int(missing_reserved_quantity)):
                        move_line_vals_list.append(move._prepare_move_line_vals(quantity=1))
                elif missing_reserved_quantity:
                    to_update = move.move_line_ids.filtered(lambda ml: ml.product_uom_id == move.product_uom and
                                                            ml.location_id == move.location_id and
                                                            ml.location_dest_id == move.location_dest_id and
                                                            ml.picking_id == move.picking_id and
                                                            not ml.lot_id and
                                                            not ml.package_id and
                                                            not ml.owner_id)
                    if to_update:
                        to_update[0].product_uom_qty += move.product_id.uom_id._compute_quantity(
                            missing_reserved_quantity, move.product_uom, rounding_method='HALF-UP')
                    else:
                        move_line_vals_list.append(move._prepare_move_line_vals(quantity=missing_reserved_quantity))
                assigned_moves_ids.add(move.id)
                moves_to_redirect.add(move.id)
            else:
                if float_is_zero(move.product_uom_qty, precision_rounding=move.product_uom.rounding):
                    assigned_moves_ids.add(move.id)
                elif not move.move_orig_ids:
                    if move.procure_method == 'make_to_order':
                        continue
                    # If we don't need any quantity, consider the move assigned.
                    need = missing_reserved_quantity
                    if float_is_zero(need, precision_rounding=rounding):
                        assigned_moves_ids.add(move.id)
                        continue
                    # Reserve new quants and create move lines accordingly.
                    forced_package_id = move.package_level_id.package_id or None
                    available_quantity = move._get_available_quantity(move.location_id, package_id=forced_package_id)
                    if available_quantity <= 0:
                        continue
                    taken_quantity = move._update_reserved_quantity(need, available_quantity, move.location_id, package_id=forced_package_id, strict=False)
                    if float_is_zero(taken_quantity, precision_rounding=rounding):
                        continue
                    moves_to_redirect.add(move.id)
                    if float_compare(need, taken_quantity, precision_rounding=rounding) == 0:
                        assigned_moves_ids.add(move.id)
                    else:
                        partially_available_moves_ids.add(move.id)
                else:
                    # Check what our parents brought and what our siblings took in order to
                    # determine what we can distribute.
                    # `qty_done` is in `ml.product_uom_id` and, as we will later increase
                    # the reserved quantity on the quants, convert it here in
                    # `product_id.uom_id` (the UOM of the quants is the UOM of the product).
                    available_move_lines = _get_available_move_lines(move)
                    if not available_move_lines:
                        continue
                    for move_line in move.move_line_ids.filtered(lambda m: m.product_qty):
                        if available_move_lines.get((move_line.location_id, move_line.lot_id, move_line.result_package_id, move_line.owner_id)):
                            available_move_lines[(move_line.location_id, move_line.lot_id, move_line.result_package_id, move_line.owner_id)] -= move_line.product_qty
                    for (location_id, lot_id, package_id, owner_id), quantity in available_move_lines.items():
                        need = move.product_qty - sum(move.move_line_ids.mapped('product_qty'))
                        # `quantity` is what is brought by chained done move lines. We double check
                        # here this quantity is available on the quants themselves. If not, this
                        # could be the result of an inventory adjustment that removed totally of
                        # partially `quantity`. When this happens, we chose to reserve the maximum
                        # still available. This situation could not happen on MTS move, because in
                        # this case `quantity` is directly the quantity on the quants themselves.
                        available_quantity = move._get_available_quantity(location_id, lot_id=lot_id, package_id=package_id, owner_id=owner_id, strict=True)
                        if float_is_zero(available_quantity, precision_rounding=rounding):
                            continue
                        taken_quantity = move._update_reserved_quantity(need, min(quantity, available_quantity), location_id, lot_id, package_id, owner_id)
                        if float_is_zero(taken_quantity, precision_rounding=rounding):
                            continue
                        moves_to_redirect.add(move.id)
                        if float_is_zero(need - taken_quantity, precision_rounding=rounding):
                            assigned_moves_ids.add(move.id)
                            break
                        partially_available_moves_ids.add(move.id)
            if move.product_id.tracking == 'serial':
                move.next_serial_count = move.product_uom_qty

        self.env['stock.move.line'].create(move_line_vals_list)
        StockMove.browse(partially_available_moves_ids).write({'state': 'partially_available'})
        StockMove.browse(assigned_moves_ids).write({'state': 'assigned'})
        if self.env.context.get('bypass_entire_pack'):
            return
        self.mapped('picking_id')._check_entire_pack()
        StockMove.browse(moves_to_redirect).move_line_ids._apply_putaway_strategy()

    sales_order_id = fields.Many2one('sale.order', string="Order Reference", compute="_compute_order_reference")
    total_reserved_ctr = fields.Float(string="CTR Reserved", compute="_compute_total_reserved_ctr")
    reservation_status = fields.Selection([
        ('waiting', 'Waiting Available CTR'),
        ('assigned_ctr', 'Assigned Incoming Containers'),
        ('assigned_lot', 'Assigned In Stock'),
        ('over_assigned', 'Too Much Reserved'),
        ], compute="_compute_reserve_status")
    estimated_arrival_str = fields.Text(string="Estimated Arrival Dates", compute="_compute_estimated_arrival_str")
    incoming_container_str = fields.Text(string="Incoming Containers", compute="_compute_containers_str")

    def _compute_containers_str(self):
        for record in self:
            res = ""
            for line in record.move_line_ids:
                res += line.incoming_container_str
            record['incoming_container_str'] = res

    def _compute_estimated_arrival_str(self):
        for record in self:
            res = ""
            qty = 0
            for line in record.move_line_ids:
                res += line.estimated_arrival_str
                if line.qty_done > 0:
                    qty += line.qty_done
                if line.product_uom_qty > 0:
                    qty += line.product_uom_qty - line.qty_done
                elif line.ctr_reserved_qty > 0:
                    qty += line.ctr_reserved_qty
            if qty < record.product_uom_qty:
                diff = record.product_uom_qty - qty
                res += str(diff) + " - "
                if record.sale_line_id:
                    if record.product_id.product_tmpl_id.out_of_incoming_message and record.product_id.product_tmpl_id.out_of_incoming_message != "":
                        res += record.product_id.product_tmpl_id.out_of_incoming_message
                    else:
                        res += "Out of Stock"
                else:
                    res += "NOT RESERVED"


            record['estimated_arrival_str'] = res
    
    def _compute_reserve_status(self):
        for record in self:
            tt = record.total_reserved_ctr + record.reserved_availability
            if tt < record.product_uom_qty:
                record['reservation_status'] = 'waiting'
            elif tt > record.product_uom_qty:
                record['reservation_status'] = 'over_assigned'
            else:
                if record.total_reserved_ctr > 0:
                    record['reservation_status'] = 'assigned_ctr'
                else:
                    record['reservation_status'] = 'assigned_lot'

    def _compute_total_reserved_ctr(self):
        res = 0
        for record in self:
            for line in record.move_line_ids.filtered(lambda x: x.product_uom_qty <= 0 and x.ctr_reserved_qty > 0):
                res += line.ctr_reserved_qty
            record['total_reserved_ctr'] = res


    def _compute_order_reference(self):
        res = False
        for record in self:
            if record.sale_line_id and record.sale_line_id.order_id:
                res = record.sale_line_id.order_id.id
            record['sales_order_id'] = res

    def compute_original_name(self):
        res = ""
        for record in self:
            if record.purchase_line_id:
                res = record.purchase_line_id.order_id.container_name
        return res

    def remove_ctr_on_reassign(self):
        for record in self:
            if record.reservation_status != 'waiting':
                ctr_lots = self.env['container_lot'].search([]).filtered(lambda x: record in x.unreserved_move_ids)
                for lot in ctr_lots:
                    lot.write({
                        'unreserved_move_ids': [(3, record.id)]
                    })


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def action_assign(self):
        """ Check availability of picking moves.
        This has the effect of changing the state and reserve quants on available moves, and may
        also impact the state of the picking as it is computed based on move's states.
        @return: True
        """
        self.filtered(lambda picking: picking.state == 'draft').action_confirm()
        moves = self.mapped('move_lines').filtered(lambda move: move.state not in ('draft', 'cancel', 'done')).sorted(
            key=lambda move: (-int(move.priority), not bool(move.date_deadline), move.date_deadline, move.date, move.id)
        )
        new_moves = []
        for m in moves:
            qty = 0
            for mline in m.move_line_ids:
                if mline.qty_done > 0:
                    qty += mline.qty_done
                elif mline.product_uom_qty > 0:
                    qty += mline.product_uom_qty
                elif mline.ctr_reserved_qty > 0:
                    qty += mline.ctr_reserved_qty
            _logger.warning(str(m.product_id.name) + " " + str(qty) + " | " + str(m.product_uom_qty))
            if qty < m.product_uom_qty:
                new_moves.append(m.id)
        moves = moves.filtered(lambda x: x.id in new_moves)
        if not moves:
            raise UserError(_('Nothing to check the availability for.'))
        # If a package level is done when confirmed its location can be different than where it will be reserved.
        # So we remove the move lines created when confirmed to set quantity done to the new reserved ones.
        package_level_done = self.mapped('package_level_ids').filtered(lambda pl: pl.is_done and pl.state == 'confirmed')
        package_level_done.write({'is_done': False})
        moves._action_assign()
        package_level_done.write({'is_done': True})

        return True

    def create_ctr_lots(self):
        for record in self:
            _logger.warning("Launched automated action: " + str(record.purchase_id))
            if record.purchase_id:
                if not record.picking_type_id.show_reserved:
                    record.picking_type_id.write({'show_reserved': True})
                products = self.env['product.template'].search(['&',('detailed_type','=','product'),('tracking','=','none')])
                for p in products:
                    p.write({
                        'tracking': 'lot',
                    })
                picking_types = self.env['stock.picking.type'].search(['&',('code','!=','incoming'),('reservation_method','!=','manual')])
                if len(picking_types) > 0:
                    picking_types.write({'reservation_method': 'manual'})
                _logger.warning("Is from a PO")
                for move in record.move_ids_without_package:
                    if move.purchase_line_id:
                        finish_preset_id = move.purchase_line_id.finish_preset_id
                        fp_id = False
                        if finish_preset_id:
                            fp_id = finish_preset_id.id
                        original_name = move.compute_original_name()
                        for line in move.move_line_ids:
                            _logger.warning("Creating lot")
                            lot = self.env['container_lot'].create({
                                'receipt_line_id' : line.id,
                                'receipt_move_id': move.id,
                                'receipt_id' : record.id, 
                                'product_id' : line.product_id.id,
                                'received': False,
                                'finish_preset_id': fp_id,
                                'original_name': original_name,
                                'initial_demand': line.product_uom_qty,
                                'ctr_reserved_qty': 0,
                                'remaining_qty': line.product_uom_qty,
                                'approximate_dates': record.purchase_id.approximate_dates,
                                })
                            line.write({'ctr_lot_id_receipt': lot.id, 'lot_name': lot.name.split('(')[0]})


    def delete_ctr_lots(self):
        for record in self:
            ctr_lot_ids = self.env['container_lot'].search([('receipt_id','=',record.id)])
            for ctr_lot in ctr_lot_ids:
                ctr_lot.unreserve_on_reduce_qty(0)
                ctr_lot.unlink()

    def cancel_ctr_lots(self):
        for record in self:
            if record.state == 'cancel':
                ctr_lot_ids = self.env['container_lot'].search([('receipt_id','=',record.id)])
                ctr_lot_ids.write({
                    'cancelled': True,
                })
                for ctr_lot in ctr_lot_ids:
                    ctr_lot.unreserve_on_reduce_qty(0)


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    container_name = fields.Char(string="CTR Name")
    approximate_dates = fields.Boolean(string="Approximate Dates on CTR", help="If checked, all the CTR Lots from this Purchase Order will show either: \n'Early [Month]', 'Mid [Month]' or 'Late [Month]' \ninstead of the actual scheduled arrival date.")
    
    def update_ctr_lots(self):
        for record in self:
            for picking in record.picking_ids:
                for move in picking.move_ids_without_package:
                    finish_preset_id = move.purchase_line_id.finish_preset_id
                    fp_id = False
                    if finish_preset_id:
                        fp_id = finish_preset_id.id
                    for line in move.move_line_ids:
                        if line.ctr_lot_id_receipt:
                            original_name = move.compute_original_name()
                            if len(move.move_line_ids) > 1:
                                i = 0
                                for l in move.move_line_ids:
                                    if l.id == line.id and i > 0:
                                        original_name += "_" + str(i)
                                        break
                                    i += 1
                            line.ctr_lot_id_receipt.write({
                                'original_name': original_name,
                                'approximate_dates': record.approximate_dates,
                                'finish_preset_id': fp_id,
                            })
                            line.write({
                                'lot_name': line.ctr_lot_id_receipt.name.split('(')[0]
                            })

    def _create_picking(self):
        StockPicking = self.env['stock.picking']
        for order in self.filtered(lambda po: po.state in ('purchase', 'done')):
            if any(product.type in ['product', 'consu'] for product in order.order_line.product_id):
                order = order.with_company(order.company_id)
                pickings = order.picking_ids.filtered(lambda x: x.state not in ('done', 'cancel'))
                if not pickings:
                    res = order._prepare_picking()
                    picking = StockPicking.with_user(SUPERUSER_ID).create(res)
                else:
                    picking = pickings[0]
                moves = order.order_line._create_stock_moves(picking)
                moves = moves.filtered(lambda x: x.state not in ('done', 'cancel'))._action_confirm()
                seq = 0
                for move in sorted(moves, key=lambda move: move.date):
                    seq += 5
                    move.sequence = seq
                moves._action_assign()
                picking.message_post_with_view('mail.message_origin_link',
                    values={'self': picking, 'origin': order},
                    subtype_id=self.env.ref('mail.mt_note').id)
                
                # picking.create_ctr_lots()

        return True

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    next_incoming_containers = fields.One2many('container_lot', 'product_tmpl_id', domain="['&','&',('received','=',False),('cancelled','=',False),('receipt_line_id','!=',False)]")

class ProductLot(models.Model):
    _inherit = 'stock.production.lot'

    finish_preset_id = fields.Many2one('finish_preset', string="Finish Preset")

class ContainerLot(models.Model):
    _name = 'container_lot'
    
    name = fields.Char(string="CTR Lot", compute="_compute_name")
    original_name = fields.Char(string="Original Name")
    received = fields.Boolean(string="Received")
    product_id = fields.Many2one('product.product', string="Product")
    product_tmpl_id = fields.Many2one('product.template', string="Product Template", related='product_id.product_tmpl_id')
    finish_preset_id = fields.Many2one('finish_preset', string="Finish Preset")
    receipt_id = fields.Many2one('stock.picking')
    receipt_move_id = fields.Many2one('stock.move', string="Receipt Move")
    receipt_line_id = fields.Many2one('stock.move.line', string="Receipt Line")
    receipt_scheduled_date = fields.Datetime(string="Scheduled Arrival Date", related='receipt_line_id.move_id.date')
    lot_id = fields.Many2one('stock.production.lot', string="Lot")
    initial_demand = fields.Float(string="Initial Demand")
    ctr_reserved_qty = fields.Float(string="Reserved", compute='_compute_reserved')
    remaining_qty = fields.Float(string="Available", compute='_compute_remaining')
    approximate_dates = fields.Boolean(string="Approximate Dates on CTR")
    cancelled = fields.Boolean(string="Cancelled")

    unreserved_move_ids = fields.Many2many('stock.move', string="Unreserved Moves")
    warning_on_ctr = fields.Char(string="Warning")

    delivery_move_line_ids = fields.One2many('stock.move.line', 'ctr_lot_id_delivery', string="Delivery Lines", help="Lines from deliveries (mostly linked to Sale Orders)\non which quantity from this container is reserved.")

    @api.model
    def _name_search(self, name, args=None, operator='ilike', limit=100, name_get_uid=None):
        args = args or []
        domain = []
        if name:
            domain = ['|', ('original_name', operator, name), ('finish_preset_id', operator, name)]
        return self._search(expression.AND([domain, args]), limit=limit, access_rights_uid=name_get_uid)

    def compute_scheduled_date_string(self):
        res = ""
        for record in self:
            date = record.receipt_scheduled_date
            if record.approximate_dates:
                if int(date.day) < 11:
                    res += "Early "
                elif int(date.day) < 20:
                    res += "Mid "
                elif int(date.day) < 32:
                    res += "Late "

            month = datetime.date(1900, int(date.month), 1).strftime('%B')

            res += month

            if not record.approximate_dates:
                res += " " + str(date.day)
        return res


    def unreserve_on_reduce_qty(self, new_qty):
        for record in self:
            _logger.warning("Unreserving from CTR: " + str(record.name) + " to a quantity of " + str(new_qty))
            diff = record.ctr_reserved_qty - new_qty
            if diff > 0:
                qty = 0
                unreserved_moves = []
                cant_unreserve_lines = []
                for del_move_line in record.delivery_move_line_ids:
                    if qty >= diff:
                        break
                    if del_move_line.product_uom_qty > 0:
                        cant_unreserve_lines.append(del_move_line)
                    else:
                        move = del_move_line.move_id
                        unreserved_moves.append(move)
                        qty += del_move_line.product_uom_qty
                        del_move_line.unlink()
                if qty < diff and len(cant_unreserve_lines) > 0:
                    st = "Cannot unreserve the following lines as they are already reserved on In Stock products\n"
                    for line in cant_unreserve_lines:
                        st += str(line.sales_order_id.name) + " - " + str(line.picking_id.name) + " | CTR Reserved: " + str(line.ctr_reserved_qty) + "\n"
                    record['warning_on_ctr'] = st
                for move in unreserved_moves:
                    record.write({
                        'unreserved_move_ids': [(4, move.id)]
                    })


    def toggle_active(self):
        pass
        # IF BUGS FROM TIMEOUTS THAT DO NOT RECEIVE CTR HAPPEN, REACTIVATE THIS AND IMPLEMENT A MANUAL FIX / SCHEDULED ACTION? 
        # for record in self:
        #     record['received'] = not record.received

    def toggle_cancel(self):
        pass

    def compute_original_name(self):
        res = ""
        for record in self:
            move = record.receipt_line_id.move_id
            res = move.compute_original_name()
        return res
    
    def _compute_name(self):
        res = ""
        for record in self:
            res = record.original_name
            move = record.receipt_line_id.move_id
            finish_preset_id = move.purchase_line_id.finish_preset_id
            if finish_preset_id and finish_preset_id.name:
                if record.product_id.name in finish_preset_id.name:
                    res += " - " + finish_preset_id.name
                else:
                    res += " - " + record.product_id.name + " - " + finish_preset_id.name
            else:
                res += " - " + record.product_id.name
            res += " (" + str(record.remaining_qty) + " remaining)"
            record['name'] = res
            

    def _compute_remaining(self):
        for record in self:
            record['remaining_qty'] = record.initial_demand - record.ctr_reserved_qty

    def _compute_reserved(self):
        for record in self:
            reserved = 0
            for line in record.delivery_move_line_ids:
                reserved += line.ctr_reserved_qty
            record['ctr_reserved_qty'] = reserved