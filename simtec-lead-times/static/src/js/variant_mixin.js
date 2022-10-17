odoo.define('simtec-lead-times.VariantMixin', function (require) {
    'use strict';
    
    var LeadTimesVariantMixin = require('sale.VariantMixin');
    var publicWidget = require('web.public.widget');
    require('website_sale.website_sale');
    
    publicWidget.registry.WebsiteSale.include({
        /**
         * Adds the stock checking to the regular _onChangeCombination method
         * @override
         */
        _onChangeCombination: function () {
            this._super.apply(this, arguments);
            var arglen = arguments.length
            if (arglen > 2) {
                var combination = arguments[2]
                var estimated_arrival = combination.estimated_arrival
                var lead_times = document.getElementById("lead_times");
                lead_times.innerHTML = "Estimated arrival: " + estimated_arrival
            }
        },
    });
    
    return LeadTimesVariantMixin;
    
    });
    