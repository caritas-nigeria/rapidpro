from __future__ import unicode_literals, absolute_import

import phonenumbers
from django import forms
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView

from temba.channels.models import Channel
from temba.channels.views import BaseClaimNumberMixin, ClaimViewMixin, NEXMO_SUPPORTED_COUNTRIES, \
    NEXMO_SUPPORTED_COUNTRY_CODES
from temba.orgs.models import Org, NEXMO_UUID, NEXMO_APP_ID
from temba.utils import analytics
from temba.utils.models import generate_uuid


class ClaimView(BaseClaimNumberMixin, SmartFormView):

    class Form(ClaimViewMixin.Form):
        country = forms.ChoiceField(choices=NEXMO_SUPPORTED_COUNTRIES)
        phone_number = forms.CharField(help_text=_("The phone number being added"))

        def clean_phone_number(self):
            if not self.cleaned_data.get('country', None):  # pragma: needs cover
                    raise ValidationError(_("That number is not currently supported."))

            phone = self.cleaned_data['phone_number']
            phone = phonenumbers.parse(phone, self.cleaned_data['country'])

            return phonenumbers.format_number(phone, phonenumbers.PhoneNumberFormat.E164)

    form_class = Form

    def pre_process(self, *args, **kwargs):
        org = Org.objects.get(pk=self.request.user.get_org().pk)
        try:
            client = org.get_nexmo_client()
        except Exception:  # pragma: needs cover
            client = None

        if client:
            return None
        else:  # pragma: needs cover
            return HttpResponseRedirect(reverse('channels.channel_claim'))

    def is_valid_country(self, country_code):
        return country_code in NEXMO_SUPPORTED_COUNTRY_CODES

    def is_messaging_country(self, country):
        return country in [c[0] for c in NEXMO_SUPPORTED_COUNTRIES]

    def get_search_url(self):
        return reverse('channels.channel_search_nexmo')

    def get_claim_url(self):
        return reverse('channels.claim_nexmo')

    def get_supported_countries_tuple(self):
        return NEXMO_SUPPORTED_COUNTRIES

    def get_search_countries_tuple(self):
        return NEXMO_SUPPORTED_COUNTRIES

    def get_existing_numbers(self, org):
        client = org.get_nexmo_client()
        if client:
            account_numbers = client.get_numbers(size=100)

        numbers = []
        for number in account_numbers:
            if number['type'] == 'mobile-shortcode':  # pragma: needs cover
                phone_number = number['msisdn']
            else:
                parsed = phonenumbers.parse(number['msisdn'], number['country'])
                phone_number = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
            numbers.append(dict(number=phone_number, country=number['country']))

        return numbers

    def claim_number(self, user, phone_number, country, role):
        org = user.get_org()

        client = org.get_nexmo_client()
        org_config = org.config_json()
        org_uuid = org_config.get(NEXMO_UUID)
        app_id = org_config.get(NEXMO_APP_ID)

        nexmo_phones = client.get_numbers(phone_number)
        is_shortcode = False

        # try it with just the national code (for short codes)
        if not nexmo_phones:
            parsed = phonenumbers.parse(phone_number, None)
            shortcode = str(parsed.national_number)

            nexmo_phones = client.get_numbers(shortcode)

            if nexmo_phones:
                is_shortcode = True
                phone_number = shortcode

        # buy the number if we have to
        if not nexmo_phones:
            try:
                client.buy_nexmo_number(country, phone_number)
            except Exception as e:
                raise Exception(_("There was a problem claiming that number, "
                                  "please check the balance on your account. " +
                                  "Note that you can only claim numbers after "
                                  "adding credit to your Nexmo account.") + "\n" + str(e))

        mo_path = reverse('handlers.nexmo_handler', args=['receive', org_uuid])

        channel_uuid = generate_uuid()

        nexmo_phones = client.get_numbers(phone_number)

        features = [elt.upper() for elt in nexmo_phones[0]['features']]
        role = ''
        if 'SMS' in features:
            role += Channel.ROLE_SEND + Channel.ROLE_RECEIVE

        if 'VOICE' in features:
            role += Channel.ROLE_ANSWER + Channel.ROLE_CALL

        # update the delivery URLs for it
        from temba.settings import TEMBA_HOST
        try:
            client.update_nexmo_number(country, phone_number, 'https://%s%s' % (TEMBA_HOST, mo_path), app_id)

        except Exception as e:  # pragma: no cover
            # shortcodes don't seem to claim right on nexmo, move forward anyways
            if not is_shortcode:
                raise Exception(_("There was a problem claiming that number, please check the balance on your account.") +
                                "\n" + str(e))

        if is_shortcode:
            phone = phone_number
            nexmo_phone_number = phone_number
        else:
            parsed = phonenumbers.parse(phone_number, None)
            phone = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)

            # nexmo ships numbers around as E164 without the leading +
            nexmo_phone_number = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164).strip('+')

        channel = Channel.create(org, user, country, 'NX', name=phone, address=phone_number, role=role,
                                 bod=nexmo_phone_number, uuid=channel_uuid)

        analytics.track(user.username, 'temba.channel_claim_nexmo', dict(number=phone_number))

        return channel