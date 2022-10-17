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


class StockMove(models.Model):
    _inherit = 'stock.move'

    to_init = fields.Boolean(string="To Init")

    def set_as_to_init(self):
        for record in self:
            if record.sale_line_id != False:
                record['to_init'] = True
    
    def init_ctr_lot(self):
        for record in self:
            if record.product_id and record.sale_line_id:
                quantity = record.product_uom_qty - (record.reserved_availability + sum(item.ctr_reserved_qty for item in record.move_line_ids.filtered(lambda mline: mline.ctr_reserved_qty > 0 and mline.product_uom_qty <= 0)))
                combination = record.sale_line_id.product_no_variant_attribute_value_ids
                usage_list, qty = record.product_id.get_next_available_list(quantity, combination)
                usage_list_filtered = list(filter(lambda x: x['model'] != 'none', usage_list))
                for l in usage_list_filtered:
                    values = {
                        'location_id': record.location_id.id,
                        'location_dest_id': record.location_dest_id.id,
                        'move_id': record.id,
                        'product_id': record.product_id.id,
                        'product_uom_id': record.product_uom.id,
                        'picking_id': record.picking_id.id,
                    }
                    if l['model'] == 'stock.quant':
                        quants = self.env['stock.quant'].browse(l['res_id'])
                        if len(quants) > 0:
                            quant = quants[0]
                            if quant.lot_id:
                                values.update({
                                    'lot_id': quant.lot_id.id,
                                    'product_uom_qty': l['qty'],
                                })
                                mline = self.env['stock.move.line'].create(values)
                                quant.update_reserved_qty()
                    elif l['model'] == 'container_lot':
                        values.update({
                            'ctr_lot_id_delivery': l['res_id'],
                            'ctr_reserved_qty': l['qty'],
                        })
                        mline = self.env['stock.move.line'].create(values)


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    website_lot_info = fields.Char(string="Lead Time shown on Website")

    def on_write_auto(self):
        for record in self:
            _logger.warning("UPDATED SO LINE: " + str(record.product_id) + " | " + str(record.product_uom_qty) + " | " + str(record.product_no_variant_attribute_value_ids))
            if record.product_id and record.product_uom_qty and len(record.move_ids) < 1:
                record.get_initial_website_lot_info()

    def on_create_auto(self):
        for record in self:
            _logger.warning("CREATED SO LINE: " + str(record.product_id) + " | " + str(record.product_uom_qty) + " | " + str(record.product_no_variant_attribute_value_ids))
            if record.product_id and record.product_uom_qty:
                record.get_initial_website_lot_info()

    def get_initial_website_lot_info(self):
        for record in self:
            usage_list, qty = record.product_id.get_next_available_list(record.product_uom_qty, record.product_no_variant_attribute_value_ids)
            res = ""
            if record.product_id:
                if qty > 0 or len(usage_list) < 1:
                    _logger.warning("- OUT OF STOCK MESSAGE: " + str(record.product_id.product_tmpl_id.out_of_incoming_message))
                    if record.product_id.product_tmpl_id.out_of_incoming_message != "" and record.product_id.product_tmpl_id.out_of_incoming_message != False:
                        res = record.product_id.product_tmpl_id.out_of_incoming_message
                    else:
                        res = "Out of Stock"
                else:
                    last = usage_list[len(usage_list) - 1]
                    if last['model'] == 'stock.quant':
                        res = "In Stock"
                    elif last['model'] == 'container_lot':
                        ctr_lots = self.env['container_lot'].browse(last['res_id'])
                        if len(ctr_lots) > 0:
                            ctr_lot = ctr_lots[0]
                            res = ctr_lot.compute_scheduled_date_string()
            record['website_lot_info'] = res
    
