# -*- coding: utf-8 -*-
# Delivery operation type -> Reservation Method -> Manually
{
    'name': 'Lead Times on Website | Simtec IT',
    'sequence': 5,
    'summary': 'Show lead times to your customers on your website.',
    'version': '1.0',
    'author': 'Simtec IT',
    'support': '',
    'website': '',
    'license': 'Other proprietary',
    'description': """
        """,
    'depends': ['base_automation','purchase','stock','product','sale_management','website_sale'],
    'data': [
        'security/ir.model.access.csv',
        'views/automatic_settings.xml',
        'views/init_ctr_on_stock_move_scheduled.xml',
        'views/container_lot_edit_automated_action.xml',
        'views/container_lot_views_menus.xml',
        'views/finish_preset_form.xml',
        'views/product_template_form_customization.xml',
        'views/purchase_order_form_customization.xml',
        'views/sale_order_portal_content_customization.xml',
        'views/stock_picking_move_line_view_customizations.xml',
        'views/website_product_page_customization.xml',
        'views/website_sale_cart_lines_customization.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            '/simtec-lead-times/static/src/js/variant_mixin.js',
        ]
    },
    'installable': True,
    'application': True,
}
