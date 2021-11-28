# coding: utf-8
from hashlib import sha512
import logging
import pprint

from werkzeug import urls

from odoo import api, fields, models, _
from odoo.addons.payment.models.payment_acquirer import ValidationError
from odoo.tools.float_utils import float_compare, float_repr, float_round
from odoo.http import request

_logger = logging.getLogger(__name__)


# def normalize_keys_upper(data):
#     """Set all keys of a dictionnary to uppercase

#     Buckaroo parameters names are case insensitive
#     convert everything to upper case to be able to easily detected the presence
#     of a parameter by checking the uppercase key only
#     """
#     return {key.upper(): val for key, val in data.items()}


class AzulPaymentAcquirer(models.Model):
    _inherit = 'payment.acquirer'

    provider = fields.Selection(selection_add=[('azul', 'Azul')])
    azul_merchant_id = fields.Char(
        'MerchantId', required_if_provider='azul', groups='base.group_user')
    azul_merchant_type = fields.Char(
        'MerchantType', required_if_provider='azul', groups='base.group_user')
    azul_auth_key = fields.Char(
        'AuthKey', required_if_provider='azul', groups='base.group_user')

    _azul_auth_hash_fields = {
        'out': ['OrderNumber', 'Amount', 'AuthorizationCode', 'DateTime', 'ResponseCode' 'ISOCode', 'ResponseMessage', 'ErrorDescription', 'RRN'],
        'in': ['Azul_MerchantId', 'Azul_MerchantName', 'Azul_MerchantType', 'Azul_CurrencyCode', 'Azul_OrderNumber', 'Azul_Amount', 'Azul_ITBIS', 'Azul_ApprovedUrl', 'Azul_DeclinedUrl', 'Azul_CancelUrl', 'Azul_UseCustomField1', 'Azul_CustomField1Label', 'Azul_CustomField1Value', 'Azul_UseCustomField2', 'Azul_CustomField2Label', 'Azul_CustomField2Value']
    }

    _approved_url = '/payment/azul/approved'
    _cancel_url = '/payment/azul/cancel'
    _declined_url = '/payment/azul/declined'

    def _get_azul_urls(self, environment):
        """ Azul URLs
        """
        if environment == 'prod':
            return 'https://pagos.azul.com.do/paymentpage/Default.aspx'
        else:
            return 'https://pruebas.azul.com.do/paymentpage/Default.aspx'

    def _azul_generate_digital_sign(self, inout, values):
        """ Generate the shasign for incoming or outgoing communications.

        :param browse acquirer: the payment.acquirer browse record. It should
                                have a shakey in shaky out
        :param string inout: 'in' (odoo contacting azul) or 'out' (azul
                             contacting odoo).
        :param dict values: transaction values

        :return string: shasign
        """
        assert inout in ('in', 'out')
        assert self.provider == 'azul'

        keys = self._azul_auth_hash_fields[inout]
        values_dict = dict(values or {})

        _logger.info('_azul_generate_digital_sign: values=%s, inout=%s, keys=%s',
                     pprint.pformat(values_dict), inout, keys)

        sign = ''.join([values_dict.get(key, '') for key in keys])
        # Add the pre-shared secret key at the end of the signature
        sign = sign + self.azul_auth_key
        _logger.info('_azul_generate_digital_sign: values=%s, inout=%s, keys=%s, sign=%s',
                     pprint.pformat(values_dict), inout, keys, sign)
        return sha512(sign.encode('utf-16le')).hexdigest()

    @api.multi
    def azul_form_generate_values(self, values):
        base_url = self.get_base_url()
        azul_tx_values = dict(values)
        azul_tx_values.update({
            'Azul_MerchantId': self.azul_merchant_id,
            'Azul_MerchantName': self.company_id.name,
            'Azul_MerchantType': self.azul_merchant_type,
            'Azul_CurrencyCode': '$',
            # 'Azul_CurrencyCode': values['currency'] and values['currency'].name or '$',
            'Azul_OrderNumber': values['reference'],
            'Azul_Amount': float_repr(float_round(values['amount'], 2) * 100, 0),
            'Azul_ITBIS': float_repr(float_round(values['amount'] - (values['amount']/1.18), 2) * 100, 0),
            'Azul_ApprovedUrl': urls.url_join(base_url, self._approved_url) + "?return_url=%s" % (azul_tx_values.get('return_url', '/')),
            'Azul_CancelUrl': urls.url_join(base_url, self._cancel_url) + "?return_url=%s" % (azul_tx_values.get('return_url', '/')),
            'Azul_DeclinedUrl': urls.url_join(base_url, self._declined_url) + "?return_url=%s" % (azul_tx_values.get('return_url', '/')),

            'Azul_UseCustomField1': '0',
            'Azul_CustomField1Label': '',
            'Azul_CustomField1Value': '',

            'Azul_UseCustomField2': '0',
            'Azul_CustomField2Label': '',
            'Azul_CustomField2Value': '',
            # 'Brq_culture': (values.get('partner_lang') or 'en_US').replace('_', '-'),
        })
        azul_tx_values['Azul_AuthHash'] = self._azul_generate_digital_sign(
            'in', azul_tx_values)

        _logger.info('azul_form_generate_values: values=%s',
                     pprint.pformat(azul_tx_values))
        return azul_tx_values

    @api.multi
    def azul_get_form_action_url(self):
        """ Azul URLs
        """
        if self.environment == 'prod':
            return 'https://pagos.azul.com.do/paymentpage/Default.aspx'
        else:
            return 'https://pruebas.azul.com.do/paymentpage/Default.aspx'

    def get_base_url(self):
        return request and request.httprequest.url_root or self.env['ir.config_parameter'].sudo().get_param('web.base.url')


class AzulPaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    # azul status
    # _azul_valid_tx_status = [190]
    # _azu_pending_tx_status = [790, 791, 792, 793]
    # _azu_cancel_tx_status = [890, 891]
    # _azu_error_tx_status = [490, 491, 492]
    # _azu_reject_tx_status = [690]

    # --------------------------------------------------
    # FORM RELATED METHODS
    # --------------------------------------------------

    @api.model
    def _azul_form_get_tx_from_data(self, data):
        """ Given a data dict coming from buckaroo, verify it and find the related
        transaction record. """
        _logger.info('_azul_form_get_tx_from_data: data=%s',
                     pprint.pformat(data))
        origin_data = dict(data)
        reference, shasign = origin_data.get(
            'OrderNumber'), origin_data.get('AuthHash')
        if not reference or not shasign:
            error_msg = _('Azul: received data with missing reference (%s) or shasign (%s)') % (
                reference, shasign)
            _logger.info(error_msg)
            raise ValidationError(error_msg)

        tx = self.search([('reference', '=', reference)])
        if not tx or len(tx) > 1:
            error_msg = _(
                'Azul: received data for reference %s') % (reference)
            if not tx:
                error_msg += _('; no order found')
            else:
                error_msg += _('; multiple order found')
            _logger.info(error_msg)
            raise ValidationError(error_msg)

        # verify shasign
        shasign_check = tx.acquirer_id._azul_generate_digital_sign(
            'out', origin_data)
        if shasign_check.upper() != shasign.upper():
            error_msg = _('Azul: invalid shasign, received %s, computed %s, for data %s') % (
                shasign, shasign_check, data)
            _logger.info(error_msg)
            raise ValidationError(error_msg)

        return tx

    def _azul_form_get_invalid_parameters(self, data):
        _logger.info('_azul_form_get_invalid_parameters: data=%s',
                     pprint.pformat(data))
        invalid_parameters = []
        if self.acquirer_reference and data.get('RRN') != self.acquirer_reference:
            invalid_parameters.append(
                ('Transaction Id', data.get('RRN'), self.acquirer_reference))
        # check what is buyed
        amount = float_repr(float_round(self.amount, 2) * 100, 0)
        if data.get('Amount') != amount:
            invalid_parameters.append(
                ('Amount', data.get('Amount'), float_repr(float_round(self.amount, 2) * 100, 0)))

        return invalid_parameters

    def _azul_form_validate(self, data):
        _logger.info('_azul_form_validate: data=%s', pprint.pformat(data))
        data = dict(data)
        status_code = data.get('ResponseMessage', '').upper()
        if status_code == 'APROBADA':
            self.write({
                'state': 'done',
                'acquirer_reference': data.get('RRN'),
            })
            return True
        # elif status_code in self._azul_pending_tx_status:
        #     self.write({
        #         'state': 'pending',
        #         'acquirer_reference': data.get('RRN'),
        #     })
        #     return True
        elif status_code == 'CANCELADA':
            self.write({
                'state': 'cancel',
                'acquirer_reference': data.get('RRN'),
            })
            return True
        else:
            error = data.get('ErrorDescription', 'Azul: feedback error')
            _logger.info(error)
            self.write({
                'state': 'error',
                'state_message': error,
                'acquirer_reference': data.get('RRN'),
            })
            return False