class ProductTemplate(models.Model):
    _inherit = 'product.template'

    out_of_incoming_message = fields.Char(string="Out of Stock Message", help="Message to display on the website if no stock or incoming containers \nwere found to match the requested quantity / attributes")            


    def get_web_availability_string(self, product, quantity, combination):
        for record in self:
            res = ""
            if product:
                usage_list, qty = product.sudo().get_next_available_list(quantity, combination)
                if qty > 0 or len(usage_list) < 1:
                    if record.out_of_incoming_message != "" and record.out_of_incoming_message != False:
                        res = record.out_of_incoming_message
                    else:
                        res = "Out of Stock"
                else:
                    last = usage_list[len(usage_list) - 1]
                    if last['model'] == 'stock.quant':
                        res = "In Stock"
                    elif last['model'] == 'container_lot':
                        ctr_lots = self.env['container_lot'].sudo().browse(last['res_id'])
                        if len(ctr_lots) > 0:
                            ctr_lot = ctr_lots[0]
                            res = ctr_lot.sudo().compute_scheduled_date_string()
            return res

    def _get_combination_info(self, combination=False, product_id=False, add_qty=1, pricelist=False, parent_combination=False, only_template=False):
        combination_info = super(ProductTemplate, self)._get_combination_info(
            combination=combination, product_id=product_id, add_qty=add_qty, pricelist=pricelist,
            parent_combination=parent_combination, only_template=only_template)
        combination_info.update({
            'estimated_arrival': "",
        })
        if not self.env.context.get('website_sale_stock_get_quantity'):
            return combination_info

        if combination_info['product_id']:
            product = self.env['product.product'].sudo().browse(combination_info['product_id'])
            website = self.env['website'].get_current_website()
            free_qty = product.with_context(warehouse=website._get_warehouse_available()).free_qty

            next_arrival_str = self.get_web_availability_string(product, add_qty, combination)

            combination_info.update({
                'free_qty': free_qty,
                'product_type': product.type,
                'product_template': self.id,
                'available_threshold': self.available_threshold,
                'cart_qty': product.cart_qty,
                'uom_name': product.uom_id.name,
                'allow_out_of_stock_order': self.allow_out_of_stock_order,
                'show_availability': self.show_availability,
                'out_of_stock_message': self.out_of_stock_message,
                'estimated_arrival': next_arrival_str,
            })
        else:
            product_template = self.sudo()
            product = product_template.product_variant_id
            next_arrival_str = self.get_web_availability_string(product, add_qty, combination)

            combination_info.update({
                'free_qty': 0,
                'product_type': product_template.type,
                'allow_out_of_stock_order': product_template.allow_out_of_stock_order,
                'available_threshold': product_template.available_threshold,
                'product_template': product_template.id,
                'cart_qty': 0,
                'estimated_arrival': next_arrival_str,
            })

        return combination_info

class ProductVariant(models.Model):
    _inherit = 'product.product'

    # Looks for the next best match to reserve on quant of CTR lot
    # /!\ Will only reserve a maximum of best match's available quant
    # Use this in a loop to build the array of next available quants
    def compute_next_available(self, quantity, combination, used_lot_ids, used_ctr_lot_ids):
        for record in self:
            in_stock = self.env['stock.quant'].search(['&', '&', ('product_id','=',record.id), ('location_id.usage','=','internal'), ('quantity','>',0)])
            in_stock = in_stock.filtered(lambda x: x.quantity - x.reserved_quantity > 0 and x.id not in used_lot_ids).sorted(lambda x: x.create_date)
            no_exclusions = []
            # Looking for in stock quants to match
            for quant in in_stock:
                _logger.warning("LOT: " + str(quant.lot_id.name) + " | QTY: " + str(quant.quantity - quant.reserved_quantity) + " | DATE: " + str(quant.create_date))
                if quant.lot_id and quant.lot_id.finish_preset_id:
                    preset = quant.lot_id.finish_preset_id
                    # Checking if any exclusions are found in the quant's finish
                    exclusion_found = False
                    for attr in combination:
                        if exclusion_found:
                            break
                        for exclu in preset.exclusion_line_ids:
                            if attr.product_attribute_value_id.id == exclu.attribute_value_id.id and attr.attribute_id.id == exclu.attribute_id.id:
                                exclusion_found = True
                                break
                    if not exclusion_found:
                        no_exclusions.append(quant)
                else:
                    no_exclusions.append(quant)
            
            # Score remaining quants by how well they match ordered attributes
            scored_quants = []
            for quant in no_exclusions:
                score = 0
                prio = 10000
                if quant.lot_id and quant.lot_id.finish_preset_id:
                    preset = quant.lot_id.finish_preset_id
                    prio = preset.priority
                    for attr in combination:
                        score += len(preset.finish_line_ids.filtered(lambda x: x.attribute_id.id == attr.attribute_id.id and x.attribute_value_id.id == attr.product_attribute_value_id.id))
                scored_quants.append({
                    'id': quant,
                    'score': score,
                    'date': quant.create_date,
                    'prio': prio,
                })
            
            # Return highest score
            if len(scored_quants) > 0:
                scored_quants.sort(key=lambda x: x['prio'])
                scored_quants_sorted = scored_quants
                max_quant = max(scored_quants_sorted, key=lambda x: x['score'])
                _logger.warning("GOT MAX: " + str(max_quant))
                available = max_quant['id']['quantity'] - max_quant['id']['reserved_quantity']
                diff = 0                
                res_qty = quantity
                if quantity > available:
                    diff = quantity - available
                    res_qty = available
                return diff, res_qty, max_quant['id'], 'stock.quant', max_quant['id']['create_date']
            
            # If we reach this, we have no stock that we can use for the remaining quantity
            # Looking for incoming CTR lots
            ctr_lots = self.env['container_lot'].search(['&','&','&',('received','=',False),('cancelled','=',False),('product_id','=',record.id),('receipt_line_id','!=',False)])
            ctr_lots_filtered = ctr_lots.filtered(lambda x: x.remaining_qty > 0 and x.id not in used_ctr_lot_ids).sorted(lambda x: x.receipt_scheduled_date)

            ctr_no_exclusions = []
            for ctr in ctr_lots_filtered:
                if ctr.finish_preset_id:
                    preset = ctr.finish_preset_id
                    # Checking if any exclusions are found in the ctr's finish
                    exclusion_found = False
                    for attr in combination:
                        if exclusion_found:
                            break
                        for exclu in preset.exclusion_line_ids:
                            if attr.product_attribute_value_id.id == exclu.attribute_value_id.id and attr.attribute_id.id == exclu.attribute_id.id:
                                exclusion_found = True
                                break
                    if not exclusion_found:
                        ctr_no_exclusions.append(ctr)
                else:
                    ctr_no_exclusions.append(ctr)
            
            if len(ctr_no_exclusions) > 0:
                ctr_no_exclusions.sort(key=lambda x: x.receipt_scheduled_date)
                ctr_sorted = ctr_no_exclusions
                first_date = ctr_sorted[0].receipt_scheduled_date
                # Keeping only the CTR within the range of their finish's max lead time
                ctr_filtered = list(filter(lambda x: x.receipt_scheduled_date - datetime.timedelta(days=x.finish_preset_id.max_lead_time) <= first_date if x.finish_preset_id else True, ctr_sorted))
                if len(ctr_filtered) > 0:
                    ctr_scored = []
                    for ctr in ctr_filtered:
                        score = 0
                        prio = 10000
                        if ctr.finish_preset_id:
                            preset = ctr.finish_preset_id
                            prio = preset.priority
                            score += len(preset.finish_line_ids.filtered(lambda x: x.attribute_id.id == attr.attribute_id.id and x.attribute_value_id.id == attr.product_attribute_value_id.id))
                        ctr_scored.append({
                            'id': ctr,
                            'score': score,
                            'date': ctr.receipt_scheduled_date,
                            'prio': prio,
                        })
                    if len(ctr_scored) > 0:
                        ctr_scored.sort(key=lambda x: x['prio'])
                        ctr_scored_sorted = ctr_scored
                        max_ctr = max(ctr_scored_sorted, key=lambda x: x['score'])
                        available = max_ctr['id']['remaining_qty']
                        diff = 0
                        res_qty = quantity
                        if quantity > available:
                            diff = quantity - available
                            res_qty = available
                        return diff, res_qty, max_ctr['id'], 'container_lot', max_ctr['id']['receipt_scheduled_date']
        # Did not find anything
        return 0, 0, False, 'none', False


    def get_next_available_list(self, quantity, combination):
        qty = quantity
        used_lot_ids = []
        used_ctr_lot_ids = []
        usage_list = []
        while qty > 0:
            new_qty, res_qty, rec, model, date = self.compute_next_available(qty, combination, used_lot_ids, used_ctr_lot_ids)
            if model == 'none':
                break
            else:
                usage_list.append({
                    'model': model,
                    'res_id': rec['id'],
                    'qty': res_qty,
                    'date': date,
                })
                if model == 'stock.quant':
                    used_lot_ids.append(rec.id)
                elif model == 'container_lot':
                    used_ctr_lot_ids.append(rec.id)
                qty = new_qty
        usage_list.sort(key=lambda x: x['date'])
        usage_list_sorted = usage_list
        return usage_list_sorted, qty


